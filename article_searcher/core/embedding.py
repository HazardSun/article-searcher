import os
import logging
import numpy as np
from typing import List, Optional

import psutil

logger = logging.getLogger(__name__)


class HardwareDetector:

    @staticmethod
    def detect() -> dict:
        info = {
            'cpu_cores': psutil.cpu_count(logical=True),
            'cpu_physical': psutil.cpu_count(logical=False),
            'total_memory_gb': round(psutil.virtual_memory().total / (1024**3), 1),
            'available_memory_gb': round(psutil.virtual_memory().available / (1024**3), 1),
            'device': 'cpu',
            'acceleration': 'none',
            'gpu_name': '',
            'gpu_devices': [],
            'available_devices': ['cpu'],
        }

        cuda_devices = HardwareDetector._detect_cuda_devices()
        dml_devices = HardwareDetector._detect_directml_devices()

        all_gpus = cuda_devices + dml_devices
        info['gpu_devices'] = all_gpus

        available = ['cpu']
        for gpu in all_gpus:
            available.append(gpu['key'])
        info['available_devices'] = available

        npu = HardwareDetector._detect_npu()
        if npu:
            info['available_devices'].append('npu')

        if cuda_devices:
            info['device'] = cuda_devices[0]['key']
            info['gpu_name'] = cuda_devices[0]['name']
            info['acceleration'] = 'gpu'
        elif dml_devices:
            info['device'] = dml_devices[0]['key']
            info['gpu_name'] = dml_devices[0]['name']
            info['acceleration'] = 'gpu'

        return info

    @staticmethod
    def _detect_cuda_devices() -> list:
        devices = []
        try:
            import torch
            if not torch.cuda.is_available():
                return devices
            count = torch.cuda.device_count()
            for i in range(count):
                name = torch.cuda.get_device_name(i)
                devices.append({'key': 'cuda', 'name': name, 'index': i, 'type': 'cuda'})
                logger.info(f"CUDA device {i}: {name}")
        except Exception:
            pass
        return devices

    @staticmethod
    def _detect_directml_devices() -> list:
        devices = []
        try:
            import torch_directml
            count = torch_directml.device_count()
            for i in range(count):
                name = torch_directml.device_name(i).strip().rstrip('\x00').strip()
                devices.append({'key': f'directml:{i}', 'name': name, 'index': i, 'type': 'directml'})
                logger.info(f"DirectML device {i}: {name}")
        except ImportError:
            logger.info("torch_directml not installed")
        except Exception as e:
            logger.debug(f"DirectML detection failed: {e}")
        return devices

    @staticmethod
    def _detect_npu() -> bool:
        try:
            import subprocess
            r = subprocess.run(
                ['powershell', '-Command',
                 'Get-PnpDevice | Where-Object { $_.FriendlyName -match \"NPU|Compute Accelerator\" } | Select-Object -ExpandProperty Status'],
                capture_output=True, text=True, timeout=10,
            )
            return 'OK' in r.stdout
        except Exception:
            pass
        return False


