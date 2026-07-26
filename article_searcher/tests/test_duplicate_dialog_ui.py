"""
近似 / 重复文章检测对话框（ui/duplicate_dialog.py）UI 回归测试。

目标：回归验证 UI-001 修复后，`_on_ready` 走完整填充路径
（含 _size_item 新签名：接收 list_widget，用 list_widget.viewport().width()，
并对 setSizeHint 包裹 QSize）不再抛出 TypeError / AttributeError，
且结果列表被正确填充。

设计要点：
- offscreen Qt 平台（CI / 无显示环境可跑）；
- 最小 FakeEngine（仅被 __init__ 持有 + get_indexed_files 供 _refresh_badge 调用）；
- FakePair 模拟 find_duplicate_pairs 返回的对象（file_a / file_b / similarity）。
"""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 显式要求 offscreen 平台
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from ui.duplicate_dialog import DuplicateDialog  # noqa: E402


class FakePair:
    """模拟 core.dedup.DuplicatePair（仅含 UI 读取的字段）。"""

    def __init__(self, file_a, file_b, similarity):
        self.file_a = file_a
        self.file_b = file_b
        self.similarity = similarity


class FakeEngine:
    """最小 engine 桩：DuplicateDialog 构造期仅持有 engine，
    并在 _refresh_badge 中调用 get_indexed_files（已 try/except 保护）。"""

    def get_indexed_files(self):
        return []


class DuplicateDialogUITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 整个测试类共用一个 QApplication 实例
        cls.app = QApplication.instance() or QApplication([])

    def test_on_ready_fills_without_typeerror(self):
        """_on_ready 完整路径：不抛 TypeError/AttributeError，且列表有填充项。"""
        dlg = DuplicateDialog(FakeEngine(), "dark", None)
        pairs = [
            FakePair("A/文章一.md", "A/文章二.md", 1.0),
            FakePair("B/笔记.md", "B/随笔.md", 0.92),
        ]
        try:
            dlg._on_ready(pairs)
        except (TypeError, AttributeError) as exc:
            self.fail(
                f"_on_ready 抛出了 {type(exc).__name__}（UI-001 崩溃复现）: {exc}"
            )

        # 列表应被填充，且首条带 file_a/file_b（UserRole）
        self.assertGreater(dlg.result_list.count(), 0, "结果列表未被填充")
        data = dlg.result_list.item(0).data(Qt.ItemDataRole.UserRole)
        self.assertIsNotNone(data, "列表项缺少 UserRole 数据")
        self.assertIn("file_a", data)
        self.assertIn("file_b", data)

    def test_on_ready_empty_no_crash(self):
        """空结果同样走完整路径，不抛异常并给出友好提示。"""
        dlg = DuplicateDialog(FakeEngine(), "dark", None)
        try:
            dlg._on_ready([])
        except (TypeError, AttributeError) as exc:
            self.fail(
                f"空结果下 _on_ready 抛出 {type(exc).__name__}（UI-001 复现）: {exc}"
            )
        # 空结果显示「未找到」占位项
        self.assertGreaterEqual(dlg.result_list.count(), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
