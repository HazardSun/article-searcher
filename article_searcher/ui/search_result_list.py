"""
搜索结果列表组件
"""

from PyQt6.QtWidgets import (
    QListWidget, QListWidgetItem, QVBoxLayout, QWidget, QLabel
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont


class SearchResultItem(QListWidgetItem):
    """搜索结果列表项"""

    def __init__(self, result: dict):
        super().__init__()
        self.result = result
        self._setup_display()

    def _setup_display(self):
        """设置显示内容"""
        meta = self.result.get('metadata', {})
        similarity = self.result.get('similarity', 0)

        file_name = meta.get('file_name', 'Unknown')
        title = meta.get('title', file_name)
        start_line = meta.get('start_line', 0)

        similarity_pct = f"{similarity * 100:.1f}%"

        display_text = f"[{similarity_pct}] {title}"
        sub_text = f"  {file_name} (行 {start_line + 1})"

        self.setText(display_text)
        self.setToolTip(f"{title}\n{file_name}\n匹配度: {similarity_pct}\n位置: 行 {start_line + 1}")
        self.setData(Qt.ItemDataRole.UserRole, self.result)


class SearchResultList(QWidget):
    """搜索结果列表组件"""

    item_selected = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.header_label = QLabel("搜索结果")
        self.header_label.setObjectName("section")
        layout.addWidget(self.header_label)

        self.list_widget = QListWidget()
        self.list_widget.currentItemChanged.connect(self._on_item_changed)
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.list_widget)

    def display_results(self, results: list):
        """显示搜索结果"""
        self.list_widget.clear()

        if not results:
            item = QListWidgetItem("未找到匹配结果")
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.list_widget.addItem(item)
            return

        for result in results:
            item = SearchResultItem(result)
            self.list_widget.addItem(item)

        self.header_label.setText(f"搜索结果 ({len(results)} 条)")

    def _on_item_changed(self, current, previous):
        if current:
            result = current.data(Qt.ItemDataRole.UserRole)
            if result:
                self.item_selected.emit(result)

    def _on_item_clicked(self, item):
        result = item.data(Qt.ItemDataRole.UserRole)
        if result:
            self.item_selected.emit(result)

    def clear(self):
        self.list_widget.clear()
        self.header_label.setText("搜索结果")
