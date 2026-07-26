"""
状态栏组件（增强版）
左：当前状态；中：进度条；右：检索模式/设备上下文 + 索引统计。
"""

from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel, QProgressBar
from PyQt6.QtCore import Qt


class StatusBarWidget(QWidget):
    """自定义状态栏组件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(12)

        self.status_label = QLabel("就绪")
        layout.addWidget(self.status_label, 1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(220)
        self.progress_bar.setFixedHeight(10)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.context_label = QLabel("")
        self.context_label.setObjectName("subtitle")
        layout.addWidget(self.context_label)

        self.stats_label = QLabel("")
        self.stats_label.setObjectName("subtitle")
        layout.addWidget(self.stats_label)

        self.setFixedHeight(32)

    def set_status(self, text: str):
        self.status_label.setText(text)

    def set_context(self, text: str):
        """显示检索模式 / 当前设备等上下文信息"""
        self.context_label.setText(text)

    def show_progress(self, value: int, max_value: int, text: str = ""):
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(max_value)
        self.progress_bar.setValue(value)
        if text:
            self.status_label.setText(text)

    def hide_progress(self):
        self.progress_bar.setVisible(False)

    def set_stats(self, files: int, chunks: int, sources: int = None):
        if sources is not None:
            self.stats_label.setText(f"源 {sources} · 文件 {files} · 切片 {chunks}")
        else:
            self.stats_label.setText(f"文件 {files} · 切片 {chunks}")
