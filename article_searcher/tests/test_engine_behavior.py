"""
针对审核发现的高危问题做行为级验证（不依赖真实模型/ONNX 推理）：
- 关键词检索不得调用语义编码（encode_single）
- reindex_file 必须使用公开的 extract_metadata 而非私有 _extract_metadata
- set_device / set_model 正确委托给 embedding 引擎
- 混合检索结果附带 rrf_score 与 similarity
通过将 ArticleSearchEngine 内部的 EmbeddingEngine 替换为假引擎，并用临时目录的
ChromaDB 完成（chromadb 已安装）。
"""

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core.engine as engine_mod
from core.parser import TextChunk
from core.engine import ArticleSearchEngine, SEARCH_MODES

DIM = 8


class FakeEmbeddingEngine:
    """仅实现 ArticleSearchEngine 所需的接口，不加载任何模型"""

    def __init__(self, model_name=None, device=None, cache_dir=None,
                 batch_size=32, priority=None):
        self.model_name = model_name or "fake/model"
        self._requested = device or "auto"
        self.cache_dir = cache_dir
        self._batch_size = batch_size
        self._calls = []

    def encode(self, texts, batch_size=None, show_progress=False):
        arr = np.random.RandomState(0).rand(len(texts), DIM).astype("float32")
        return arr

    def encode_single(self, text):
        self._calls.append(text)
        return np.zeros(DIM, dtype="float32")

    @property
    def device(self):
        return self._requested

    @property
    def dimension(self):
        return DIM

    @property
    def backend(self):
        return "fake"

    def set_device(self, device):
        self._requested = device

    def set_model(self, model_name, device=None):
        self.model_name = model_name
        if device:
            self._requested = device

    @property
    def devices(self):
        return []

    @property
    def available_devices(self):
        return ["cpu"]

    @property
    def recommended_device(self):
        return "cpu"

    def get_status_info(self):
        return {"model": self.model_name, "device": self._requested,
                "actual_device": self._requested, "backend": "fake",
                "dimension": DIM, "hardware": {}, "available_devices": ["cpu"],
                "recommended_device": "cpu"}

    hardware_info = property(lambda self: {"devices": [], "priority": "npu,gpu,cpu"})


def make_engine():
    engine_mod.EmbeddingEngine = FakeEmbeddingEngine
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "chromadb")
    eng = ArticleSearchEngine(db_path=db, embedding_model="fake/model",
                              search_mode="hybrid")
    return eng


class TestEngineBehavior(unittest.TestCase):
    def setUp(self):
        self.engine = make_engine()
        self.tmp = tempfile.mkdtemp()
        # 预置词法索引
        chunks = [
            TextChunk(chunk_id="c1", file_path="/x/a.md", file_name="a.md",
                      title="A", content="神经网络 深度学习", start_line=0,
                      end_line=1, chunk_index=0, total_chunks=1),
            TextChunk(chunk_id="c2", file_path="/x/b.md", file_name="b.md",
                      title="B", content="机器学习 入门", start_line=0,
                      end_line=1, chunk_index=0, total_chunks=1),
        ]
        self.engine.lexical.add_chunks(chunks)

    # ------------------------------------------------------------------ #
    def test_keyword_mode_skips_semantic_encode(self):
        """高危修复：关键词模式下不得调用 encode_single"""
        spy_calls = []
        orig = self.engine.embedding_engine.encode_single

        def spy(q):
            spy_calls.append(q)
            return orig(q)

        self.engine.embedding_engine.encode_single = spy
        res = self.engine.search("神经网络", mode="keyword")
        self.assertEqual(spy_calls, [], "关键词模式不应调用 encode_single")
        self.assertTrue(len(res) > 0, "关键词模式应返回词法结果")
        # 评分应被归一化，而非 0%
        for r in res:
            self.assertIn("similarity", r)
            self.assertGreaterEqual(r["similarity"], 0.0)
            self.assertLessEqual(r["similarity"], 1.0)

    # ------------------------------------------------------------------ #
    def test_reindex_uses_public_extract_metadata(self):
        """中危修复：reindex_file 必须走公开 API，而非私有 _extract_metadata"""
        import core.parser as parser_mod
        flag = {"used_public": False}

        def fake_extract(self, fp):
            flag["used_public"] = True
            return self._extract_metadata(Path(fp))

        parser_mod.FileScanner.extract_metadata = fake_extract
        p = os.path.join(self.tmp, "doc.md")
        Path(p).write_text("# 标题\n正文内容用于索引。", encoding="utf-8")
        try:
            self.engine.reindex_file(p)
        finally:
            parser_mod.FileScanner.extract_metadata = (
                parser_mod.FileScanner.__dict__.get("extract_metadata"))
        self.assertTrue(flag["used_public"],
                        "reindex_file 未使用公开的 extract_metadata")

    # ------------------------------------------------------------------ #
    def test_set_device_and_model_delegate(self):
        self.engine.set_device("cuda:0")
        self.assertEqual(self.engine.embedding_engine.device, "cuda:0")
        self.engine.set_model("another/model")
        self.assertEqual(self.engine.embedding_engine.model_name, "another/model")
        # 向量库应同步持有新引擎引用
        self.assertIs(self.engine.vector_store.embedding_engine,
                      self.engine.embedding_engine)

    # ------------------------------------------------------------------ #
    def test_hybrid_attaches_scores(self):
        """混合结果应同时具备 rrf_score 与 similarity（供 UI 展示）"""
        semantic = [{
            "id": "c1", "content": "x",
            "metadata": {"file_path": "/x/a.md", "file_name": "a.md",
                         "title": "A", "start_line": 0, "end_line": 1},
            "similarity": 0.9,
        }]
        self.engine.vector_store.search = lambda **kw: semantic
        res = self.engine.search("神经网络", mode="hybrid")
        self.assertTrue(res, "混合检索应返回结果")
        for r in res:
            self.assertIn("rrf_score", r)
            self.assertIn("similarity", r)

    def test_search_modes_valid(self):
        self.assertEqual(set(SEARCH_MODES), {"semantic", "keyword", "hybrid"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
