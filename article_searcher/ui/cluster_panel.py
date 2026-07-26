"""
左栏「主题簇」分组面板（功能5）

与手动标签（TagFilterWidget）视觉隔离：紫色边框 chip，独立管理。
点击某簇 → 以簇内文件集合过滤检索/展示；提供「重新聚类」按钮。
簇数据来自 engine.tag_manager.get_clusters()（与 file_tags 物理隔离）。
右键簇可「解散」；簇为建议性、可关闭（cluster_enabled=False 时整体隐藏）。
"""

from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QMenu,
)
from PyQt6.QtCore import Qt, pyqtSignal


class ClusterPanel(QWidget):
    cluster_selected = pyqtSignal(list)   # 簇内文件列表
    cluster_cleared = pyqtSignal()
    recluster_requested = pyqtSignal()

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(6)

        header = QHBoxLayout()
        label = QLabel("主题簇")
        label.setObjectName("cluster_section")
        header.addWidget(label)
        header.addStretch()
        self.recluster_btn = QPushButton("重新聚类")
        self.recluster_btn.setObjectName("secondary")
        # 动态按字体度量撑高：任何字体/DPI 下都 >= sizeHint，绝不裁切中文
        self.recluster_btn.setMinimumHeight(self.recluster_btn.fontMetrics().height() + 18)
        self.recluster_btn.setToolTip("基于当前索引的语义向量重新聚类")
        self.recluster_btn.clicked.connect(self.recluster_requested.emit)
        header.addWidget(self.recluster_btn)
        layout.addLayout(header)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("cluster_list")
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self.list_widget)

    def set_visible_enabled(self, enabled: bool):
        """cluster_enabled 开关控制整体可见性。"""
        self.setVisible(enabled)

    def refresh(self):
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        clusters = self.engine.tag_manager.get_clusters()
        for cid, c in clusters.items():
            files = c.get("files", [])
            if not files:
                continue
            label = c.get("label", cid)
            item = QListWidgetItem(f"{label}  ·  {len(files)}")
            item.setData(Qt.ItemDataRole.UserRole, cid)
            item.setToolTip("样本: " + " / ".join(c.get("sample_titles", [])[:3]))
            self.list_widget.addItem(item)
        self.list_widget.blockSignals(False)
        self.setVisible(self.isVisible() and len(clusters) > 0)

    def _on_item_clicked(self, item):
        cid = item.data(Qt.ItemDataRole.UserRole)
        c = self.engine.tag_manager.get_clusters().get(cid, {})
        files = c.get("files", [])
        if files:
            self.cluster_selected.emit(list(files))

    def _on_context_menu(self, pos):
        item = self.list_widget.itemAt(pos)
        if not item:
            return
        cid = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        act_dismiss = menu.addAction("解散该簇")
        act_clear = menu.addAction("清除全部簇筛选")
        chosen = menu.exec(self.list_widget.mapToGlobal(pos))
        if chosen == act_dismiss:
            self.engine.tag_manager.dismiss_cluster(cid)
            self.engine.tag_manager.save()
            self.refresh()
        elif chosen == act_clear:
            self.cluster_cleared.emit()
