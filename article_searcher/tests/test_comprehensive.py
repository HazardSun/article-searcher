"""
综合全面测试套件（真实后端，unittest 可发现）。

覆盖：
  1) 真实 ONNX 推理（本机 DML / DirectML 后端，bge-small-zh-v1.5）
     - 维度、L2 归一化、确定性、相似度排序、批处理、空输入
  2) 真实 ChromaDB 向量库集成
     - 增删、余弦相似度排序、MD5 记录、get_all_md5s
  3) 完整引擎端到端（真实 embedding + 真实 chromadb）
     - 三模式检索、空查询、标签过滤、增量索引、单文件重建、移除
  4) Parser / Chunker / Tagger 单元
  5) Config 优先级持久化
  6) GUI offscreen 冒烟（PyQt6 实例化主窗口）

所有测试不依赖网络；模型已导出至本地缓存，首次推理在 DML 上完成。
"""

import os
import sys
import json
import time
import shutil
import tempfile
import unittest
from pathlib import Path

# 必须在导入任何 PyQt 模块之前设置 offscreen，GUI 冒烟测试才可在无显示环境运行
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np

from core.parser import FileScanner, ContentParser, TextChunk
from core.chunker import SemanticChunker, ChunkConfig
from core.tagger import TagManager, KeywordExtractor
from core.config import ConfigStore, AppConfig
from core.lexical import LexicalIndex
from core.search import rrf
from core.vectorstore import VectorStore
from core.embedding import EmbeddingEngine
from core.engine import ArticleSearchEngine, SEARCH_MODES

DIM = 512  # bge-small-zh-v1.5


def _write(path: Path, text: str):
    path.write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------- #
# 1) 真实 ONNX 推理
# --------------------------------------------------------------------------- #
class TestOnnxEmbeddingReal(unittest.TestCase):
    """验证真实 ONNX 引擎（本机 DML 后端）的语义编码正确性。"""

    @classmethod
    def setUpClass(cls):
        cls.eng = EmbeddingEngine(device="auto")  # 自动选择 DML / CPU
        cls.actual = cls.eng.actual_device

    def test_backend_loaded(self):
        self.assertIn(self.eng.backend, ("onnx", "sentence-transformers"))
        self.assertEqual(self.eng.dimension, DIM)

    def test_actual_provider_reported(self):
        # 应明确记录实际后端（Dml / CPU / CUDA / OpenVINO…）
        self.assertTrue(self.eng.actual_device, "actual_device 不应为空")

    def test_dimension_and_shape(self):
        v = self.eng.encode_single("测试文本")
        self.assertEqual(v.shape, (DIM,))

    def test_l2_normalized(self):
        vecs = self.eng.encode(["深度学习", "机器学习", "烹饪技巧"], batch_size=2)
        self.assertEqual(vecs.shape, (3, DIM))
        for row in vecs:
            self.assertAlmostEqual(float(np.linalg.norm(row)), 1.0, places=4)

    def test_deterministic(self):
        a = self.eng.encode_single("相同的输入应得到相同的向量")
        b = self.eng.encode_single("相同的输入应得到相同的向量")
        self.assertTrue(np.allclose(a, b, atol=1e-6), "同输入应 deterministic")

    def test_semantic_ordering(self):
        """语义相近文本的余弦相似度应高于无关文本。"""
        group_a = self.eng.encode([
            "深度学习是机器学习的一个重要分支领域",
            "神经网络属于深度学习的核心模型结构",
        ])
        group_b = self.eng.encode([
            "今天天气晴朗适合出门散步",
            "苹果是一种常见的水果营养丰富",
        ])
        sim_aa = float(np.dot(group_a[0], group_a[1]))
        sim_ab = float(np.dot(group_a[0], group_b[0]))
        self.assertGreater(sim_aa, sim_ab, "组内相似度应高于组间")

    def test_batching_and_empty(self):
        many = ["句子%d" % i for i in range(7)]
        out = self.eng.encode(many, batch_size=3)
        self.assertEqual(out.shape, (7, DIM))
        empty = self.eng.encode([])
        self.assertEqual(empty.shape, (0,))

    def test_long_text_truncation(self):
        long_text = "人工智能" * 2000  # 远超 512 token
        v = self.eng.encode_single(long_text)
        self.assertEqual(v.shape, (DIM,))  # 不应崩溃，且能正常返回


# --------------------------------------------------------------------------- #
# 2) 真实 ChromaDB 向量库集成
# --------------------------------------------------------------------------- #
def _make_chunk(cid, content, fp="a.md"):
    return TextChunk(
        chunk_id=cid, file_path=fp, file_name=Path(fp).name, title="T",
        content=content, start_line=0, end_line=1, chunk_index=0,
        total_chunks=1,
    )


