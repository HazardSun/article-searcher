"""ONNX Runtime 推理引擎 - 支持 NPU (DirectML)"""

import os
import json
import logging
import numpy as np
from typing import List
from pathlib import Path

logger = logging.getLogger(__name__)


def _find_hf_cache_path(model_name: str, cache_dir: str) -> str:
    """从 sentence-transformers 缓存中找到模型快照路径"""
    safe = model_name.replace('/', '--')
    base = os.path.join(cache_dir, 'models--' + safe, 'snapshots')
    if not os.path.isdir(base):
        return ''
    snaps = os.listdir(base)
    if not snaps:
        return ''
    return os.path.join(base, snaps[0])


class OnnxEmbeddingEngine:
    """基于 ONNX Runtime + DirectML 的 Embedding 引擎"""

    def __init__(self, model_name: str, cache_dir: str, device_id: int = 0):
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.device_id = device_id
        self._session = None
        self._tokenizer = None
        self._model_dir = None
        self._dimension = 0

    def _ensure_exported(self) -> str:
        safe_name = self.model_name.replace('/', '--')
        onnx_dir = os.path.join(self.cache_dir, safe_name, 'onnx')
        onnx_path = os.path.join(onnx_dir, 'model.onnx')
        config_path = os.path.join(onnx_dir, 'config.json')

        if os.path.isfile(onnx_path) and os.path.isfile(config_path):
            self._model_dir = onnx_dir
            return onnx_path

        logger.info(f"Exporting model to ONNX: {self.model_name}")
        os.makedirs(onnx_dir, exist_ok=True)
        self._export_model(onnx_dir)
        logger.info(f"ONNX model exported to: {onnx_dir}")
        self._model_dir = onnx_dir
        return onnx_path

    def _export_model(self, onnx_dir: str):
        from transformers import AutoTokenizer, AutoConfig
        from transformers.onnx import FeaturesManager

        snapshot = _find_hf_cache_path(self.model_name, self.cache_dir)
        if not snapshot:
            raise FileNotFoundError(f"Model {self.model_name} not found in cache: {self.cache_dir}")

        tokenizer = AutoTokenizer.from_pretrained(snapshot, use_fast=True, local_files_only=True)
        tokenizer.save_pretrained(onnx_dir)

        config = AutoConfig.from_pretrained(snapshot, local_files_only=True)
        model_class = FeaturesManager.get_model_class_for_feature('default', 'pt')
        model = model_class.from_pretrained(snapshot, config=config, local_files_only=True)
        model.eval()

        onnx_config = FeaturesManager.get_config(config.model_type, 'default')(config)

        from transformers.onnx import export as onnx_export
        onnx_export(
            preprocessor=tokenizer,
            model=model,
            config=onnx_config,
            opset=14,
            output=Path(os.path.join(onnx_dir, 'model.onnx')),
        )

        model_info = {
            'model_name': self.model_name,
            'feature': 'default',
            'pooling_mode': 'mean',
            'normalize': True,
            'dimension': model.config.hidden_size,
        }
        with open(os.path.join(onnx_dir, 'config.json'), 'w', encoding='utf-8') as f:
            json.dump(model_info, f, indent=2)

    def _load_session(self, onnx_path: str):
        import onnxruntime
        providers = [
            ('DmlExecutionProvider', {'device_id': self.device_id}),
            'CPUExecutionProvider',
        ]
        try:
            so = onnxruntime.SessionOptions()
            so.log_severity_level = 3
            so.enable_cpu_mem_arena = False
            self._session = onnxruntime.InferenceSession(
                onnx_path, sess_options=so, providers=providers
            )
            actual = self._session.get_providers()[0]
            if 'DML' not in actual:
                logger.warning(f"NPU DirectML not available, using: {actual}")
            logger.info(f"ONNX Runtime loaded: {actual}")
        except Exception as e:
            logger.warning(f"DML device_id={self.device_id} failed: {e}. Trying GPU (device_id=0).")
            providers = [
                ('DmlExecutionProvider', {'device_id': 0}),
                'CPUExecutionProvider',
            ]
            so = onnxruntime.SessionOptions()
            so.log_severity_level = 3
            self._session = onnxruntime.InferenceSession(
                onnx_path, sess_options=so, providers=providers
            )
            logger.info(f"ONNX Runtime loaded on GPU: {self._session.get_providers()[0]}")

    def _load_tokenizer_and_config(self):
        from transformers import AutoTokenizer
        self._tokenizer = AutoTokenizer.from_pretrained(self._model_dir, use_fast=True)
        config_path = os.path.join(self._model_dir, 'config.json')
        if os.path.isfile(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            self._dimension = cfg.get('dimension', 512)

    def _mean_pooling(self, token_embeddings: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
        mask = np.expand_dims(attention_mask.astype(np.float32), axis=-1)
        summed = (token_embeddings * mask).sum(axis=1)
        counts = np.maximum(mask.sum(axis=1), 1e-9)
        return summed / counts

    @staticmethod
    def _l2_normalize(embeddings: np.ndarray) -> np.ndarray:
        return embeddings / np.maximum(np.linalg.norm(embeddings, axis=-1, keepdims=True), 1e-12)

    def ensure_loaded(self):
        if self._session is not None:
            return
        onnx_path = self._ensure_exported()
        self._load_session(onnx_path)
        self._load_tokenizer_and_config()

    def encode(self, texts: List[str], batch_size: int = 32,
               normalize_embeddings: bool = True) -> np.ndarray:
        if not texts:
            return np.array([])
        self.ensure_loaded()

        all_emb = []
        for i in range(0, len(texts), batch_size):
            batch = [t if t.strip() else ' ' for t in texts[i:i + batch_size]]
            inputs = self._tokenizer(
                batch, padding=True, truncation=True, max_length=512, return_tensors='np',
            )
            ort_in = {
                'input_ids': inputs['input_ids'],
                'attention_mask': inputs['attention_mask'],
                'token_type_ids': inputs.get('token_type_ids', np.zeros_like(inputs['input_ids'])),
            }
            outputs = self._session.run(None, ort_in)
            pooled = self._mean_pooling(outputs[0], inputs['attention_mask'])
            if normalize_embeddings:
                pooled = self._l2_normalize(pooled)
            all_emb.append(pooled)
        return np.concatenate(all_emb, axis=0)

    def encode_single(self, text: str) -> np.ndarray:
        return self.encode([text])[0]

    @property
    def dimension(self) -> int:
        return self._dimension or 512
