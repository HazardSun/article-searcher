"""
P1 功能 7 星标 / 收藏：与 file_tags / 簇 三权分立 测试。

- star / unstar / is_starred / get_starred 独立列表；
- 星标不影响 tag 过滤（get_files_by_tag 不被污染）；
- 持久化（save/load 往返）；engine 委托方法一致；
- remove_file 同步清理星标。
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.tagger import TagManager

DIM = 8


def make_engine():
    import core.engine as engine_mod

    class FakeEmbeddingEngine:
        def __init__(self, *a, **k):
            self.model_name = "fake"
        def encode(self, texts, **k):
            import numpy as np
            return np.random.RandomState(0).rand(len(texts), DIM).astype("float32")
        def encode_single(self, text):
            import numpy as np
            return np.zeros(DIM, dtype="float32")
        @property
        def dimension(self):
            return DIM
        def get_status_info(self):
            return {"model": "fake", "dimension": DIM}

    engine_mod.EmbeddingEngine = FakeEmbeddingEngine
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "chromadb")
    from core.engine import ArticleSearchEngine
    return ArticleSearchEngine(db_path=db, embedding_model="fake", search_mode="hybrid")


class TestStarIndependence(unittest.TestCase):
    def setUp(self):
        self.tm = TagManager(save_path=os.path.join(tempfile.mkdtemp(), "tags.json"))

    def test_star_basic(self):
        self.tm.star("/a.md")
        self.assertTrue(self.tm.is_starred("/a.md"))
        self.assertEqual(self.tm.get_starred(), ["/a.md"])
        self.tm.unstar("/a.md")
        self.assertFalse(self.tm.is_starred("/a.md"))
        self.assertEqual(self.tm.get_starred(), [])

    def test_star_does_not_pollute_tags(self):
        self.tm.star("/a.md")
        # 星标不应出现在标签索引
        self.assertNotIn("/a.md", self.tm.get_files_by_tag("收藏")
                          if "收藏" in self.tm.get_all_tags() else [])
        # 反向：打标签不应影响星标
        self.tm.set_tags_for_file("/a.md", ["重要"])
        self.assertTrue(self.tm.is_starred("/a.md"))
        self.assertEqual(self.tm.get_files_by_tag("重要"), ["/a.md"])
        self.assertEqual(self.tm.get_starred(), ["/a.md"])

    def test_persist_roundtrip(self):
        p = os.path.join(tempfile.mkdtemp(), "tags.json")
        tm = TagManager(save_path=p)
        tm.star("/a.md")
        tm.star("/b.md")
        tm.set_clusters([{"id": "c0", "label": "L", "files": [], "sample_titles": [], "centroid": []}])
        tm.save()
        tm2 = TagManager(save_path=p)
        tm2.load()
        self.assertEqual(set(tm2.get_starred()), {"/a.md", "/b.md"})
        self.assertIn("c0", tm2.get_clusters())

    def test_remove_file_purges_star(self):
        self.tm.star("/a.md")
        self.tm.set_tags_for_file("/a.md", ["x"])
        self.tm.set_clusters([{"id": "c0", "label": "L", "files": ["/a.md"], "sample_titles": [], "centroid": []}])
        self.tm.remove_file("/a.md")
        self.assertFalse(self.tm.is_starred("/a.md"))
        self.assertEqual(self.tm.get_starred(), [])
        self.assertEqual(tm := self.tm.get_tags_for_file("/a.md"), [])
        self.assertEqual(self.tm.get_clusters()["c0"]["files"], [])


class TestEngineStarDelegate(unittest.TestCase):
    def setUp(self):
        self.engine = make_engine()

    def test_delegation(self):
        self.engine.star_file("/x.md")
        self.assertTrue(self.engine.is_starred_file("/x.md"))
        self.assertIn("/x.md", self.engine.get_starred_files())
        # 状态扩展字段
        st = self.engine.get_status()
        self.assertEqual(st["starred_count"], 1)
        self.engine.unstar_file("/x.md")
        self.assertFalse(self.engine.is_starred_file("/x.md"))
        self.assertEqual(self.engine.get_status()["starred_count"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
