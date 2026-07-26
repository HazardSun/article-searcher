"""
相似文章推荐面板（功能4）

右栏「相关」Tab：打开/选中某文档时，后台调用 engine.query_similar 查询相似文章，
以列表展示（文件名 + 相似度 + 片段），点击复用主窗口定位与高亮。

所有耗时查询走 RelatedWorker(QThread)，结果通过信号回传，不在 GUI 线程操作引擎。
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QListWidget, QListWidgetItem,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize
from PyQt6.QtGui import QFont, QFontMetrics

_USER_ROLE = Qt.ItemDataRole.UserRole


class RelatedWorker(QThread):
    """后台执行 engine.query_similar，避免阻塞 GUI 线程。"""
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, engine, file_path: str, top_k: int = 5):
        super().__init__()
        self.engine = engine
        self.file_path = file_path
        self.top_k = top_k

    def run(self):
        try:
            results = self.engine.query_similar(self.file_path, self.top_k)
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


class RelatedArticlesWidget(QWidget):
    """相似文章推荐组件（右栏 Tab）"""

    related_selected = pyqtSignal(dict)

    def __init__(self, engine=None, parent=None):
        super().__init__(parent)
        self.engine = engine
        self._worker = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.header_label = QLabel("相关文章")
        self.header_label.setObjectName("section")
        layout.addWidget(self.header_label)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("related_list")
        self.list_widget.setWordWrap(True)
        self.list_widget.currentItemChanged.connect(self._on_item_changed)
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.list_widget)

    def set_engine(self, engine):
        self.engine = engine

    def load(self, file_path: str):
        """查询 file_path 的相似文章（单飞保护）。"""
        if not self.engine or not file_path:
            return
        if self._worker is not None and self._worker.isRunning():
            return
        self.header_label.setText("相关文章（查询中…）")
        self.list_widget.clear()
        item = QListWidgetItem("正在查找相似文章…")
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        self.list_widget.addItem(item)

        self._worker = RelatedWorker(self.engine, file_path, top_k=5)
        self._worker.finished.connect(self._on_ready)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_ready(self, results: list):
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        self.list_widget.blockSignals(False)
        if not results:
            self.header_label.setText("相关文章 (0 条)")
            item = QListWidgetItem("没有找到相似文章")
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list_widget.addItem(item)
            return

        font = self.list_widget.font()
        fm = QFontMetrics(font)
        for r in results:
            meta = r.get("metadata", {}) or {}
            fn = meta.get("file_name", "") or meta.get("title", "Unknown")
            sim = r.get("similarity", 0) or 0
            snippet = (r.get("snippet", "") or "").replace("\n", " ")
            if len(snippet) > 90:
                snippet = snippet[:90] + "…"
            text = f"[{sim * 100:.0f}%] {fn}\n  {snippet}"
            item = QListWidgetItem(text)
            item.setData(_USER_ROLE, r)
            # 估算高度防遮挡
            per_line = max(1, (self.list_widget.viewport().width() - 24) // max(fm.horizontalAdvance("中"), 1))
            lines = max(2, 1 + max(1, (len(snippet) // per_line) + 1))
            item.setSizeHint(QSize(0, 12 * 2 + lines * fm.height() + (lines - 1) * 4))
            item.setToolTip(f"{fn}\n相似度: {sim * 100:.1f}%")
            self.list_widget.addItem(item)

        self.header_label.setText(f"相关文章 ({len(results)} 条)")

    def _on_error(self, msg: str):
        self.list_widget.clear()
        self.header_label.setText("相关文章 (错误)")
        item = QListWidgetItem("相似推荐失败")
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        self.list_widget.addItem(item)

    def _emit(self, item):
        result = item.data(_USER_ROLE)
        if result:
            self.related_selected.emit(result)

    def _on_item_changed(self, current, previous):
        if current:
            self._emit(current)

    def _on_item_clicked(self, item):
        self._emit(item)
