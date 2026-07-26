"""
状态 / 概览健壮性（UI-004）回归测试。

目标：回归验证 UI-004 修复后，状态读取与概览渲染对「缺失可选键」的 status
字典具备健壮性，不再抛出 KeyError：

- MainWindow._update_status()：从 engine.get_status() 读取，缺失可选键时应
  以 .get(..., default) 兜底，不抛 KeyError；
- MainWindow._on_indexing_finished(stats)：stats 缺失可选键时以 .get 兜底，
  不抛 KeyError；
- DashboardDialog({}) 构造 + _fill()：空 status 字典渲染不抛 KeyError。

设计要点：
- offscreen Qt 平台；
- 用 tests._ui_fakes.FlexEngine（不加载 chromadb / onnx），
  并通过构造参数注入「缺失可选键」的 status 字典；
- DashboardDialog 仅依赖 status 字典，独立构造验证。
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from ui.main_window import MainWindow  # noqa: E402
from ui.dashboard_dialog import DashboardDialog  # noqa: E402
from core.config import ConfigStore  # noqa: E402
from tests._ui_fakes import FlexEngine  # noqa: E402


# 故意缺失大量可选键，仅保留极少数，用于验证 .get 兜底健壮性
_PARTIAL_STATUS = {
    "indexed_files": 7,
    # 缺失：embedding_model / total_chunks / sources / starred_count /
    #       tag_distribution / cluster_distribution / recent_updates ...
}

# 故意缺失大量可选键，仅保留 total_files，验证 _on_indexing_finished .get 兜底
_PARTIAL_STATS = {
    "total_files": 3,
    # 缺失：total_chunks / sources / new_files / updated_files /
    #       orphans_removed / errors ...
}


class StatusGetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _make_window(self, status):
        tmp = tempfile.mkdtemp()
        cs = ConfigStore(tmp)
        cs.update(auto_index_enabled=False)
        eng = FlexEngine(status=status)
        return MainWindow(eng, cs)

    def test_dashboard_empty_no_keyerror(self):
        """DashboardDialog({}) 构造 + _fill() 不应抛 KeyError。"""
        try:
            dlg = DashboardDialog({})
            dlg._fill()  # 再次显式触发，确保覆盖
        except KeyError as exc:
            self.fail(f"DashboardDialog 空 status 渲染抛出 KeyError（UI-004 复现）: {exc}")
        self.assertIsNotNone(dlg)

    def test_update_status_missing_keys_no_keyerror(self):
        """_update_status 在 status 缺键时不应抛 KeyError。"""
        win = self._make_window(_PARTIAL_STATUS)
        try:
            win._update_status()
        except KeyError as exc:
            self.fail(f"_update_status 抛出 KeyError（UI-004 复现）: {exc}")
        # 硬件标签应已被写入（非空），证明 _update_status 走完
        self.assertTrue(win.hardware_label.text(), "_update_status 未写入硬件标签")

    def test_on_indexing_finished_missing_keys_no_keyerror(self):
        """_on_indexing_finished 在 stats 缺键时不应抛 KeyError。"""
        win = self._make_window(_PARTIAL_STATUS)
        try:
            win._on_indexing_finished(_PARTIAL_STATS)
        except KeyError as exc:
            self.fail(f"_on_indexing_finished 抛出 KeyError（UI-004 复现）: {exc}")
        # 索引完成后「管理索引源」按钮应被重新启用（证明方法走完）
        self.assertTrue(win.folder_btn.isEnabled(),
                        "_on_indexing_finished 未重新启用索引源按钮")


if __name__ == "__main__":
    unittest.main(verbosity=2)
