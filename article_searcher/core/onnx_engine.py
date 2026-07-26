"""
ONNX Runtime 推理引擎
统一在 CPU / CUDA / DirectML(GPU 或 NPU) / OpenVINO 上运行 Embedding 模型，
自动选择第一个可用的执行提供方，并记录最终实际使用的设备。
"""

import os
import json
import logging
import threading
from typing import List, Optional

import numpy as np
from pathlib import Path

from .device_manager import DeviceInfo, DeviceManager

logger = logging.getLogger(__name__)


def _find_hf_cache_path(model_name: str, cache_dir: str) -> str:
    """从 sentence-transformers 缓存中找到模型快照路径"""
    safe = model_name.replace("/", "--")
    base = os.path.join(cache_dir, "models--" + safe, "snapshots")
    if not os.path.isdir(base):
        return ""
    snaps = os.listdir(base)
    if not snaps:
        return ""
    return os.path.join(base, snaps[0])


class OnnxEmbeddingEngine:
    """基于 ONNX Runtime 的 Embedding 引擎，支持多硬件后端"""

    def __init__(
        self,
        model_name: str,
        cache_dir: str,
        device: Optional[DeviceInfo] = None,
        device_manager: Optional[DeviceManager] = None,
        batch_size: int = 32,
        max_workers: int = 2,
    ):
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.device = device or DeviceInfo("cpu", "CPU", "cpu")
        self.device_manager = device_manager or DeviceManager()
        self.batch_size = batch_size
        self.max_workers = max(1, max_workers)

        self._session = None
        self._tokenizer = None
        self._model_dir = None
        self._dimension = 0
        self._actual_provider = ""
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # 模型导出 (PyTorch -> ONNX)
    # ------------------------------------------------------------------ #
    def _ensure_exported(self) -> str:
        safe_name = self.model_name.replace("/", "--")
        onnx_dir = os.path.join(self.cache_dir, safe_name, "onnx")
        onnx_path = os.path.join(onnx_dir, "model.onnx")
        config_path = os.path.join(onnx_dir, "config.json")

        if os.path.isfile(onnx_path) and os.path.isfile(config_path):
            self._model_dir = onnx_dir
            return onnx_path

        logger.info("导出模型为 ONNX: %s", self.model_name)
        os.makedirs(onnx_dir, exist_ok=True)
        self._export_model(onnx_dir)
        logger.info("ONNX 模型已导出至: %s", onnx_dir)
        self._model_dir = onnx_dir
        return onnx_path

    def _export_model(self, onnx_dir: str):
        from transformers import AutoTokenizer, AutoConfig
        from transformers.onnx import FeaturesManager, export as onnx_export

        snapshot = _find_hf_cache_path(self.model_name, self.cache_dir)
        if not snapshot:
            raise FileNotFoundError(
                f"模型 {self.model_name} 未在缓存中找到: {self.cache_dir}"
            )

        tokenizer = AutoTokenizer.from_pretrained(snapshot, use_fast=True, local_files_only=True)
        tokenizer.save_pretrained(onnx_dir)

        config = AutoConfig.from_pretrained(snapshot, local_files_only=True)
        model_class = FeaturesManager.get_model_class_for_feature("default", "pt")
        model = model_class.from_pretrained(snapshot, config=config, local_files_only=True)
        model.eval()

        onnx_config = FeaturesManager.get_config(config.model_type, "default")(config)
        onnx_export(
            preprocessor=tokenizer,
            model=model,
            config=onnx_config,
            opset=14,
            output=Path(os.path.join(onnx_dir, "model.onnx")),
        )

        model_info = {
            "model_name": self.model_name,
            "feature": "default",
            "pooling_mode": "mean",
            "normalize": True,
            "dimension": config.hidden_size,
        }
        with open(os.path.join(onnx_dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump(model_info, f, indent=2)

    # ------------------------------------------------------------------ #
    # 会话加载
    # ------------------------------------------------------------------ #
    def _build_providers(self):
        return self.device_manager.onnx_providers(self.device)

    def _load_session(self, onnx_path: str):
        import onnxruntime as ort

        so = self.device_manager.build_session_options()
        if so is None:
            so = ort.SessionOptions()
            so.log_severity_level = 3

        providers = self._build_providers()
        if not providers:
            providers = ["CPUExecutionProvider"]

        self._session = ort.InferenceSession(onnx_path, sess_options=so, providers=providers)
        actual_list = self._session.get_providers()
        self._actual_provider = actual_list[0] if actual_list else "unknown"
        logger.info(
            "ONNX 会话已加载 | 请求设备=%s | 实际 provider=%s",
            self.device.label, self._actual_provider,
        )

    def _load_tokenizer_and_config(self):
        from transformers import AutoTokenizer
        self._tokenizer = AutoTokenizer.from_pretrained(self._model_dir, use_fast=True)
        config_path = os.path.join(self._model_dir, "config.json")
        if os.path.isfile(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self._dimension = cfg.get("dimension", 512)

    def ensure_loaded(self):
        """幂等地加载模型（线程安全）"""
        if self._session is not None:
            return
        with self._lock:
            if self._session is not None:
                return
            onnx_path = self._ensure_exported()
            self._load_session(onnx_path)
            self._load_tokenizer_and_config()
            self._warmup()

    def _warmup(self):
        """预热：加载后用若干代表性输入触发一次推理，预分配 onnxruntime 内部
        工作缓冲区，规避首帧 / 变长输入下的偶发 C++ 崩溃（线程池竞态）。
        即便预热失败也不影响后续正式推理。"""
        try:
            self._run_batch(["warmup", "预热文本", "a short sentence"], True)
            self._run_batch(
                ["这是一段用于预热的较长中文文本，目的是触发 onnxruntime 为不同长度输入"
                 "分配内部工作缓冲区，降低后续批量编码时偶发崩溃的概率。"],
                True,
            )
        except Exception as e:  # pragma: no cover - 预热失败不应阻断加载
            logger.warning("ONNX 预热失败（不影响后续推理）: %s", e)

    # ------------------------------------------------------------------ #
    # 编码
    # ------------------------------------------------------------------ #
    @staticmethod
    def _mean_pooling(token_embeddings: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
        mask = np.expand_dims(attention_mask.astype(np.float32), axis=-1)
        summed = (token_embeddings * mask).sum(axis=1)
        counts = np.maximum(mask.sum(axis=1), 1e-9)
        return summed / counts

    @staticmethod
    def _l2_normalize(embeddings: np.ndarray) -> np.ndarray:
        return embeddings / np.maximum(np.linalg.norm(embeddings, axis=-1, keepdims=True), 1e-12)

    def _tokenize_batch(self, batch: List[str]):
        return self._tokenizer(
            batch, padding=True, truncation=True, max_length=512, return_tensors="np"
        )

    def encode(
        self,
        texts: List[str],
        batch_size: Optional[int] = None,
        normalize_embeddings: bool = True,
    ) -> np.ndarray:
        if not texts:
            return np.array([])
        self.ensure_loaded()

        batch_size = batch_size or self.batch_size
        all_emb = []
        batches = [texts[i:i + batch_size] for i in range(0, len(texts), batch_size)]

        for batch in batches:
            all_emb.append(self._run_batch(batch, normalize_embeddings))

        return np.concatenate(all_emb, axis=0)

    def _run_batch(self, batch: List[str], normalize_embeddings: bool) -> np.ndarray:
        """单次推理（串行执行，避免多线程共享同一 ONNX 会话引发的争用/死锁）"""
        b = [t if t.strip() else " " for t in batch]
        inputs = self._tokenize_batch(b)
        ort_in = {
            "input_ids": inputs["input_ids"],
            "attention_mask": inputs["attention_mask"],
            "token_type_ids": inputs.get(
                "token_type_ids", np.zeros_like(inputs["input_ids"])
            ),
        }
        outputs = self._session.run(None, ort_in)
        pooled = self._mean_pooling(outputs[0], inputs["attention_mask"])
        return self._l2_normalize(pooled) if normalize_embeddings else pooled

    def encode_single(self, text: str) -> np.ndarray:
        return self.encode([text])[0]

    @property
    def dimension(self) -> int:
        return self._dimension or 512

    @property
    def actual_provider(self) -> str:
        return self._actual_provider