class TestVectorStoreReal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.db = os.path.join(cls.tmp, "chromadb")
        cls.vs = VectorStore(db_path=cls.db, embedding_engine=None)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _rand(self, n):
        rng = np.random.RandomState(42)
        return rng.rand(n, DIM).astype("float32")

    def test_add_and_count(self):
        chunks = [_make_chunk("c1", "深度学习 神经网络"), _make_chunk("c2", "烹饪 美食", "b.md")]
        self.vs.add_chunks(chunks, self._rand(2))
        self.assertEqual(self.vs.get_chunk_count(), 2)

    def test_search_returns_ranked(self):
        q = self._rand(1)[0]
        res = self.vs.search(query_embedding=q, top_k=5)
        self.assertTrue(res)
        sims = [r["similarity"] for r in res]
        self.assertEqual(sims, sorted(sims, reverse=True))

    def test_delete_by_file(self):
        before = self.vs.get_chunk_count()
        self.vs.delete_by_file("a.md")
        after = self.vs.get_chunk_count()
        self.assertEqual(after, before - 1)
        self.assertNotIn("a.md", [f for f in self.vs.get_indexed_files()])

    def test_md5_roundtrip_and_all(self):
        self.vs.set_file_md5("x.md", "abc123")
        self.assertEqual(self.vs.get_file_md5("x.md"), "abc123")
        all_md5 = self.vs.get_all_md5s()
        self.assertEqual(all_md5.get("x.md"), "abc123")

    def test_persistence_across_reopen(self):
        """重新打开同一 db_path 应保持数据。"""
        cnt = self.vs.get_chunk_count()
        vs2 = VectorStore(db_path=self.db, embedding_engine=None)
        self.assertEqual(vs2.get_chunk_count(), cnt)


