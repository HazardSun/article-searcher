"""
链接图谱面板（ui/link_graph_panel.py）UI 回归测试。

目标：回归验证 UI-002 修复后，`_fill_out` / `_fill_in` 完整填充路径
（含 _size_item 新签名：接收 list_widget，用 list_widget.viewport().width()，
并对 setSizeHint 包裹 QSize）不再抛出 TypeError / AttributeError，
且出链 / 入链列表被正确填充。

设计要点：
- offscreen Qt 平台；
- 最小 FakeEngine（仅被 __init__ 持有；本测试直接喂 graph，不触发 load/build_link_graph）；
- FakeNode / FakeEdge / FakeGraph 模拟 core.link_graph 的 LinkGraph 结构
  （nodes: {path: node(.title)}；edges: [{source, target, line, context}]）。
"""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from ui.link_graph_panel import LinkGraphPanel  # noqa: E402


class FakeNode:
    def __init__(self, title):
        self.title = title


class FakeEdge:
    def __init__(self, source, target, line, context):
        self.source = source
        self.target = target
        self.line = line
        self.context = context


class FakeGraph:
    def __init__(self, nodes, edges):
        self.nodes = nodes
        self.edges = edges


class FakeEngine:
    """最小 engine 桩；本测试不调用 build_link_graph。"""

    def build_link_graph(self):
        raise AssertionError("本测试不应触发真实 build_link_graph")


class LinkGraphPanelUITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_fill_out_in_without_typeerror(self):
        """_fill_out / _fill_in 完整路径：不抛 TypeError/AttributeError，且列表有填充。"""
        panel = LinkGraphPanel(FakeEngine(), None)
        center = "A/center.md"
        nodes = {
            center: FakeNode("Center"),
            "A/out.md": FakeNode("Out Article"),
            "B/in.md": FakeNode("In Article"),
        }
        # 2 条边：一条出链（source=center）、一条入链（target=center）
        edges = [
            FakeEdge(center, "A/out.md", 10,
                     "正文参见 [[Out Article]] 了解更多细节内容"),
            FakeEdge("B/in.md", center, 3,
                     "在 [[Center]] 一文中被引用，见上方说明"),
        ]
        graph = FakeGraph(nodes, edges)
        panel._graph = graph

        try:
            panel._fill_out(center)
            panel._fill_in(center)
        except (TypeError, AttributeError) as exc:
            self.fail(
                f"_fill_out/_fill_in 抛出 {type(exc).__name__}（UI-002 崩溃复现）: {exc}"
            )

        # 出链列表应有 1 项（source == center），入链列表应有 1 项（target == center）
        self.assertEqual(panel.out_list.count(), 1, "出链列表填充数量不符")
        self.assertEqual(panel.in_list.count(), 1, "入链列表填充数量不符")
        # 标签应显示计数
        self.assertIn("出链 1", panel.out_label.text())
        self.assertIn("入链 1", panel.in_label.text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
