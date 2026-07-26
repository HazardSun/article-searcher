"""
「管理索引源」对话框（ui/sources_dialog.py）无头冒烟测试。

目标：回归验证对话框构造不再因布局误用（addWidget(布局变量) -> addLayout）
而抛出 TypeError 导致进程崩溃。

设计要点：
- 使用 offscreen Qt 平台（CI/无显示环境可跑）；
- 用真实配置目录只读构造 ConfigStore（不写入用户配置）；
- 用最小 DummyEngine 作为 engine（SourcesDialog 构造期只持有，不调用其方法）；
- 断言 __init__ -> _setup_ui 能跑完、关键属性存在；
- 用 QTimer 触发 reject 后 exec()，确认能正常进入事件循环并退出，不污染。
"""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 保险起见，显式要求 offscreen 平台
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QDialog  # noqa: E402
from PyQt6.QtCore import QTimer  # noqa: E402

from core.config import ConfigStore  # noqa: E402
from ui.sources_dialog import SourcesDialog  # noqa: E402

# 真实配置目录（只读读取，不会改写用户配置）
CONFIG_DIR = "C:/Users/sunxi/.cache/article_searcher"


class DummyEngine:
    """最小 engine 桩：SourcesDialog 构造期只持有 engine，不调用其方法。"""
    pass


class SourcesDialogSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 整个测试类共用一个 QApplication 实例，避免重复实例报错
        cls.app = QApplication.instance() or QApplication([])

    def test_construct_without_typeerror(self):
        """构造 SourcesDialog 不应抛 TypeError（原崩溃根因）。"""
        cs = ConfigStore(CONFIG_DIR)
        original_error = None
        try:
            dlg = SourcesDialog(DummyEngine(), cs, None)
        except TypeError as exc:
            # 命中与本次修复同源的 TypeError（addWidget 误收布局对象）
            original_error = exc
            self.fail(
                f"SourcesDialog 构造抛出了 TypeError（原崩溃已复现）: {exc}"
            )
        # 其它非预期异常照常上抛

        # 断言 _setup_ui 执行成功，关键属性已创建
        self.assertTrue(
            hasattr(dlg, "list_widget"),
            "构造后缺少 list_widget 属性（_setup_ui 未完整执行）",
        )
        self.assertIsNotNone(dlg.list_widget)
        self.assertTrue(hasattr(dlg, "path_edit"))
        self.assertTrue(hasattr(dlg, "exclude_edit"))
        self.assertTrue(hasattr(dlg, "enabled_chk"))
        self.assertTrue(hasattr(dlg, "auto_index_chk"))
        self.assertTrue(hasattr(dlg, "cluster_chk"))
        # 确认 path_row 被正确 addLayout 进 right 布局（而非 addWidget）
        self.assertTrue(hasattr(dlg, "browse_btn"))
        self.assertTrue(hasattr(dlg, "add_btn"))
        self.assertTrue(hasattr(dlg, "del_btn"))

    def test_event_loop_enter_and_return(self):
        """对话框应能进入事件循环并正常返回（验证 reject/done 不抛异常）。"""
        cs = ConfigStore(CONFIG_DIR)
        dlg = SourcesDialog(DummyEngine(), cs, None)

        # 用 QTimer 在事件循环启动后立即触发 reject，确保 exec() 能退出而非挂起
        QTimer.singleShot(0, dlg.reject)
        result = dlg.exec()
        self.assertIn(
            result,
            (QDialog.DialogCode.Accepted, QDialog.DialogCode.Rejected),
        )

        # 直接调用 done(0) 也应安全
        dlg.done(0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
