"""
P1 功能 11 多源 + 排除规则：单元 + 集成测试。

覆盖：
- path_matches_exclude 的「相对路径 / basename / 路径段」三种匹配与大小写不敏感；
- Source / SourceList 的增删改与派生视图（enabled_paths / all_excludes / 序列化）；
- engine.load_folder 多源遍历时应用排除规则（集成）。
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.multisource import path_matches_exclude, Source, SourceList
from core.engine import ArticleSearchEngine
import core.engine as engine_mod


DIM = 8


class FakeEmbeddingEngine:
    def __init__(self, model_name=None, device=None, cache_dir=None,
                 batch_size=32, priority=None):
        self.model_name = model_name or "fake/model"
        self._requested = device or "auto"
        self.cache_dir = cache_dir
        self._batch_size = batch_size

    def encode(self, texts, batch_size=None, show_progress=False):
        import numpy as np
        return np.random.RandomState(0).rand(len(texts), DIM).astype("float32")

    def encode_single(self, text):
        import numpy as np
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
    return ArticleSearchEngine(db_path=db, embedding_model="fake/model",
                               search_mode="hybrid")


class TestPathMatchesExclude(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        # 制造相对路径：root/sub/note.md
        sub = os.path.join(self.root, "sub")
        os.makedirs(sub, exist_ok=True)
        self.note = os.path.join(sub, "note.md")
        open(self.note, "w").close()

    def test_relative_path_match(self):
        self.assertTrue(path_matches_exclude(self.note, self.root, ["sub/note.md"]))

    def test_basename_match(self):
        # 规则只写文件名
        self.assertTrue(path_matches_exclude(self.note, self.root, ["note.md"]))

    def test_path_segment_match(self):
        # 规则写目录名，应匹配其中任意文件
        self.assertTrue(path_matches_exclude(self.note, self.root, ["sub"]))

    def test_glob_star_match(self):
        self.assertTrue(path_matches_exclude(self.note, self.root, ["*.md"]))
        self.assertTrue(path_matches_exclude(self.note, self.root, ["sub/*.md"]))

    def test_case_insensitive(self):
        up = os.path.join(self.root, "Sub", "Note.MD")
        self.assertTrue(path_matches_exclude(up, self.root, ["sub/note.md"]))

    def test_no_patterns(self):
        self.assertFalse(path_matches_exclude(self.note, self.root, []))

    def test_non_matching(self):
        self.assertFalse(path_matches_exclude(self.note, self.root, ["other", "*.txt"]))


class TestSourceModel(unittest.TestCase):
    def test_matches_exclude_delegates(self):
        s = Source(path="/data", exclude_rules=["*.tmp", "secret"])
        self.assertTrue(s.matches_exclude("/data/a.tmp"))
        self.assertTrue(s.matches_exclude("/data/secret/notes.md"))
        self.assertFalse(s.matches_exclude("/data/ok.md"))

    def test_dict_roundtrip(self):
        s = Source(path="/data", exclude_rules=["x"], enabled=False)
        d = s.to_dict()
        self.assertEqual(d, {"path": "/data", "exclude_rules": ["x"], "enabled": False})
        s2 = Source.from_dict(d)
        self.assertEqual(s2.path, s.path)
        self.assertEqual(s2.exclude_rules, s.exclude_rules)
        self.assertEqual(s2.enabled, s.enabled)


class TestSourceList(unittest.TestCase):
    def test_add_update_remove_get(self):
        sl = SourceList()
        sl.add(Source("/a"))
        sl.add(Source("/b", exclude_rules=["x"]))
        # 重复 add 同路径应覆盖
        sl.add(Source("/a", exclude_rules=["y"]))
        self.assertEqual(len(sl), 2)
        self.assertEqual(sl.get("/a").exclude_rules, ["y"])
        sl.update("/b", enabled=False)
        self.assertFalse(sl.get("/b").enabled)
        sl.remove("/a")
        self.assertIsNone(sl.get("/a"))
        self.assertEqual(len(sl), 1)

    def test_derived_views(self):
        sl = SourceList([
            Source("/a", exclude_rules=["a1"], enabled=True),
            Source("/b", exclude_rules=["b1"], enabled=False),
        ])
        self.assertEqual(sl.enabled_paths(), ["/a"])
        self.assertEqual(sl.all_excludes(), ["a1"])  # 禁用的不计入
        d = sl.to_dicts()
        sl2 = SourceList.from_dicts(d)
        self.assertEqual([s.path for s in sl2], ["/a", "/b"])
        self.assertEqual(sl2.all_excludes(), ["a1"])


class TestMultiSourceLoadFolder(unittest.TestCase):
    def setUp(self):
        self.engine = make_engine()
        self.dir1 = tempfile.mkdtemp()
        self.dir2 = tempfile.mkdtemp()
        # dir1: 普通文件（应被索引）
        Path(os.path.join(self.dir1, "normal.md")).write_text(
            "# 普通\n正文内容。", encoding="utf-8")
        # dir2: keep 索引；draft_secret 与 archive/ 排除
        Path(os.path.join(self.dir2, "keep.md")).write_text(
            "# 保留\n内容。", encoding="utf-8")
        Path(os.path.join(self.dir2, "draft_secret.md")).write_text(
            "# 机密\n内容。", encoding="utf-8")
        arch = os.path.join(self.dir2, "archive")
        os.makedirs(arch, exist_ok=True)
        Path(os.path.join(arch, "old.md")).write_text(
            "# 旧\n内容。", encoding="utf-8")

    def _index(self):
        self.engine.set_sources([
            Source(self.dir1, enabled=True),
            Source(self.dir2, exclude_rules=["*secret*", "archive"], enabled=True),
        ])
        return self.engine.load_folder()

    def test_exclude_applied_during_traversal(self):
        stats = self._index()
        indexed = self.engine.list_indexed_files()
        paths = set(indexed.keys())
        self.assertIn(os.path.join(self.dir1, "normal.md"), paths)
        self.assertIn(os.path.join(self.dir2, "keep.md"), paths)
        self.assertNotIn(os.path.join(self.dir2, "draft_secret.md"), paths)
        self.assertNotIn(os.path.join(self.dir2, "archive", "old.md"), paths)
        # status 中 sources 计数 = 2 个启用源
        self.assertEqual(stats["sources"], 2)
        self.assertEqual(stats["total_files"], 2)

    def test_new_files_count(self):
        stats = self._index()
        self.assertEqual(stats["new_files"], 2)
        self.assertEqual(stats["errors"], [])

    def test_current_folder_shim(self):
        self._index()
        # shim 返回首个启用源
        self.assertEqual(self.engine.current_folder, self.dir1)

    def test_disabled_source_skipped(self):
        self.engine.set_sources([
            Source(self.dir1, enabled=False),
            Source(self.dir2, enabled=True),
        ])
        stats = self.engine.load_folder()
        self.assertEqual(stats["sources"], 1)
        indexed = self.engine.list_indexed_files()
        self.assertNotIn(os.path.join(self.dir1, "normal.md"), indexed.keys())


if __name__ == "__main__":
    unittest.main(verbosity=2)
