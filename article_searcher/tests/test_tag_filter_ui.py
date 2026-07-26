"""
左栏多标签组合筛选 UI 逻辑测试（T03 / P0-3）

验证 TagFilterWidget 多选 + AND/OR 切换，以及经 build_tag_filter_parsed
构造出的 ParsedQuery 与搜索框 `tag:A OR tag:B` 语法同构。

用 offscreen Qt + 直接调用 _on_tag_clicked（绕过按钮态），聚焦多选集合与信号。
运行: python tests/test_tag_filter_ui.py
"""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from ui.tag_filter import TagFilterWidget  # noqa: E402
from core.query_parser import build_tag_filter_parsed, OrNode, AndNode  # noqa: E402


class TagFilterUiTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _widget(self):
        w = TagFilterWidget()
        w.update_tags({"ML": 2, "DL": 3, "CV": 1})
        return w

    def test_single_select_and(self):
        w = self._widget()
        w._on_tag_clicked("ML", True)
        self.assertEqual(w.selected_tags(), ["ML"])
        self.assertEqual(w.selected_op(), "AND")
        p = build_tag_filter_parsed(w.selected_tags(), w.selected_op())
        self.assertFalse(p.has_boolean)
        self.assertEqual(p.tag_filters, ["ML"])

    def test_multi_select_and(self):
        w = self._widget()
        w._on_tag_clicked("ML", True)
        w._on_tag_clicked("DL", True)
        self.assertEqual(w.selected_tags(), ["DL", "ML"])
        p = build_tag_filter_parsed(w.selected_tags(), w.selected_op())
        self.assertFalse(p.has_boolean)
        self.assertEqual(p.tag_filters, ["DL", "ML"])

    def test_deselect_removes(self):
        w = self._widget()
        w._on_tag_clicked("ML", True)
        w._on_tag_clicked("ML", False)
        self.assertEqual(w.selected_tags(), [])

    def test_or_mode(self):
        w = self._widget()
        w.set_op("OR")
        w._on_tag_clicked("ML", True)
        w._on_tag_clicked("DL", True)
        self.assertEqual(w.selected_op(), "OR")
        p = build_tag_filter_parsed(w.selected_tags(), w.selected_op())
        self.assertTrue(p.has_boolean)
        self.assertIsInstance(p.expr, OrNode)

    def test_signal_emits_list_op(self):
        w = self._widget()
        received = []
        w.tags_selected.connect(lambda tags, op: received.append((tags, op)))
        w._on_tag_clicked("DL", True)
        self.assertEqual(received, [(["DL"], "AND")])

    def test_old_signal_compat(self):
        w = self._widget()
        received = []
        w.tag_selected.connect(lambda t: received.append(t))
        w._on_tag_clicked("DL", True)
        self.assertEqual(received, ["DL"])

    def test_clear(self):
        w = self._widget()
        w._on_tag_clicked("ML", True)
        w.clear_selection()
        self.assertEqual(w.selected_tags(), [])

    def test_set_op_with_selection_refreshes(self):
        w = self._widget()
        w._on_tag_clicked("ML", True)
        received = []
        w.tags_selected.connect(lambda tags, op: received.append((tags, op)))
        w.set_op("OR")
        self.assertEqual(received[-1], (["ML"], "OR"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
