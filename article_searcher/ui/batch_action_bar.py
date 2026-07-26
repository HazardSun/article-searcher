"""
批量操作浮动条（功能8）

左栏/中栏多选后浮出：提供「加标签 / 重建 / 移除」三类操作。纯 UI 组件，
实际操作由 main_window 经 BatchWorker（后台线程）执行。通过信号回传
(操作类型, 文件列表) 给主窗口。
"""

from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton
from PyQt6.QtCore import pyqtSignal


class BatchActionBar(QWidget):
    add_tags_requested = pyqtSignal(list)
    reindex_requested = pyqtSignal(list)
    remove_requested = pyqtSignal(list)
    cancelled = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._paths = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)

        self.count_label = QLabel("已选 0 项")
        self.count_label.setObjectName("batch_count")
        layout.addWidget(self.count_label)

        layout.addStretch()

        self.add_tags_btn = QPushButton("加标签")
        self.reindex_btn = QPushButton("重建")
        self.remove_btn = QPushButton("移除")
        self.cancel_btn = QPushButton("取消")
        for b in (self.add_tags_btn, self.reindex_btn, self.remove_btn, self.cancel_btn):
            # 动态按字体度量撑高：任何字体/DPI 下都 >= sizeHint，绝不裁切中文
            b.setMinimumHeight(b.fontMetrics().height() + 24)
        self.add_tags_btn.setObjectName("primary")
        self.cancel_btn.setObjectName("secondary")

        self.add_tags_btn.clicked.connect(lambda: self.add_tags_requested.emit(list(self._paths)))
        self.reindex_btn.clicked.connect(lambda: self.reindex_requested.emit(list(self._paths)))
        self.remove_btn.clicked.connect(lambda: self.remove_requested.emit(list(self._paths)))
        self.cancel_btn.clicked.connect(self.cancelled.emit)

        layout.addWidget(self.add_tags_btn)
        layout.addWidget(self.reindex_btn)
        layout.addWidget(self.remove_btn)
        layout.addWidget(self.cancel_btn)

    def set_targets(self, paths: list):
        self._paths = list(paths)
        self.count_label.setText(f"已选 {len(self._paths)} 项")
        self.setVisible(len(self._paths) > 0)

    def clear(self):
        self._paths = []
        self.count_label.setText("已选 0 项")
        self.setVisible(False)
