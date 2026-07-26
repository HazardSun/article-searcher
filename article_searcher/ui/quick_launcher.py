"""
Ctrl+K 快速检索浮层（功能14）

独立置顶无边框 QDialog，复用主窗口同一 engine 实例与索引状态：
- 顶部居中、宽 ~600、可拖拽移动、自绘关闭按钮。
- 输入即搜（防抖），Enter 唤起主窗口并定位选中结果，Esc 关闭且不改变主窗口状态。
- 所有检索走后台 Worker，避免阻塞浮层线程。

注意：浮层不修改主窗口 engine 之外的任何状态，关闭即销毁，对主窗口零副作用。
"""

import logging
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QLabel,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QSize
from PyQt6.QtGui import QFont, QFontMetrics

from core.query_parser import parse_query

logger = logging.getLogger(__name__)

_USER_ROLE = Qt.ItemDataRole.UserRole


class _LauncherSearchWorker(QThread):
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, engine, parsed, top_k, mode, tag_filter):
        super().__init__()
        self.engine = engine
        self.parsed = parsed
        self.top_k = top_k
        self.mode = mode
        self.tag_filter = tag_filter

    def run(self):
        try:
            results = self.engine.search(
                parsed=self.parsed, top_k=self.top_k,
                mode=self.mode, tag_filter=self.tag_filter,
            )
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


class QuickLauncher(QDialog):
    """Ctrl+K 浮层"""

    def __init__(self, engine, parent=None, on_activate=None):
        super().__init__(parent)
        self.engine = engine
        self._on_activate = on_activate
        self._worker = None
        self._drag_pos = None

        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.FramelessWindowHint
        )
        self.setMinimumWidth(600)
        self.resize(600, 440)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 顶部栏（标题 + 关闭按钮），也是拖拽区
        top = QHBoxLayout()
        top.setContentsMargins(14, 10, 10, 10)
        title = QLabel("快速检索  (Ctrl+K)")
        title.setObjectName("section")
        top.addWidget(title)
        top.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setObjectName("icon_btn")
        close_btn.setFixedSize(28, 28)
        close_btn.clicked.connect(self.reject)
        top.addWidget(close_btn)
        layout.addLayout(top)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入关键词或高级语法（tag: / path: / -排除）…")
        self.search_input.setMinimumHeight(42)
        self.search_input.returnPressed.connect(self._on_return_pressed)
        self.search_input.textChanged.connect(self._on_text_changed)
        layout.addWidget(self.search_input)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("launcher_list")
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        self.list_widget.currentItemChanged.connect(lambda c, p: None)
        layout.addWidget(self.list_widget)

        self.status_label = QLabel("")
        self.status_label.setObjectName("subtitle")
        self.status_label.setContentsMargins(14, 6, 14, 8)
        layout.addWidget(self.status_label)

        # 防抖定时器
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self._run_search)

    # ------------------------------------------------------------------ #
    # 显示与定位
    # ------------------------------------------------------------------ #
    def showEvent(self, event):
        super().showEvent(event)
        self._center_top()
        self.search_input.setFocus()
        self.search_input.selectAll()

    def _center_top(self):
        screen = self.screen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        x = geo.x() + (geo.width() - self.width()) // 2
        y = geo.y() + max(40, int(geo.height() * 0.08))
        self.move(x, y)

    # ------------------------------------------------------------------ #
    # 检索
    # ------------------------------------------------------------------ #
    def _on_text_changed(self, text: str):
        self._debounce.start(250)

    def _on_return_pressed(self):
        if self.list_widget.count() > 0:
            item = self.list_widget.currentItem() or self.list_widget.item(0)
            result = item.data(_USER_ROLE) if item else None
            if result:
                self._activate(result)
                return
        self._run_search()

    def _run_search(self):
        query = self.search_input.text().strip()
        if not query:
            self.list_widget.clear()
            return
        if self.engine.current_folder is None:
            self.status_label.setText("请先在主窗口索引一个文件夹")
            return
        if self._worker is not None and self._worker.isRunning():
            return
        parsed = parse_query(query)
        self.status_label.setText("检索中…")
        self._worker = _LauncherSearchWorker(
            self.engine, parsed, top_k=10, mode=None, tag_filter=None,
        )
        self._worker.finished.connect(self._on_ready)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_ready(self, results):
        self.list_widget.clear()
        if not results:
            self.status_label.setText("未找到结果")
            return
        font = self.list_widget.font()
        fm = QFontMetrics(font)
        width = self.list_widget.viewport().width() or 560
        for r in results:
            meta = r.get("metadata", {}) or {}
            fn = meta.get("file_name", "") or meta.get("title", "Unknown")
            sim = r.get("similarity", 0) or 0
            snippet = (r.get("snippet", "") or "").replace("\n", " ")
            if len(snippet) > 80:
                snippet = snippet[:80] + "…"
            text = f"[{sim * 100:.0f}%] {fn}\n  {snippet}"
            item = QListWidgetItem(text)
            item.setData(_USER_ROLE, r)
            per_line = max(1, (width - 24) // max(fm.horizontalAdvance("中"), 1))
            lines = 1 + max(1, (len(snippet) // per_line) + 1)
            item.setSizeHint(QSize(0, 16 + lines * fm.height() + (lines - 1) * 4))
            self.list_widget.addItem(item)
        self.list_widget.setCurrentRow(0)
        self.status_label.setText(f"找到 {len(results)} 条结果 · Enter 定位 / Esc 关闭")

    def _on_error(self, msg):
        self.status_label.setText(f"检索失败: {msg}")

    def _on_item_clicked(self, item):
        result = item.data(_USER_ROLE)
        if result:
            self._activate(result)

    def _activate(self, result):
        if self._on_activate:
            self._on_activate(result)
        self.accept()

    # ------------------------------------------------------------------ #
    # 键盘 / 拖拽
    # ------------------------------------------------------------------ #
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            # 让列表处理上下导航
            self.list_widget.keyPressEvent(event)
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint() - self._drag_pos
            self.move(self.pos() + delta)
            self._drag_pos = event.globalPosition().toPoint()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)
