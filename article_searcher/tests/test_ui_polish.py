"""
UI 渲染打磨回归测试（UI-P1 / UI-P2）。

目标：回归验证两个 UI 渲染修复——
- UI-P1：左侧栏操作按钮（批量操作条 BatchActionBar、主题簇重新聚类按钮）
  文字不再被过小的固定高度裁切（setFixedHeight → setMinimumHeight，按内容自然撑开）。
- UI-P2：思维导图缩放按钮具备 tooltip 描述（放大 / 缩小 / 适应窗口）。

设计要点：
- offscreen Qt 平台；
- 用 QFontMetrics 的 sizeHint 与实际渲染几何对比，检测纵向/横向裁切；
- 不依赖真实引擎/索引，纯 UI 几何与 tooltip 校验。
"""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QPushButton  # noqa: E402

from ui.styles import DARK_THEME  # noqa: E402
from ui.batch_action_bar import BatchActionBar  # noqa: E402
from ui.cluster_panel import ClusterPanel  # noqa: E402
from ui.mindmap_viewer import MindMapViewer  # noqa: E402


class UIPolishTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyleSheet(DARK_THEME)

    def test_batch_action_bar_text_not_clipped(self):
        """批量操作条按钮文字不应被固定高度裁切（UI-P1）。"""
        bar = BatchActionBar()
        bar.set_targets(["a.md", "b.md", "c.md"])
        bar.show()
        for b in bar.findChildren(QPushButton):
            sh = b.sizeHint()
            self.assertGreaterEqual(
                b.height(), sh.height(),
                f"批量操作条按钮 '{b.text()}' 高度 {b.height()} < 内容高度 {sh.height()}（纵向裁切）"
            )
            self.assertGreaterEqual(
                b.width(), sh.width(),
                f"批量操作条按钮 '{b.text()}' 宽度 {b.width()} < 内容宽度 {sh.width()}（横向裁切）"
            )

    def test_cluster_recluster_text_not_clipped(self):
        """主题簇「重新聚类」按钮文字不应被固定高度裁切（UI-P1）。"""
        cp = ClusterPanel(object())
        cp.show()
        rb = cp.recluster_btn
        sh = rb.sizeHint()
        self.assertGreaterEqual(
            rb.height(), sh.height(),
            f"重新聚类按钮高度 {rb.height()} < 内容高度 {sh.height()}（纵向裁切）"
        )

    def test_mindmap_zoom_tooltips(self):
        """思维导图缩放按钮应具备 tooltip 描述（UI-P2）。"""
        mm = MindMapViewer()
        mm.show()
        expect = {"+": "放大", "-": "缩小", "适应": "适应窗口"}
        for b in mm.findChildren(QPushButton):
            t = b.text()
            if t in expect:
                self.assertEqual(
                    b.toolTip(), expect[t],
                    f"思维导图按钮 '{t}' 的 tooltip 应为 '{expect[t]}'"
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
