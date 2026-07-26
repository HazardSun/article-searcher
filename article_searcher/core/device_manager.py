"""
统一硬件设备管理器
负责探测 CPU / CUDA GPU / DirectML GPU / NPU，并基于可配置优先级推荐最优设备，
同时为 ONNX Runtime 生成正确的执行提供方（provider）列表，实现 CPU/GPU/NPU 协同。
"""

import os
import logging
import subprocess
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

try:
    import psutil
except ImportError:  # pragma: no cover - psutil is a hard dependency but guard anyway
    psutil = None

logger = logging.getLogger(__name__)

# 默认设备优先级：GPU(DirectML/CUDA) -> CPU
# 说明：本机 onnxruntime 仅提供 DmlExecutionProvider / CPUExecutionProvider，
# 无 NpuExecutionProvider；且 torch 在本机导入会原生段错误（无法被 Python 捕获），
# 故移除 NPU 探测与一切 torch 依赖，设备探测改用 onnxruntime provider + Windows WMI。
DEFAULT_PRIORITY = "gpu,cpu"


@dataclass
class DeviceInfo:
    """一个可被模型使用的计算设备"""
    key: str           # 稳定唯一键，如 'cpu' / 'cuda:0' / 'dml:1' / 'npu:1'
    name: str          # 展示名称
    kind: str          # 'cpu' | 'cuda' | 'dml' | 'npu'
    index: int = 0     # 设备索引（用于 ONNX provider 的 device_id）
    detail: str = ""   # 附加说明

    @property
    def label(self) -> str:
        if self.kind == "cpu":
            return "CPU"
        if self.kind == "npu":
            return f"NPU ({self.name})"
        if self.kind == "cuda":
            return f"GPU · CUDA ({self.name})"
        if self.kind == "dml":
            return f"GPU · DirectML ({self.name})"
        return self.name


