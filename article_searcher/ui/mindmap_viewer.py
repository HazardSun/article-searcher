"""
思维导图查看器组件
使用 QGraphicsScene 渲染交互式思维导图
"""

import math
from PyQt6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsItem,
    QVBoxLayout, QWidget, QPushButton, QHBoxLayout, QLabel
)
from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QFontMetrics,
    QPainterPath, QWheelEvent, QMouseEvent
)


class MindMapNodeItem(QGraphicsItem):
    """思维导图节点图形项"""

    def __init__(self, label: str, level: int, weight: float = 1.0, parent=None):
        super().__init__(parent)
        self.label = label
        self.level = level
        self.weight = weight
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setAcceptHoverEvents(True)
        self._hovered = False
        self._children_items = []
        self._parent_item = None

        self._setup_appearance()

    def _setup_appearance(self):
        """根据层级设置外观"""
        if self.level == 0:
            self._bg_color = QColor(99, 102, 241)
            self._text_color = QColor(255, 255, 255)
            self._font_size = 14
            self._font_weight = 700
            self._radius = 12
        elif self.level == 1:
            self._bg_color = QColor(139, 92, 246)
            self._text_color = QColor(255, 255, 255)
            self._font_size = 12
            self._font_weight = 600
            self._radius = 10
        elif self.level == 2:
            self._bg_color = QColor(167, 139, 250)
            self._text_color = QColor(255, 255, 255)
            self._font_size = 11
            self._font_weight = 500
            self._radius = 8
        else:
            self._bg_color = QColor(196, 181, 253)
            self._text_color = QColor(20, 20, 30)
            self._font_size = 10
            self._font_weight = 400
            self._radius = 6

    def add_child(self, child: 'MindMapNodeItem'):
        self._children_items.append(child)
        child._parent_item = self

    def boundingRect(self) -> QRectF:
        font = QFont('Segoe UI', self._font_size, self._font_weight)
        fm = QFontMetrics(font)
        text_width = fm.horizontalAdvance(self.label)
        padding = 20

        w = max(text_width + padding * 2, 60)
        h = self._font_size + padding

        return QRectF(-w / 2, -h / 2, w, h)

    def paint(
        self,
        painter: QPainter,
        option,
        widget=None
    ):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        rect = self.boundingRect()
        r = self._radius

        bg_color = QColor(self._bg_color)
        if self._hovered:
            bg_color = bg_color.lighter(120)
        if self.isSelected():
            bg_color = bg_color.lighter(140)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(bg_color))
        painter.drawRoundedRect(rect, r, r)

        if self.isSelected():
            pen = QPen(QColor(255, 255, 255), 2)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(-1, -1, 1, 1), r, r)

        font = QFont('Segoe UI', self._font_size, self._font_weight)
        painter.setFont(font)
        painter.setPen(QPen(self._text_color))
        painter.drawText(
            rect,
            Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
            self.label
        )

    def hoverEnterEvent(self, event):
        self._hovered = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._hovered = False
        self.update()
        super().hoverLeaveEvent(event)


class MindMapConnection(QGraphicsItem):
    """思维导图连接线"""

    def __init__(self, start_item: MindMapNodeItem, end_item: MindMapNodeItem, parent=None):
        super().__init__(parent)
        self._start_item = start_item
        self._end_item = end_item
        self.setZValue(-1)

        self._start_item.add_child(end_item)

    def boundingRect(self) -> QRectF:
        extra = 20
        return QRectF(
            min(self._start_item.x(), self._end_item.x()) - extra,
            min(self._start_item.y(), self._end_item.y()) - extra,
            abs(self._end_item.x() - self._start_item.x()) + extra * 2,
            abs(self._end_item.y() - self._start_item.y()) + extra * 2
        )

    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        start = QPointF(self._start_item.x(), self._start_item.y())
        end = QPointF(self._end_item.x(), self._end_item.y())

        level = self._end_item.level
        if level == 1:
            color = QColor(139, 92, 246, 180)
            width = 3
        elif level == 2:
            color = QColor(167, 139, 250, 150)
            width = 2
        else:
            color = QColor(196, 181, 253, 120)
            width = 1.5

        pen = QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)

        dx = end.x() - start.x()
        dy = end.y() - start.y()
        cx1 = start.x() + dx * 0.4
        cy1 = start.y()
        cx2 = end.x() - dx * 0.4
        cy2 = end.y()

        path = QPainterPath()
        path.moveTo(start)
        path.cubicTo(QPointF(cx1, cy1), QPointF(cx2, cy2), end)
        painter.drawPath(path)


