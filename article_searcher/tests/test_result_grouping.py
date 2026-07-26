"""
结果分组渲染测试（T04 / P0-2）

验证：
  - FLAT 行数不变（与旧逐行行为一致）
  - BY_FILE 折叠为文件数（同文件多命中 → 一篇）
  - BY_TAG 按 file_tags 分组（含「未标记」组）
  - BY_SOURCE 按 engine.sources 路径前缀归属（含「其他」组）

用 offscreen Qt 实例化 SearchResultList。仅校验顶层条目数与分组键，
不依赖像素渲染。
运行: python tests/test_result_grouping.py
"""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from ui.search_result_list import SearchResultList, GroupMode  # noqa: E402


class _FakeSource:
    def __init__(self, path):
        self.path = path


def _mk(fp, fn, sim):
    return {
        "content": f"内容关于 {fp}",
        "metadata": {
            "file_path": fp, "file_name": fn, "title": fn,
            "start_line": 0, "end_line": 1,
        },
        "similarity": sim,
        "matched_terms": [],
        "snippet": f"片段 {fp}",
        "file_tags": [],
        "search_mode": "semantic",
    }


class ResultGroupingTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _results(self):
        return [
            _mk("/a/doc1.md", "doc1.md", 0.9),
            _mk("/a/doc1.md", "doc1.md", 0.8),
            _mk("/b/doc2.md", "doc2.md", 0.7),
            _mk("/c/doc3.md", "doc3.md", 0.6),
        ]

    def test_flat_count_unchanged(self):
        rl = SearchResultList()
        rl.display_results(self._results())
        self.assertEqual(rl.list_widget.count(), 4)

    def test_by_file_collapses(self):
        rl = SearchResultList()
        rl.display_results(self._results(), group_mode=GroupMode.BY_FILE)
        # 3 个唯一文件（doc1 双命中折叠为一篇）
        self.assertEqual(rl.list_widget.count(), 3)

    def test_by_tag(self):
        results = self._results()
        results[0]["file_tags"] = ["ML"]
        results[1]["file_tags"] = ["ML", "DL"]
        results[2]["file_tags"] = ["CV"]
        rl = SearchResultList()
        rl.display_results(results, group_mode=GroupMode.BY_TAG)
        # ML / DL / CV 三组 + 未标记(doc3) = 4 组
        self.assertEqual(rl.list_widget.count(), 4)

    def test_by_source(self):
        results = self._results()
        rl = SearchResultList()
        rl.set_sources([_FakeSource("/a"), _FakeSource("/b")])
        rl.display_results(results, group_mode=GroupMode.BY_SOURCE)
        # /a, /b 命中 + /c 归「其他」 = 3 组
        self.assertEqual(rl.list_widget.count(), 3)

    def test_default_group_mode_flat(self):
        rl = SearchResultList()
        # 默认参数应为 FLAT，行数不变
        rl.display_results(self._results())
        self.assertEqual(rl.list_widget.count(), 4)
        self.assertEqual(rl._group_mode, GroupMode.FLAT)

    def test_set_group_mode_rerender(self):
        rl = SearchResultList()
        rl.display_results(self._results())  # FLAT: 4
        rl.set_group_mode(GroupMode.BY_FILE)
        self.assertEqual(rl.list_widget.count(), 3)
        self.assertEqual(rl._group_mode, GroupMode.BY_FILE)

    def test_group_changed_signal(self):
        rl = SearchResultList()
        seen = []
        rl.group_changed.connect(lambda m: seen.append(m))
        rl.display_results(self._results(), group_mode=GroupMode.BY_TAG)
        self.assertIn(GroupMode.BY_TAG, seen)


if __name__ == "__main__":
    unittest.main(verbosity=2)
