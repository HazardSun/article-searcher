"""
链接图谱查看器（功能6 可视化 / P2-6）

独立 `LinkGraphViewer`：封装 QGraphicsScene/View，绘制文章节点（紫色系圆角）
与有向引用边（曲线 + 箭头，复用 MindMapViewer 的边视觉语言）。
- 悬挂（dangling）节点灰色；
- 布局：圆形环初始化 + 轻力导向松弛；
- `set_theme` 双主题（复用 MindMapViewer 思路，新增 objectName=link_graph）；
- `node_clicked(path)` 信号：点击节点回传目标文章 path；
- `highlight_neighbors(path)`：高亮某文章的入/出链邻居并淡化其余；
- 缩放/适应/拖拽 复用 MindMapViewer 交互；
- 节点上限（_MAX_NODES）防超大库渲染卡顿。
"""

import math
from typing import Dict, List, Optional, Tuple

from PyQt6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsItem,
    QVBoxLayout, QWidget, QPushButton, QHBoxLayout, QLabel,
)
from PyQt6.QtCore import Qt, QRectF, QPointF, pyqtSignal
from PyQt6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QFontMetrics,
    QPainterPath, QWheelEvent, QMouseEvent, QPolygonF,
)


class LinkGraphNodeItem(QGraphicsItem):
    """链接图谱节点（文章 / 悬挂）。"""

    def __init__(self, path: str, title: str, kind: str = "article", parent=None):
        super().__init__(parent)
        self.path = path
        self.title = title
        self.kind = kind          # 'article' | 'missing'
        self._highlighted = False
        self._dimmed = False
        self._hovered = False
        self.on_click = None       # 由 viewer 注入：点击回传
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setAcceptHoverEvents(True)

    def _colors(self):
        """返回 (bg, text, border) QColor，按主题/状态/类型。"""
        is_light = getattr(self, "_theme", "dark") == "light"
        if self.kind == "missing":
            if is_light:
                return QColor(203, 213, 225), QColor(71, 85, 105), QColor(148, 163, 184)
            return QColor(40, 40, 50), QColor(148, 163, 184), QColor(58, 58, 74)
        # 文章：紫色系（与簇 #7c3aed / 相关 #a78bfa 协调）
        if is_light:
            return QColor(124, 58, 237), QColor(255, 255, 255), QColor(91, 33, 184)
        return QColor(139, 92, 246), QColor(255, 255, 255), QColor(167, 139, 250)

    def set_theme(self, theme: str):
        self._theme = theme if theme in ("dark", "light") else "dark"

    def set_highlighted(self, on: bool):
        self._highlighted = on
        self.update()

    def set_dimmed(self, on: bool):
        self._dimmed = on
        self.setOpacity(0.22 if on else 1.0)
        self.update()

    def boundingRect(self) -> QRectF:
        font = QFont("Segoe UI", 11, 600)
        fm = QFontMetrics(font)
        text_width = fm.horizontalAdvance(self.title)
        padding = 18
        w = max(text_width + padding * 2, 64)
        h = 26
        return QRectF(-w / 2, -h / 2, w, h)

    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        rect = self.boundingRect()
        bg, text, border = self._colors()
        if self._hovered:
            bg = bg.lighter(118)
        if self._highlighted:
            border = QColor(255, 255, 255) if self._theme == "dark" else QColor(15, 23, 42)
            bg = bg.lighter(125)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(bg))
        painter.drawRoundedRect(rect, 12, 12)

        pen = QPen(border, 2 if self._highlighted else 1)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(-1, -1, 1, 1), 12, 12)

        font = QFont("Segoe UI", 11, 600)
        painter.setFont(font)
        painter.setPen(QPen(text))
        painter.drawText(
            rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.title)

    def hoverEnterEvent(self, event):
        self._hovered = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._hovered = False
        self.update()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent):
        if self.on_click is not None:
            self.on_click(self.path)
        super().mousePressEvent(event)