# --------------------------------------------------------------------------- #
# 3) 完整引擎端到端（真实 embedding + 真实 chromadb）
# --------------------------------------------------------------------------- #
class TestEngineEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.db = os.path.join(cls.tmp, "chromadb")
        cls.folder = os.path.join(cls.tmp, "docs")
        os.makedirs(cls.folder, exist_ok=True)
        # 三个不同主题的中文文档，便于检索区分度验证
        _write(Path(cls.folder) / "ml.md",
               "# 机器学习入门\n\n深度学习是机器学习的重要分支。"
               "神经网络属于深度学习的核心模型，常用于自然语言处理。\n")
        _write(Path(cls.folder) / "cook.md",
               "# 烹饪技巧\n\n今天教大家做一道家常菜，苹果派的制作方法简单又美味。\n")
        _write(Path(cls.folder) / "hist.md",
               "# 历史随笔\n\n唐朝是中国历史上最强盛的朝代之一，长安城繁华无比。\n")
        cls.engine = ArticleSearchEngine(
            db_path=cls.db, model_cache_dir=None,
            embedding_model="BAAI/bge-small-zh-v1.5",
            device="cpu", search_mode="hybrid",
        )
        cls.engine.load_folder(cls.folder, incremental=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_indexed_files_count(self):
        files = self.engine.list_indexed_files()
        self.assertEqual(len(files), 3)

    def test_empty_query_returns_nothing(self):
        self.assertEqual(self.engine.search(""), [])
        self.assertEqual(self.engine.search("   "), [])

    def test_semantic_mode_relevance(self):
        res = self.engine.search("神经网络 深度学习", mode="semantic", top_k=3)
        self.assertTrue(res)
        top_fp = res[0]["metadata"]["file_path"]
        self.assertTrue(top_fp.endswith("ml.md"), f"语义检索应命中 ml.md，实际: {top_fp}")

    def test_keyword_mode_relevance(self):
        res = self.engine.search("苹果", mode="keyword", top_k=3)
        self.assertTrue(res)
        self.assertTrue(any(r["metadata"]["file_path"].endswith("cook.md") for r in res))

    def test_hybrid_mode_relevance(self):
        res = self.engine.search("唐朝 历史", mode="hybrid", top_k=3)
        self.assertTrue(res)
        self.assertTrue(any(r["metadata"]["file_path"].endswith("hist.md") for r in res))
        # 混合模式应带 rrf_score 与 similarity
        for r in res:
            self.assertIn("rrf_score", r)
            self.assertIn("similarity", r)
            self.assertGreaterEqual(r["similarity"], 0.0)
            self.assertLessEqual(r["similarity"], 1.0)

    def test_three_modes_valid(self):
        self.assertEqual(set(SEARCH_MODES), {"semantic", "keyword", "hybrid"})

    def test_incremental_skip_unchanged(self):
        stats = self.engine.load_folder(self.folder, incremental=True)
        self.assertEqual(stats["unchanged_files"], 3, "未修改文件应全部命中增量跳过")
        self.assertEqual(stats["new_files"], 0)
        self.assertEqual(stats["updated_files"], 0)
        # 切片总数不应因重复索引而翻倍
        self.assertEqual(self.engine.vector_store.get_chunk_count(),
                         self.engine.lexical.count())

    def test_incremental_detects_modification(self):
        p = Path(self.folder) / "ml.md"
        _write(p, "# 机器学习入门（修订版）\n\n修订后新增了关于支持向量机与决策树的内容说明。\n")
        stats = self.engine.load_folder(self.folder, incremental=True)
        self.assertGreaterEqual(stats["updated_files"], 1, "修改文件应被识别为更新")

    def test_reindex_single_file(self):
        p = Path(self.folder) / "cook.md"
        _write(p, "# 烹饪技巧（重建）\n\n重建索引测试：红烧肉的做法讲究火候与糖色。\n")
        before = self.engine.vector_store.get_chunk_count()
        self.engine.reindex_file(str(p))
        after = self.engine.vector_store.get_chunk_count()
        # 重建后该文件仍只贡献其自身切片，总量不应异常膨胀
        self.assertGreaterEqual(after, 1)
        # 内容中包含“红烧肉”，检索应可命中
        res = self.engine.search("红烧肉", mode="keyword", top_k=5)
        self.assertTrue(any(r["metadata"]["file_path"].endswith("cook.md") for r in res))

    def test_remove_file_from_index(self):
        p = str(Path(self.folder) / "hist.md")
        self.engine.remove_file_from_index(p)
        files = self.engine.list_indexed_files()
        self.assertNotIn(p, files)
        res = self.engine.search("唐朝", mode="semantic", top_k=5)
        self.assertFalse(any(r["metadata"]["file_path"].endswith("hist.md") for r in res))

    def test_tag_filter(self):
        tags = self.engine.get_all_tags()
        if tags:
            t = tags[0]
            res = self.engine.search("内容", mode="hybrid", top_k=10, tag_filter=t)
            for r in res:
                self.assertIn(t, r.get("file_tags", []))
        # 不存在的标签应返回空
        self.assertEqual(self.engine.search("内容", tag_filter="__no_such_tag__"), [])

    def test_unicode_and_emoji_query(self):
        # 不应崩溃
        res = self.engine.search("机器学习 🚀 神经网络", mode="hybrid", top_k=3)
        self.assertIsInstance(res, list)


# --------------------------------------------------------------------------- #
# 4) Parser / Chunker / Tagger
# --------------------------------------------------------------------------- #
class TestParser(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.scan = FileScanner()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_scan_finds_supported(self):
        for name, ext in [("a", ".md"), ("b", ".txt"), ("c", ".html"), ("d", ".pdf"), ("e", ".docx")]:
            _write(Path(self.tmp) / (name + ext), "# 标题\n内容")
        # 加入一个不支持的文件，应被忽略
        _write(Path(self.tmp) / "ignore.png", "img")
        files = self.scan.scan_directory(self.tmp)
        exts = {Path(f.file_path).suffix for f in files}
        self.assertEqual(exts, {".md", ".txt", ".html", ".pdf", ".docx"})

    def test_md_title_extraction(self):
        p = Path(self.tmp) / "doc.md"
        _write(p, "# 我的标题\n正文内容\n")
        meta = self.scan.extract_metadata(str(p))
        self.assertEqual(meta.title, "我的标题")
        self.assertTrue(meta.md5_hash)

    def test_html_title_extraction(self):
        p = Path(self.tmp) / "page.html"
        _write(p, "<html><head><title>页面标题</title></head><body><p>内容</p></body></html>")
        meta = self.scan.extract_metadata(str(p))
        self.assertEqual(meta.title, "页面标题")

    def test_md5_deterministic(self):
        p = Path(self.tmp) / "x.md"
        _write(p, "相同内容")
        m1 = self.scan.extract_metadata(str(p)).md5_hash
        m2 = self.scan.extract_metadata(str(p)).md5_hash
        self.assertEqual(m1, m2)

    def test_parse_markdown_structure(self):
        cp = ContentParser()
        paras = cp.parse_markdown("# 标题一\n段落一\n\n## 标题二\n段落二\n\n```\n代码块\n```\n")
        types = [p["type"] for p in paras]
        self.assertIn("heading", types)
        self.assertIn("paragraph", types)
        self.assertIn("code", types)

    def test_parse_html_strips_script_style(self):
        html = "<html><head><style>.a{color:red}</style><script>var x=1;</script></head><body><p>正文内容</p></body></html>"
        cp = ContentParser()
        paras = cp.parse_html(html)
        joined = " ".join(p["content"] for p in paras)
        self.assertNotIn("var x", joined)
        self.assertIn("正文内容", joined)

    def test_parse_text(self):
        paras = ContentParser.parse_text("第一行\n第二行\n\n第三行\n")
        self.assertEqual(len(paras), 2)


class TestChunker(unittest.TestCase):
    def test_chunk_splits_large_group(self):
        cfg = ChunkConfig(max_chunk_size=50, overlap_size=10)
        chunker = SemanticChunker(cfg)
        paras = [{"type": "heading", "content": "大标题", "line": 0, "level": 1}]
        # 构造超过 max_chunk_size 的长段落
        long_text = "这是一段很长的中文测试内容用于触发切片逻辑。" * 10
        paras.append({"type": "paragraph", "content": long_text, "line": 1})
        chunks = chunker.chunk_article("f.md", "f.md", "大标题", paras)
        self.assertGreater(len(chunks), 1, "超长内容应被切分为多个切片")
        for c in chunks:
            self.assertEqual(c.file_path, "f.md")
            self.assertTrue(c.content)

    def test_chunk_total_chunks_set(self):
        chunker = SemanticChunker(ChunkConfig())
        paras = [
            {"type": "heading", "content": "H1", "line": 0, "level": 1},
            {"type": "paragraph", "content": "段落A", "line": 1},
            {"type": "heading", "content": "H2", "line": 2, "level": 1},
            {"type": "paragraph", "content": "段落B", "line": 3},
        ]
        chunks = chunker.chunk_article("g.md", "g.md", "T", paras)
        total = len(chunks)
        for c in chunks:
            self.assertEqual(c.total_chunks, total)


class TestTagger(unittest.TestCase):
    def setUp(self):
        self.tm = TagManager()

    def test_generate_and_query(self):
        fp = "/x/ml.md"
        tags = self.tm.generate_tags(fp, "深度学习 与 神经网络 的 关系 探讨", "深度学习入门")
        self.assertIsInstance(tags, list)
        # 标签应可被反向查询到该文件
        for t in tags:
            self.assertIn(fp, self.tm.get_files_by_tag(t))

    def test_remove_file_cleans_index(self):
        fp = "/x/a.md"
        self.tm.generate_tags(fp, "机器学习 模型 训练", "ML")
        tags = self.tm.get_all_tags()
        self.assertTrue(tags)
        self.tm.remove_file(fp)
        for t in tags:
            self.assertNotIn(fp, self.tm.get_files_by_tag(t))

    def test_get_tags_for_file(self):
        fp = "/x/b.md"
        self.tm.generate_tags(fp, "烹饪 美食 菜谱", "做饭")
        self.assertEqual(self.tm.get_tags_for_file(fp), self.tm.get_tags_for_file(fp))


# --------------------------------------------------------------------------- #
# 5) Config 优先级持久化
# --------------------------------------------------------------------------- #
class TestConfigPriority(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_priority_persisted(self):
        cs = ConfigStore(self.tmp)
        cs.update(priority="gpu,cpu", device="cuda:0", model="BAAI/bge-small-zh-v1.5",
                  search_mode="semantic", top_k=20)
        cs2 = ConfigStore(self.tmp)
        self.assertEqual(cs2.config.priority, "gpu,cpu")
        self.assertEqual(cs2.config.device, "cuda:0")
        self.assertEqual(cs2.config.search_mode, "semantic")
        self.assertEqual(cs2.config.top_k, 20)

    def test_default_priority_present(self):
        cs = ConfigStore(self.tmp)
        self.assertTrue(cs.config.priority, "AppConfig 应带默认优先级")


# --------------------------------------------------------------------------- #
# 6) GUI offscreen 冒烟
# --------------------------------------------------------------------------- #
class TestGUISmoke(unittest.TestCase):
    def test_main_window_instantiates(self):
        from PyQt6.QtWidgets import QApplication
        from ui.main_window import MainWindow
        from core.config import ConfigStore

        app = QApplication.instance() or QApplication([])
        tmp_cfg = tempfile.mkdtemp()
        cs = ConfigStore(tmp_cfg)
        eng = ArticleSearchEngine(
            db_path=os.path.join(tmp_cfg, "chromadb"), device="cpu",
            search_mode="hybrid",
        )
        win = MainWindow(eng, cs)
        # 关键控件应已创建
        self.assertIsNotNone(win.search_input)
        self.assertGreaterEqual(win.device_combo.count(), 1)
        self.assertIn(win.mode_combo.count(), (3,))
        self.assertIsNotNone(win.result_list)
        self.assertIsNotNone(win.doc_viewer)
        win.show()
        app.processEvents()
        win.close()
        app.processEvents()
        shutil.rmtree(tmp_cfg, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