class EmbeddingEngine:

    DEFAULT_MODEL = "BAAI/bge-small-zh-v1.5"
    FALLBACK_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

    def __init__(self, model_name: str = None, device: str = None, cache_dir: str = None):
        self.model_name = model_name or self.DEFAULT_MODEL
        self.cache_dir = cache_dir or os.path.join(
            os.path.expanduser("~"), ".cache", "article_searcher", "models"
        )
        self._model = None
        self._onnx_engine = None
        self._device = device or 'auto'
        self._directml_device_index = 0
        self.hardware_info = HardwareDetector.detect()

        if self._device == 'auto':
            self._device = self.hardware_info['device']

        self._actual_device = self._device
        os.makedirs(self.cache_dir, exist_ok=True)

    def set_device(self, device: str):
        if device == self._device and (self._model is not None or self._onnx_engine is not None):
            return

        self._directml_device_index = 0
        if device == 'auto':
            self._device = self.hardware_info['device']
        elif device.startswith('directml:'):
            try:
                self._directml_device_index = int(device.split(':', 1)[1])
            except (ValueError, IndexError):
                self._directml_device_index = 0
            self._device = device
        elif device not in self.hardware_info.get('available_devices', ['cpu']):
            self._device = self.hardware_info['device']
        else:
            self._device = device

        self._model = None
        self._onnx_engine = None
        self._actual_device = None

    @property
    def device(self) -> str:
        return self._device

    @property
    def actual_device(self) -> str:
        return self._actual_device or self._device

    @property
    def model(self):
        if self._device == 'npu':
            if self._onnx_engine is None:
                self._load_onnx_engine()
            return self._onnx_engine
        if self._model is None:
            self._load_model()
        return self._model

    def _load_onnx_engine(self):
        from .onnx_engine import OnnxEmbeddingEngine
        logger.info(f"Loading ONNX model: {self.model_name} on NPU (device_id=1)")
        engine = OnnxEmbeddingEngine(self.model_name, self.cache_dir, device_id=1)
        try:
            engine._dimension = 512
            engine.ensure_loaded()
            actual = engine._session.get_providers()[0] if engine._session else ''
            if 'DML' in actual:
                self._onnx_engine = engine
                self._actual_device = 'npu (DirectML)'
                logger.info(f"Model loaded on NPU: {actual}")
                return
        except Exception:
            pass
        logger.warning("NPU unavailable, falling back to GPU via sentence-transformers")
        first_dml = next(
            (g['key'] for g in self.hardware_info.get('gpu_devices', []) if g['type'] == 'directml'),
            None
        )
        self._device = first_dml or 'cpu'
        self._onnx_engine = None
        self._actual_device = None
        self._load_model()

    def _load_model(self):
        logger.info(f"Loading model: {self.model_name} on {self._device}")
        try:
            from sentence_transformers import SentenceTransformer
            if self._device == 'cuda':
                device = 'cuda'
                self._actual_device = 'cuda'
            elif self._device and (self._device.startswith('directml:') or self._device == 'directml'):
                import torch_directml
                dml_idx = self._directml_device_index
                device = torch_directml.device(dml_idx)
                gpu_name = self.hardware_info.get('gpu_devices', [])
                gpu_name = next(
                    (g['name'] for g in gpu_name if g['type'] == 'directml' and g['index'] == dml_idx),
                    f'DirectML #{dml_idx}'
                )
                self._actual_device = f'directml (GPU {dml_idx}: {gpu_name})'
            else:
                device = 'cpu'
                self._actual_device = 'cpu'

            self._model = SentenceTransformer(
                self.model_name, cache_folder=self.cache_dir, device=device
            )
            logger.info(f"Model loaded on {self._actual_device}")
        except Exception as e:
            logger.warning(f"Failed to load on {self._device}: {e}. Falling back to CPU.")
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(
                    self.FALLBACK_MODEL, cache_folder=self.cache_dir, device='cpu'
                )
            except Exception as e2:
                logger.error(f"Fallback model also failed: {e2}")
                raise
            self._actual_device = 'cpu'
            self._device = 'cpu'

    def encode(self, texts: List[str], batch_size: int = 32, show_progress: bool = False) -> np.ndarray:
        if not texts:
            return np.array([])
        filtered = [t if t.strip() else " " for t in texts]
        eng = self.model
        if self._onnx_engine is not None:
            return eng.encode(filtered)
        return eng.encode(
            filtered, batch_size=batch_size, show_progress_bar=show_progress,
            normalize_embeddings=True, convert_to_numpy=True,
        )

    def encode_single(self, text: str) -> np.ndarray:
        return self.encode([text])[0]

    @property
    def dimension(self) -> int:
        if self._onnx_engine is not None:
            return self._onnx_engine.dimension
        if self._model is not None:
            try:
                return self._model.get_embedding_dimension()
            except AttributeError:
                return self._model.get_sentence_embedding_dimension()
        return 0

    @property
    def available_devices(self) -> List[str]:
        return self.hardware_info.get('available_devices', ['cpu'])

    def get_status_info(self) -> dict:
        actual = self._actual_device or self._device
        dim = self.dimension
        return {
            'model': self.model_name,
            'device': self._device,
            'actual_device': actual,
            'dimension': dim,
            'hardware': self.hardware_info,
            'available_devices': self.hardware_info.get('available_devices', ['cpu']),
        }