class MindMapViewer(QWidget):
    """思维导图查看器"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = None
        self._view = None
        self._root_item = None
        self._all_nodes = []
        self._all_connections = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        toolbar = QHBoxLayout()

        self.title_label = QLabel("思维导图")
        self.title_label.setObjectName("section")
        self.title_label.setStyleSheet("font-size: 14px; padding: 0;")
        toolbar.addWidget(self.title_label)

        toolbar.addStretch()

        zoom_in_btn = QPushButton("+")
        zoom_in_btn.setFixedWidth(32)
        zoom_in_btn.clicked.connect(self._zoom_in)
        toolbar.addWidget(zoom_in_btn)

        zoom_out_btn = QPushButton("-")
        zoom_out_btn.setFixedWidth(32)
        zoom_out_btn.clicked.connect(self._zoom_out)
        toolbar.addWidget(zoom_out_btn)

        fit_btn = QPushButton("适应")
        fit_btn.clicked.connect(self._fit_view)
        toolbar.addWidget(fit_btn)

        layout.addLayout(toolbar)

        self._scene = QGraphicsScene()
        self._scene.setBackgroundBrush(QColor(18, 18, 24))

        self._view = QGraphicsView(self._scene)
        self._view.setRenderHints(
            QPainter.RenderHint.Antialiasing |
            QPainter.RenderHint.TextAntialiasing |
            QPainter.RenderHint.SmoothPixmapTransform
        )
        self._view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self._view.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.AnchorUnderMouse
        )
        self._view.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self._view.setStyleSheet("""
            QGraphicsView {
                border: 1px solid #2a2a35;
                border-radius: 12px;
                background-color: #121218;
            }
        """)

        layout.addWidget(self._view)

    def display_mindmap(self, root_node):
        """
        显示思维导图

        Args:
            root_node: MindMapNode 实例
        """
        self._scene.clear()
        self._all_nodes = []
        self._all_connections = []

        self._root_item = self._create_node_item(root_node, 0, 0)
        self._scene.addItem(self._root_item)
        self._all_nodes.append(self._root_item)

        self._layout_children(self._root_item, root_node)

        self.title_label.setText(f"思维导图 - {root_node.label}")
        self._fit_view()

    def _create_node_item(self, node_data, x: float, y: float) -> MindMapNodeItem:
        """创建节点图形项"""
        item = MindMapNodeItem(
            label=node_data.label,
            level=node_data.level,
            weight=node_data.weight
        )
        item.setPos(x, y)
        return item

    def _layout_children(self, parent_item: MindMapNodeItem, parent_data):
        """递归布局子节点"""
        children = parent_data.children
        if not children:
            return

        child_count = len(children)
        if child_count == 0:
            return

        parent_pos = parent_item.pos()

        if parent_data.level == 0:
            self._layout_root_children(parent_item, children)
        else:
            angle_step = math.pi / (child_count + 1)
            start_angle = -math.pi / 2 - (child_count - 1) * angle_step / 2

            for i, child_data in enumerate(children):
                angle = start_angle + i * angle_step
                distance = 180 if child_data.level <= 2 else 140

                x = parent_pos.x() + math.cos(angle) * distance
                y = parent_pos.y() + math.sin(angle) * distance

                child_item = self._create_node_item(child_data, x, y)
                conn = MindMapConnection(parent_item, child_item)
                self._scene.addItem(child_item)
                self._scene.addItem(conn)
                self._all_nodes.append(child_item)
                self._all_connections.append(conn)

                if child_data.children:
                    self._layout_children(child_item, child_data)

    def _layout_root_children(self, parent_item: MindMapNodeItem, children):
        """布局根节点的子节点（环形布局）"""
        child_count = len(children)
        if child_count == 0:
            return

        radius = 280
        angle_step = (2 * math.pi) / child_count
        start_angle = -math.pi / 2

        for i, child_data in enumerate(children):
            angle = start_angle + i * angle_step
            x = parent_item.x() + math.cos(angle) * radius
            y = parent_item.y() + math.sin(angle) * radius

            child_item = self._create_node_item(child_data, x, y)
            conn = MindMapConnection(parent_item, child_item)
            self._scene.addItem(child_item)
            self._scene.addItem(conn)
            self._all_nodes.append(child_item)
            self._all_connections.append(conn)

            if child_data.children:
                self._layout_sub_level_children(child_item, child_data, angle, radius)

    def _layout_sub_level_children(
        self,
        parent_item: MindMapNodeItem,
        parent_data,
        parent_angle: float,
        parent_radius: float
    ):
        """布局二级子节点（扇形展开）"""
        children = parent_data.children
        if not children:
            return

        child_count = len(children)
        spread = math.pi / 3
        start_angle = parent_angle - spread / 2
        angle_step = spread / max(child_count - 1, 1) if child_count > 1 else 0

        distance = 160

        for i, child_data in enumerate(children):
            angle = start_angle + i * angle_step if child_count > 1 else parent_angle

            x = parent_item.x() + math.cos(angle) * distance
            y = parent_item.y() + math.sin(angle) * distance

            child_item = self._create_node_item(child_data, x, y)
            conn = MindMapConnection(parent_item, child_item)
            self._scene.addItem(child_item)
            self._scene.addItem(conn)
            self._all_nodes.append(child_item)
            self._all_connections.append(conn)

    def _zoom_in(self):
        self._view.scale(1.2, 1.2)

    def _zoom_out(self):
        self._view.scale(0.8, 0.8)

    def _fit_view(self):
        if self._all_nodes:
            self._view.fitInView(
                self._scene.itemsBoundingRect(),
                Qt.AspectRatioMode.KeepAspectRatio
            )
            self._view.scale(0.9, 0.9)

    def wheelEvent(self, event: QWheelEvent):
        if event.angleDelta().y() > 0:
            self._view.scale(1.1, 1.1)
        else:
            self._view.scale(0.9, 0.9)
        event.accept()