class LinkGraphEdgeItem(QGraphicsItem):
    """链接图谱有向边（引用曲线 + 箭头）。"""

    def __init__(self, start_item: "LinkGraphNodeItem",
                 end_item: "LinkGraphNodeItem", link_type: str = "relpath",
                 parent=None):
        super().__init__(parent)
        self._start = start_item
        self._end = end_item
        self.link_type = link_type
        self._highlighted = False
        self.setZValue(-1)

    def _color(self):
        is_light = getattr(self, "_theme", "dark") == "light"
        if self.link_type == "wikilink":
            return QColor(167, 139, 250, 170) if not is_light else QColor(124, 58, 237, 200)
        return QColor(139, 92, 246, 150) if not is_light else QColor(91, 33, 184, 180)

    def set_theme(self, theme: str):
        self._theme = theme if theme in ("dark", "light") else "dark"

    def set_highlighted(self, on: bool):
        self._highlighted = on
        self.update()

    def boundingRect(self) -> QRectF:
        extra = 24
        return QRectF(
            min(self._start.x(), self._end.x()) - extra,
            min(self._start.y(), self._end.y()) - extra,
            abs(self._end.x() - self._start.x()) + extra * 2,
            abs(self._end.y() - self._start.y()) + extra * 2,
        )

    def _path(self) -> QPainterPath:
        start = QPointF(self._start.x(), self._start.y())
        end = QPointF(self._end.x(), self._end.y())
        dx = end.x() - start.x()
        dy = end.y() - start.y()
        # 终点回缩到节点边缘（避免箭头被节点覆盖）
        length = math.hypot(dx, dy) or 1.0
        shrink = 26
        ex = end.x() - dx / length * shrink
        ey = end.y() - dy / length * shrink
        end_p = QPointF(ex, ey)
        cx1 = start.x() + dx * 0.4
        cy1 = start.y()
        cx2 = ex - dx * 0.4
        cy2 = ey
        path = QPainterPath()
        path.moveTo(start)
        path.cubicTo(QPointF(cx1, cy1), QPointF(cx2, cy2), end_p)
        return path

    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = self._color()
        if self._highlighted:
            color = QColor(255, 255, 255) if self._theme == "dark" else QColor(15, 23, 42)
        width = 2.2 if self._highlighted else 1.4
        pen = QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)

        path = self._path()
        painter.drawPath(path)

        # 箭头
        pt = path.pointAtPercent(1.0)
        tang = path.angleAtPercent(1.0)
        ang = math.radians(tang)
        size = 9
        p1 = pt + QPointF(math.cos(ang + math.pi * 0.85) * size,
                             -math.sin(ang + math.pi * 0.85) * size)
        p2 = pt + QPointF(math.cos(ang - math.pi * 0.85) * size,
                             -math.sin(ang - math.pi * 0.85) * size)
        arrow = QPolygonF([pt, p1, p2])
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(arrow)


# 节点上限：防止超大库渲染卡顿（超出仅渲染前 N 个并提示）
_MAX_NODES = 500


