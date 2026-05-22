"""
标签筛选组件 - 流式布局（优化版）
支持搜索过滤、展开/折叠、数量徽标
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QLayout, QFrame, QLineEdit
)
from PyQt6.QtCore import Qt, QRect, QPoint, QSize, pyqtSignal


class FlowLayout(QLayout):
    """流式布局 - 自动换行排列子组件"""

    def __init__(self, parent=None, margin=0, hspacing=6, vspacing=6):
        super().__init__(parent)
        self._hspacing = hspacing
        self._vspacing = vspacing
        self._items = []
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def _do_layout(self, rect, test_only):
        m = self.contentsMargins()
        x = rect.x() + m.left()
        y = rect.y() + m.top()
        line_height = 0
        hsp = self._hspacing
        vsp = self._vspacing
        right = rect.right() - m.right()

        for item in self._items:
            wid = item.widget()
            if wid and not wid.isVisible():
                continue
            hint = item.sizeHint()
            next_x = x + hint.width() + hsp
            if next_x - hsp > right and line_height > 0:
                x = rect.x() + m.left()
                y += line_height + vsp
                next_x = x + hint.width() + hsp
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())

        return y + line_height - rect.y() + m.bottom()


class TagFilterWidget(QWidget):
    """标签筛选组件 - 流式排列、搜索过滤、展开折叠"""

    tag_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selected_tag = None
        self._tag_buttons = {}
        self._all_tags = []
        self._collapsed = False
        self._show_all = False
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(4)

        self._setup_header(layout)
        self._setup_search(layout)
        self._setup_tag_area(layout)

    def _setup_header(self, layout):
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)

        self.label = QLabel("标签筛选")
        self.label.setStyleSheet("font-size: 14px; font-weight: 600; color: #a78bfa; padding: 0;")

        self.count_label = QLabel("")
        self.count_label.setStyleSheet("font-size: 11px; color: #6a6a7a; padding: 0;")

        self.clear_btn = QPushButton("清除")
        self.clear_btn.setObjectName("filter_action")
        self.clear_btn.setFixedSize(48, 24)
        self.clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_btn.clicked.connect(self.clear_selection)
        self.clear_btn.hide()

        self._toggle_btn = QPushButton("收起")
        self._toggle_btn.setObjectName("filter_action")
        self._toggle_btn.setFixedSize(56, 24)
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.clicked.connect(self._toggle_collapse)

        header.addWidget(self.label)
        header.addSpacing(4)
        header.addWidget(self.count_label)
        header.addStretch()
        header.addWidget(self.clear_btn)
        header.addSpacing(4)
        header.addWidget(self._toggle_btn)
        layout.addLayout(header)

    def _setup_search(self, layout):
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("搜索标签...")
        self._search_input.setObjectName("tag_search")
        self._search_input.setFixedHeight(30)
        self._search_input.setClearButtonEnabled(True)
        self._search_input.textChanged.connect(self._on_search_changed)
        layout.addWidget(self._search_input)

    def _setup_tag_area(self, layout):
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self._container = QWidget()
        self._container.setStyleSheet("background: transparent;")
        self._flow = FlowLayout(self._container, margin=4, hspacing=6, vspacing=6)
        self._scroll.setWidget(self._container)
        layout.addWidget(self._scroll)

    def _on_search_changed(self, text: str):
        text = text.strip().lower()
        tag_area_visible = not self._collapsed
        for tag, btn in self._tag_buttons.items():
            visible = (not text or text in tag.lower()) and tag_area_visible
            btn.setVisible(visible)

    def update_tags(self, tags: dict):
        self._tag_buttons.clear()

        while self._flow.count():
            item = self._flow.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        sorted_tags = sorted(tags.items(), key=lambda x: x[1], reverse=True)
        self._all_tags = [t for t, _ in sorted_tags]
        total = len(sorted_tags)

        self.label.setText(f"标签筛选")
        self.count_label.setText(f"· {total} 标签 · {sum(c for _, c in sorted_tags)} 文件" if total else "")

        for tag, count in sorted_tags:
            btn = QPushButton(f"{tag}  {count}")
            btn.setCheckable(True)
            btn.setFixedHeight(28)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, t=tag: self._on_tag_clicked(t, checked))
            self._flow.addWidget(btn)
            self._tag_buttons[tag] = btn

        if self._collapsed:
            self._scroll.setVisible(False)
            self._search_input.setVisible(False)

    def _on_tag_clicked(self, tag: str, checked: bool):
        if checked:
            for other_tag, btn in self._tag_buttons.items():
                if other_tag != tag:
                    btn.setChecked(False)
            self._selected_tag = tag
            self.clear_btn.show()
            self.tag_selected.emit(tag)
        else:
            self._selected_tag = None
            self.clear_btn.hide()
            self.tag_selected.emit("")

    def _toggle_collapse(self):
        self._collapsed = not self._collapsed
        visible = not self._collapsed
        self._scroll.setVisible(visible)
        self._search_input.setVisible(visible)
        self.clear_btn.setVisible(visible and self._selected_tag is not None)
        self._toggle_btn.setText("收起" if visible else "展开")

        if visible:
            self._on_search_changed(self._search_input.text())

    def clear_selection(self):
        self._selected_tag = None
        for btn in self._tag_buttons.values():
            btn.setChecked(False)
        self.clear_btn.hide()
        self.tag_selected.emit("")

    @property
    def selected_tag(self) -> str:
        return self._selected_tag or ""
