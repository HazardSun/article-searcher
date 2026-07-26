"""
本地 Embedding 引擎
统一调度 NPU / GPU(DirectML/CUDA) / CPU：
- 主路径：ONNX Runtime（自动选择 NPU/DML/CUDA/OpenVINO/CPU 中首个可用后端）
- 回退路径：sentence-transformers（当 ONNX 导出或加载失败时）

关键修复（性能 / 卡死）：
- 模型加载改为「懒加载」：构造时不再同步创建 ONNX/DML 会话（避免启动即在 GUI 线程
  长时间阻塞白屏）；首次 encode / 显式 preload() 时才在调用方线程加载。
- 用锁串行化 encode 与设备/模型切换，消除并发推理争用与 set_device 把引擎置空导致的
  AttributeError 竞态。
"""

import os
import logging
import threading
import numpy as np
from typing import List, Optional, Dict, Any

from .device_manager import DeviceManager, DeviceInfo
from .onnx_engine import OnnxEmbeddingEngine

logger = logging.getLogger(__name__)


class EmbeddingEngine:

    DEFAULT_MODEL = "BAAI/bge-small-zh-v1.5"
    FALLBACK_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

    def __init__(
        self,
        model_name: str = None,
        device: str = None,
        cache_dir: str = None,
        batch_size: int = 32,
        priority: str = None,
    ):
        self.model_name = model_name or self.DEFAULT_MODEL
        self.cache_dir = cache_dir or os.path.join(
            os.path.expanduser("~"), ".cache", "article_searcher", "models"
        )
        self._batch_size = batch_size

        self._device_manager = DeviceManager(priority=priority) if priority else DeviceManager()
        # 兼容旧调用方（main.py / ui）读取 hardware_info
        self.hardware_info = self._device_manager.to_status()

        self._requested = device or "auto"
        self._resolved: Optional[DeviceInfo] = None
        self._onnx_engine: Optional[OnnxEmbeddingEngine] = None
        self._st_model = None
        self._actual_device: Optional[str] = None

        # 懒加载：构造时只标记需要加载，真正创建会话推迟到首次 encode / preload()
        self._reload_needed = True
        # 必须用可重入锁 RLock：encode() 持锁后会调用 _ensure_loaded()，而后者
        # 内部也要加同一把锁；普通 Lock 不可重入，会导致同线程二次加锁死锁。
        self._lock = threading.RLock()

        os.makedirs(self.cache_dir, exist_ok=True)

    # ------------------------------------------------------------------ #
    # 设备解析与加载
    # ------------------------------------------------------------------ #
    def _resolve_device(self, requested: str) -> DeviceInfo:
        if requested in (None, "auto", "recommended"):
            return self._device_manager.recommended()
        dev = self._device_manager.get(requested)
        return dev or self._device_manager.recommended()

    def _ensure_loaded(self):
        """在锁内保证会话已就绪（懒加载核心）。"""
        with self._lock:
            if self._reload_needed or (self._onnx_engine is None and self._st_model is None):
                self._resolve_and_load()
                self._reload_needed = False

    def preload(self):
        """显式触发一次加载（应在后台线程调用，避免阻塞 GUI）。"""
        self._ensure_loaded()

    def set_device(self, device: str):
        """切换运行设备；标记下次编码时重建会话（不再同步阻塞）。"""
        with self._lock:
            self._requested = device
            self._onnx_engine = None
            self._st_model = None
            self._resolved = None
            self._actual_device = None
            self._reload_needed = True
        self.hardware_info = self._device_manager.to_status()

    def set_model(self, model_name: str):
        """切换 Embedding 模型；标记下次编码时重建会话（不再同步阻塞）。"""
        with self._lock:
            self.model_name = model_name
            self._onnx_engine = None
            self._st_model = None
            self._resolved = None
            self._actual_device = None
            self._reload_needed = True
        self.hardware_info = self._device_manager.to_status()

    def _resolve_and_load(self):
        self._resolved = self._resolve_device(self._requested)
        self._load()

    def _load(self):
        dev = self._resolved
        # 主路径：ONNX（统一多硬件后端，基于 onnxruntime DML/CPU，不依赖 torch）
        try:
            self._onnx_engine = OnnxEmbeddingEngine(
                self.model_name, self.cache_dir, dev,
                self._device_manager, batch_size=self._batch_size,
            )
            self._onnx_engine.ensure_loaded()
            self._actual_device = f"{dev.label} [{self._onnx_engine.actual_provider}]"
            self._st_model = None
            logger.info("Embedding 使用 ONNX 后端: %s", self._actual_device)
            return
        except Exception as e:
            # 不再回退 sentence-transformers：该路径依赖 torch，而本机 torch 导入会
            # 原生段错误（无法被 Python 捕获），回退只会让进程崩溃。直接抛出明确错误。
            raise RuntimeError(
                f"ONNX Embedding 引擎加载失败: {e}。已禁用 sentence-transformers 回退"
                f"（依赖 torch，本机不可用）。"
            ) from e

    # ------------------------------------------------------------------ #
    # 编码（线程安全：串行化所有推理与切换）
    # ------------------------------------------------------------------ #
    def encode(
        self,
        texts: List[str],
        batch_size: int = None,
        show_progress: bool = False,
    ) -> np.ndarray:
        if not texts:
            return np.array([])
        filtered = [t if t.strip() else " " for t in texts]

        # 持锁期间完成「确保加载 + 推理」，串行化避免并发会话争用
        with self._lock:
            self._ensure_loaded()
            if self._onnx_engine is not None:
                return self._onnx_engine.encode(
                    filtered, batch_size=batch_size or self._batch_size
                )
            if self._st_model is not None:
                return self._st_model.encode(
                    filtered,
                    batch_size=batch_size or self._batch_size,
                    show_progress_bar=show_progress,
                    normalize_embeddings=True,
                    convert_to_numpy=True,
                )
        raise RuntimeError("Embedding 引擎未能加载（ONNX 与 sentence-transformers 均失败）")

    def encode_single(self, text: str) -> np.ndarray:
        return self.encode([text])[0]

    # ------------------------------------------------------------------ #
    # 属性 / 状态
    # ------------------------------------------------------------------ #
    @property
    def device(self) -> str:
        return self._requested

    @property
    def actual_device(self) -> str:
        return self._actual_device or (self._resolved.label if self._resolved else "cpu")

    @property
    def backend(self) -> str:
        return "onnx" if self._onnx_engine is not None else "sentence-transformers"

    @property
    def is_loaded(self) -> bool:
        return self._onnx_engine is not None or self._st_model is not None

    @property
    def dimension(self) -> int:
        # 懒加载下，未加载时 _onnx_engine 为空会返回 0，导致 set_model 误判维度。
        # 在锁内触发加载后再读，保证维度始终准确（调用方为后台线程，不会阻塞 GUI）。
        with self._lock:
            self._ensure_loaded()
            if self._onnx_engine is not None:
                return self._onnx_engine.dimension
            if self._st_model is not None:
                try:
                    return self._st_model.get_embedding_dimension()
                except AttributeError:
                    return self._st_model.get_sentence_embedding_dimension()
        return 0

    @property
    def available_devices(self) -> List[str]:
        return self._device_manager.keys()

    @property
    def devices(self) -> List[DeviceInfo]:
        return self._device_manager.devices

    @property
    def recommended_device(self) -> str:
        return self._device_manager.recommended().key

    def get_status_info(self) -> Dict[str, Any]:
        # 注意：这里用「已加载才读」的安全方式取维度，避免状态刷新（GUI 线程）
        # 触发模型加载导致白屏。维度精确值在首次编码后自然就绪。
        if self._onnx_engine is not None:
            dim = self._onnx_engine.dimension
        elif self._st_model is not None:
            try:
                dim = self._st_model.get_embedding_dimension()
            except AttributeError:
                dim = self._st_model.get_sentence_embedding_dimension()
        else:
            dim = 0
        return {
            "model": self.model_name,
            "device": self._requested,
            "actual_device": self.actual_device,
            "backend": self.backend,
            "dimension": dim,
            "hardware": self.hardware_info,
            "available_devices": self.available_devices,
            "recommended_device": self.recommended_device,
        }