class LinkGraphViewer(QWidget):
    """链接图谱查看器（独立组件，不复用 MindMapViewer 树结构）。"""

    node_clicked = pyqtSignal(str)   # 点击节点 -> 目标文章 path

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = None
        self._view = None
        self._theme = "dark"
        self._graph = None
        self._node_items: Dict[str, LinkGraphNodeItem] = {}
        self._edge_items: List[LinkGraphEdgeItem] = []
        self._title_label = None
        self._setup_ui()

    def set_theme(self, theme: str):
        self._theme = theme if theme in ("dark", "light") else "dark"
        if self._scene is None or self._view is None:
            return
        if self._theme == "light":
            self._scene.setBackgroundBrush(QColor(248, 249, 250))
            self._view.setStyleSheet(
                "QGraphicsView{border:1px solid #e2e8f0;border-radius:12px;"
                "background-color:#f8f9fa;}")
        else:
            self._scene.setBackgroundBrush(QColor(18, 18, 24))
            self._view.setStyleSheet(
                "QGraphicsView{border:1px solid #2a2a35;border-radius:12px;"
                "background-color:#121218;}")

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        toolbar = QHBoxLayout()
        self._title_label = QLabel("链接图谱")
        self._title_label.setObjectName("section")
        toolbar.addWidget(self._title_label)
        toolbar.addStretch()

        zoom_in = QPushButton("+")
        zoom_in.setFixedWidth(32)
        zoom_in.clicked.connect(self._zoom_in)
        toolbar.addWidget(zoom_in)

        zoom_out = QPushButton("-")
        zoom_out.setFixedWidth(32)
        zoom_out.clicked.connect(self._zoom_out)
        toolbar.addWidget(zoom_out)

        fit_btn = QPushButton("适应")
        fit_btn.clicked.connect(self._fit_view)
        toolbar.addWidget(fit_btn)
        layout.addLayout(toolbar)

        self._scene = QGraphicsScene()
        self._scene.setBackgroundBrush(QColor(18, 18, 24))
        self._view = QGraphicsView(self._scene)
        self._view.setObjectName("link_graph")
        self._view.setRenderHints(
            QPainter.RenderHint.Antialiasing |
            QPainter.RenderHint.TextAntialiasing |
            QPainter.RenderHint.SmoothPixmapTransform
        )
        self._view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self._view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self._view.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self._view.setStyleSheet(
            "QGraphicsView{border:1px solid #2a2a35;border-radius:12px;"
            "background-color:#121218;}")
        layout.addWidget(self._view)

    # ------------------------------------------------------------------ #
    # 布局：圆形环初始化 + 轻力导向松弛
    # ------------------------------------------------------------------ #
    @staticmethod
    def _layout(graph, nodes: List[str]) -> Dict[str, Tuple[float, float]]:
        n = len(nodes)
        if n == 0:
            return {}
        radius = max(220, 46 * n)
        pos: Dict[str, Tuple[float, float]] = {}
        for i, key in enumerate(nodes):
            ang = 2 * math.pi * i / n
            pos[key] = [radius * math.cos(ang), radius * math.sin(ang)]

        # 邻接（无向）用于弹簧力
        adj: Dict[str, set] = {k: set() for k in nodes}
        for e in graph.edges:
            if e.source in adj and e.target in adj:
                adj[e.source].add(e.target)
                adj[e.target].add(e.source)

        # 轻力导向：若干次迭代（O(n^2)，上限 500 节点可接受）
        iters = 50 if n <= 200 else 30
        for _ in range(iters):
            disp: Dict[str, List[float]] = {k: [0.0, 0.0] for k in nodes}
            for a in nodes:
                for b in nodes:
                    if a == b:
                        continue
                    dx = pos[a][0] - pos[b][0]
                    dy = pos[a][1] - pos[b][1]
                    d2 = dx * dx + dy * dy + 0.01
                    rep = 6000.0 / d2
                    d = math.sqrt(d2)
                    disp[a][0] += rep * dx / d
                    disp[a][1] += rep * dy / d
            for a in nodes:
                for b in adj[a]:
                    dx = pos[a][0] - pos[b][0]
                    dy = pos[a][1] - pos[b][1]
                    d = math.sqrt(dx * dx + dy * dy) + 0.01
                    att = d * d / 900.0
                    disp[a][0] -= att * dx / d
                    disp[a][1] -= att * dy / d
            for k in nodes:
                pos[k][0] += max(-28, min(28, disp[k][0]))
                pos[k][1] += max(-28, min(28, disp[k][1]))
        return pos

    def display_graph(self, graph):
        """绘制图谱（节点=文章，有向边=引用）。"""
        self._graph = graph
        self._scene.clear()
        self._node_items = {}
        self._edge_items = []

        nodes = list(graph.nodes.keys())
        truncated = False
        if len(nodes) > _MAX_NODES:
            nodes = nodes[:_MAX_NODES]
            truncated = True

        pos = self._layout(graph, nodes)

        for key in nodes:
            node = graph.nodes[key]
            item = LinkGraphNodeItem(node.path, node.title, node.kind)
            item.set_theme(self._theme)
            p = pos.get(key, (0.0, 0.0))
            item.setPos(p[0], p[1])
            item.on_click = self._on_node_clicked
            self._scene.addItem(item)
            self._node_items[key] = item

        for e in graph.edges:
            si = self._node_items.get(e.source)
            ti = self._node_items.get(e.target)
            if si is None or ti is None:
                continue
            edge = LinkGraphEdgeItem(si, ti, e.link_type)
            edge.set_theme(self._theme)
            self._scene.addItem(edge)
            self._edge_items.append(edge)

        total = len(graph.nodes)
        shown = len(nodes)
        self._title_label.setText(
            f"链接图谱 - {total} 节点 / {len(graph.edges)} 边"
            + (f"（已截断至 {shown}）" if truncated else "")
        )
        self._fit_view()

    def _on_node_clicked(self, path: str):
        self.node_clicked.emit(path)

    def highlight_neighbors(self, path: str):
        """高亮 path 的入/出链邻居，淡化其余。"""
        if not self._graph:
            return
        neighbors: set = {path}
        for e in self._graph.edges:
            if e.source == path:
                neighbors.add(e.target)
            if e.target == path:
                neighbors.add(e.source)

        for key, item in self._node_items.items():
            is_nb = key in neighbors
            item.set_dimmed(not is_nb)
            item.set_highlighted(key == path)
        for edge in self._edge_items:
            involves = (edge._start.path == path or edge._end.path == path)
            edge.set_highlighted(involves)

    def clear_highlight(self):
        for item in self._node_items.values():
            item.set_dimmed(False)
            item.set_highlighted(False)
        for edge in self._edge_items:
            edge.set_highlighted(False)

    def _zoom_in(self):
        self._view.scale(1.2, 1.2)

    def _zoom_out(self):
        self._view.scale(0.8, 0.8)

    def _fit_view(self):
        if self._node_items:
            self._view.fitInView(
                self._scene.itemsBoundingRect(),
                Qt.AspectRatioMode.KeepAspectRatio,
            )
            self._view.scale(0.9, 0.9)

    def wheelEvent(self, event: QWheelEvent):
        if event.angleDelta().y() > 0:
            self._view.scale(1.1, 1.1)
        else:
            self._view.scale(0.9, 0.9)
        event.accept()