class DeviceManager:
    """探测并管理本地计算设备"""

    def __init__(self, priority: str = DEFAULT_PRIORITY):
        self.priority = priority
        self.devices: List[DeviceInfo] = self._detect()
        self._onnx_providers_cache: Dict[str, List[Any]] = {}

    # ------------------------------------------------------------------ #
    # 探测
    # ------------------------------------------------------------------ #
    def _detect(self) -> List[DeviceInfo]:
        devices: List[DeviceInfo] = [DeviceInfo("cpu", "CPU", "cpu")]
        cuda = self._detect_cuda()
        dml = self._detect_dml()

        devices.extend(cuda)
        devices.extend(dml)
        logger.info(
            "Detected devices: %s",
            ", ".join(f"{d.key}({d.kind})" for d in devices) or "none",
        )
        return devices

    @staticmethod
    def _detect_cuda() -> List[DeviceInfo]:
        """通过 onnxruntime 的 CUDA provider 探测 NVIDIA GPU（不依赖 torch）。"""
        out: List[DeviceInfo] = []
        try:
            if "CUDAExecutionProvider" in DeviceManager.available_onnx_providers():
                names = DeviceManager._video_controller_names()
                name = next((n for n in names if "nvidia" in n.lower()), "NVIDIA GPU")
                out.append(DeviceInfo("cuda:0", name, "cuda", 0))
                logger.info("CUDA device: %s", name)
        except Exception as e:
            logger.debug("CUDA detection skipped: %s", e)
        return out

    @staticmethod
    def _detect_dml() -> List[DeviceInfo]:
        """通过 onnxruntime 的 DmlExecutionProvider 探测 DirectML GPU（不依赖 torch）。"""
        out: List[DeviceInfo] = []
        try:
            if "DmlExecutionProvider" in DeviceManager.available_onnx_providers():
                names = DeviceManager._video_controller_names()
                name = next(
                    (n for n in names if any(
                        k in n.lower() for k in
                        ("amd", "radeon", "intel", "arc", "qualcomm",
                         "adreno", "nvidia", "apple"))),
                    names[0] if names else "DirectML GPU",
                )
                out.append(DeviceInfo("dml:0", name or "DirectML GPU", "dml", 0))
                logger.info("DirectML device: %s", name)
        except Exception as e:
            logger.debug("DirectML detection failed: %s", e)
        return out

    @staticmethod
    def _video_controller_names() -> List[str]:
        """通过 Windows WMI 获取显示控制器名称（替代 torch_directml，避免原生崩溃）。"""
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_VideoController | "
                 "Select-Object -ExpandProperty Name"],
                capture_output=True, text=True, timeout=10,
            )
            return [l.strip() for l in r.stdout.splitlines() if l.strip()]
        except Exception:
            return []

    # ------------------------------------------------------------------ #
    # 推荐与查询
    # ------------------------------------------------------------------ #
    def recommended(self, priority: Optional[str] = None) -> DeviceInfo:
        """根据优先级返回最优可用设备"""
        order = (priority or self.priority).split(",")
        for pref in order:
            pref = pref.strip().lower()
            if pref == "npu":
                cands = [d for d in self.devices if d.kind == "npu"]
            elif pref == "gpu":
                cands = [d for d in self.devices if d.kind in ("cuda", "dml")]
            elif pref == "cpu":
                cands = [d for d in self.devices if d.kind == "cpu"]
            elif pref == "cuda":
                cands = [d for d in self.devices if d.kind == "cuda"]
            elif pref == "dml":
                cands = [d for d in self.devices if d.kind == "dml"]
            else:  # 具体 key，如 'cuda:0'
                cands = [d for d in self.devices if d.key == pref]
            if cands:
                return cands[0]
        return self.devices[0]

    def get(self, key: str) -> Optional[DeviceInfo]:
        return next((d for d in self.devices if d.key == key), None)

    def keys(self) -> List[str]:
        return [d.key for d in self.devices]

    # ------------------------------------------------------------------ #
    # ONNX Runtime provider 列表
    # ------------------------------------------------------------------ #
    @staticmethod
    def available_onnx_providers() -> set:
        """返回当前 onnxruntime 实际支持的执行提供方"""
        try:
            import onnxruntime as ort
            return set(ort.get_available_providers())
        except Exception:
            return set()

    def onnx_providers(self, device: DeviceInfo,
                        allow_cpu_fallback: bool = True) -> List[Any]:
        """
        为指定设备生成 ONNX Runtime provider 列表（按优先级排列）。
        ONNX Runtime 会自动跳过当前构建不支持的 provider，因此多列无害。
        """
        available = self.available_onnx_providers()
        provs: List[Any] = []

        if device.kind == "cuda":
            if "CUDAExecutionProvider" in available:
                provs.append(("CUDAExecutionProvider", {"device_id": device.index}))
            else:
                logger.warning("CUDAExecutionProvider 不可用，回退至 CPU")
        elif device.kind in ("dml", "npu"):
            if "DmlExecutionProvider" in available:
                opts = {"device_id": device.index}
                # 限制 DML 显存占用，避免核显（共享系统内存）一次性吃满导致整机卡死
                limit_gb = self._dml_mem_limit_gb()
                if limit_gb:
                    opts["gpu_mem_limit"] = int(limit_gb * 1024 ** 3)
                provs.append(("DmlExecutionProvider", opts))
            else:
                logger.warning("DmlExecutionProvider 不可用，回退至 CPU")
        elif device.kind == "cpu":
            # Intel 平台优先用 OpenVINO 加速 CPU/iGPU 推理
            if "OpenVINOExecutionProvider" in available:
                provs.append(("OpenVINOExecutionProvider", {}))

        if allow_cpu_fallback:
            provs.append("CPUExecutionProvider")
        return provs

    def _dml_mem_limit_gb(self) -> float:
        """为 DML/NPU 计算显存上限（GiB）。核显共享系统内存，限制避免整机卡死。"""
        try:
            if psutil:
                total_gb = (psutil.virtual_memory().total / (1024 ** 3)) or 0
                return round(min(max(total_gb * 0.25, 0.5), 2.0), 2)
        except Exception:
            pass
        return 1.5

    def build_session_options(self) -> "Any":
        """构造稳定优先的 ONNX SessionOptions。

        稳定性修复（关键）：建索引时（单文件 6000+ chunks）在 onnxruntime 多线程
        模式下偶发 C++ 层崩溃，根因为 CPU 执行提供方内部线程池竞态（非确定性，
        同输入不同运行时而崩溃时而正常）。彻底关闭并行执行，并将 intra/inter-op
        线程数均置 1，使推理退化为单线程、不再有任何线程池争用，从根上消除崩溃。
        代价是 CPU 路径吞吐下降，但换取「建索引不再崩溃」这一硬约束。
        """
        try:
            import onnxruntime as ort
        except Exception:
            return None

        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        # 关闭并行执行，消除 inter-op 线程池竞态（崩溃根因）
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        # 单线程推理：彻底消除 onnxruntime CPU 内部线程池争用，根绝偶发崩溃
        so.intra_op_num_threads = 1
        so.inter_op_num_threads = 1
        # 关闭自旋等待，进一步降低线程争用
        try:
            so.add_session_config_entry("session.intra_op.allow_spinning", "0")
            so.add_session_config_entry("session.inter_op.allow_spinning", "0")
        except Exception:
            pass
        so.enable_cpu_mem_arena = True
        so.log_severity_level = 3
        return so

    # ------------------------------------------------------------------ #
    # 序列化（供 UI / 状态展示使用）
    # ------------------------------------------------------------------ #
    def to_status(self) -> Dict[str, Any]:
        return {
            "devices": [
                {"key": d.key, "name": d.name, "kind": d.kind, "index": d.index,
                 "label": d.label}
                for d in self.devices
            ],
            "available_keys": self.keys(),
            "priority": self.priority,
            "recommended": self.recommended().key,
            "cpu_cores": (psutil.cpu_count(logical=True) if psutil else None),
            "cpu_physical": (psutil.cpu_count(logical=False) if psutil else None),
            "total_memory_gb": (
                round(psutil.virtual_memory().total / (1024 ** 3), 1) if psutil else None
            ),
        }
