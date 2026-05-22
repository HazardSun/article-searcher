"""
状态栏组件
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

        self.status_label = QLabel("就绪")
        layout.addWidget(self.status_label)

        layout.addStretch()

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(200)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet("color: #a0a0b0;")
        layout.addWidget(self.stats_label)

    def set_status(self, text: str):
        self.status_label.setText(text)

    def show_progress(self, value: int, max_value: int, text: str = ""):
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(max_value)
        self.progress_bar.setValue(value)
        if text:
            self.status_label.setText(text)

    def hide_progress(self):
        self.progress_bar.setVisible(False)

    def set_stats(self, files: int, chunks: int):
        self.stats_label.setText(f"文件: {files} | 切片: {chunks}")
