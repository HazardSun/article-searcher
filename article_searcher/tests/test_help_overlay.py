"""
帮助 / 语法速查浮层测试（T05 / P0-1）

验证 HelpOverlay 可构建，且静态速查文本包含关键语法 token：
  tag: / path: / -排除 / "短语" 与布尔组合示例（AND / OR / NOT / 括号）。
运行: python tests/test_help_overlay.py
"""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from ui.help_overlay import HelpOverlay  # noqa: E402


class HelpOverlayTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_cheatsheet_has_filter_tokens(self):
        sheet = HelpOverlay.SYNTAX_CHEATSHEET
        for token in ("tag:", "path:", "-", '"'):
            self.assertIn(token, sheet, f"速查文本缺少关键 token: {token}")

    def test_cheatsheet_has_boolean_keywords(self):
        sheet = HelpOverlay.SYNTAX_CHEATSHEET
        for kw in ("AND", "OR", "NOT"):
            self.assertIn(kw, sheet, f"速查文本缺少布尔关键字: {kw}")

    def test_cheatsheet_has_paren_example(self):
        sheet = HelpOverlay.SYNTAX_CHEATSHEET
        self.assertIn("(", sheet)
        self.assertIn(")", sheet)

    def test_build_dialog(self):
        dlg = HelpOverlay()
        self.assertTrue(dlg.windowTitle())
        self.assertIsInstance(dlg, HelpOverlay)


if __name__ == "__main__":
    unittest.main(verbosity=2)
