"""
链接图谱面板（功能6 容器 / P2-6）

右栏「链接图谱」Tab 容器：
- 持有 `LinkGraphViewer`（画布）+ 「本文引用」(出链) / 「被引用」(入链) 列表；
- `LinkGraphWorker`（QThread）后台 build_link_graph（读全文，不阻塞 GUI）；
- `highlight_for_file(path)`：高亮该文章的入/出链邻居，
  并填充出链/入链列表；
- 列表交互：出链表项 **单击** → 在当前文档高亮引用处
  （复用 doc_viewer.highlight_and_scroll），**双击** → link_jump 跳转；
  入链表项单击即跳转引用来源文件；
- 沿用 RelatedArticlesWidget 的面板+Worker 模式（单飞保护 isRunning()）。
"""

from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize
from PyQt6.QtGui import QFont, QFontMetrics

from .link_graph_viewer import LinkGraphViewer


class LinkGraphWorker(QThread):
    """后台构建链接图谱（读全文 + 解析，禁止 GUI 线程阻塞）。"""
    finished = pyqtSignal(object)   # LinkGraph
    error = pyqtSignal(str)

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine

    def run(self):
        try:
            graph = self.engine.build_link_graph()
            self.finished.emit(graph)
        except Exception as e:  # noqa: BLE001
            self.error.emit(str(e))


class LinkGraphPanel(QWidget):
    """链接图谱面板（右栏 Tab）。"""

    link_jump = pyqtSignal(str)   # 跳转某文章（回传主窗口）

    def __init__(self, engine=None, parent=None):
        super().__init__(parent)
        self.engine = engine
        self._worker = None
        self._graph = None
        self._doc_viewer = None
        self._pending_path = None    # 图谱就绪前暂存的待高亮文件
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        header = QHBoxLayout()
        label = QLabel("链接图谱")
        label.setObjectName("section")
        header.addWidget(label)
        header.addStretch()
        self.refresh_btn = QPushButton("重新构建")
        self.refresh_btn.setObjectName("secondary")
        self.refresh_btn.setFixedHeight(24)
        self.refresh_btn.setToolTip("重新扫描全部索引文件构建链接图谱")
        self.refresh_btn.clicked.connect(self.load)
        header.addWidget(self.refresh_btn)
        layout.addLayout(header)

        self.viewer = LinkGraphViewer()
        self.viewer.node_clicked.connect(self._on_node_clicked)
        layout.addWidget(self.viewer)

        # 出链 / 入链 列表
        self.out_label = QLabel("本文引用（出链）")
        self.out_label.setObjectName("link_section")
        layout.addWidget(self.out_label)
        self.out_list = QListWidget()
        self.out_list.setObjectName("link_list")
        self.out_list.setMaximumHeight(150)
        self.out_list.setWordWrap(True)
        self.out_list.itemClicked.connect(self._on_out_clicked)
        self.out_list.itemDoubleClicked.connect(lambda it: self._jump_item(it, "out"))
        layout.addWidget(self.out_list)

        self.in_label = QLabel("被以下引用（入链）")
        self.in_label.setObjectName("link_section")
        layout.addWidget(self.in_label)
        self.in_list = QListWidget()
        self.in_list.setObjectName("link_list")
        self.in_list.setMaximumHeight(120)
        self.in_list.setWordWrap(True)
        self.in_list.itemClicked.connect(self._on_in_clicked)
        self.in_list.itemDoubleClicked.connect(lambda it: self._jump_item(it, "in"))
        layout.addWidget(self.in_list)

    def set_engine(self, engine):
        self.engine = engine

    def set_doc_viewer(self, viewer):
        """注入文档查看器，供出链单击高亮引用处。"""
        self._doc_viewer = viewer

    def set_theme(self, theme: str):
        self.viewer.set_theme(theme)

    # ------------------------------------------------------------------ #
    # 后台构建
    # ------------------------------------------------------------------ #
    def load(self):
        """触发后台 build_link_graph（单飞保护）。"""
        if not self.engine:
            return
        if self._worker is not None and self._worker.isRunning():
            return
        self._clear_lists()
        self._worker = LinkGraphWorker(self.engine, self)
        self._worker.finished.connect(self._on_ready)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def invalidate(self):
        """索引/源变化后令缓存失效（下次 load 重算）。"""
        self._graph = None
        self._pending_path = None
        self._clear_lists()
        self.viewer.clear_highlight()
        if self._worker is not None and self._worker.isRunning():
            return
        # 若当前可见则立即重建（由调用方决定何时调用）

    def _on_ready(self, graph):
        self._graph = graph
        self.viewer.display_graph(graph)
        if self._pending_path:
            path = self._pending_path
            self._pending_path = None
            self.highlight_for_file(path)

    def _on_error(self, msg: str):
        self.out_label.setText("链接图谱（构建失败）")

    # ------------------------------------------------------------------ #
    # 高亮 + 列表填充
    # ------------------------------------------------------------------ #
    def highlight_for_file(self, path: str):
        """高亮 path 的入/出链邻居，并填充出链/入链列表。"""
        if self._graph is None:
            # 图谱尚未构建，暂存，待 ready 后补做
            self._pending_path = path
            return
        self.viewer.highlight_neighbors(path)
        self._fill_out(path)
        self._fill_in(path)

    def _fill_out(self, path: str):
        self._clear_list(self.out_list)
        if not self._graph:
            return
        font = self.out_list.font()
        fm = QFontMetrics(font)
        for e in self._graph.edges:
            if e.source != path:
                continue
            title = self._node_title(e.target)
            text = f"→ {title}\n  {e.context[:80]}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, {
                "path": e.target, "line": e.line,
                "context": e.context, "role": "out"})
            item.setToolTip(f"{title}\n行 {e.line + 1}\n{e.context}")
            self._size_item(item, fm, text, self.out_list)
            self.out_list.addItem(item)
        self.out_label.setText(f"本文引用（出链 {self.out_list.count()}）")

    def _fill_in(self, path: str):
        self._clear_list(self.in_list)
        if not self._graph:
            return
        font = self.in_list.font()
        fm = QFontMetrics(font)
        for e in self._graph.edges:
            if e.target != path:
                continue
            title = self._node_title(e.source)
            text = f"{title} →\n  {e.context[:80]}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, {
                "path": e.source, "line": e.line,
                "context": e.context, "role": "in"})
            item.setToolTip(f"{title}\n行 {e.line + 1}\n{e.context}")
            self._size_item(item, fm, text, self.in_list)
            self.in_list.addItem(item)
        self.in_label.setText(f"被以下引用（入链 {self.in_list.count()}）")

    def _node_title(self, key: str) -> str:
        node = self._graph.nodes.get(key) if self._graph else None
        if node:
            return node.title
        return Path(key).stem if key else key

    @staticmethod
    def _size_item(item, fm, text, list_widget):
        # 使用列表控件自身的 viewport 宽度（与兄弟面板一致），
        # 避免在 item 尚未 addItem 时 item.listWidget() 为 None 导致崩溃。
        vw = max(1, (list_widget.viewport().width() - 24) // max(fm.horizontalAdvance("中"), 1))
        lines = max(2, 1 + max(1, (len(text) // vw) + 1))
        item.setSizeHint(QSize(0, fm.height() * 2 + lines * (fm.height() + 2)))

    @staticmethod
    def _clear_list(lst):
        lst.blockSignals(True)
        lst.clear()
        lst.blockSignals(False)

    def _clear_lists(self):
        self._clear_list(self.out_list)
        self._clear_list(self.in_list)
        self.out_label.setText("本文引用（出链）")
        self.in_label.setText("被以下引用（入链）")

    # ------------------------------------------------------------------ #
    # 列表交互
    # ------------------------------------------------------------------ #
    def _on_out_clicked(self, item):
        """出链单击：在当前文档高亮引用所在行。"""
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data or self._doc_viewer is None:
            return
        line = data.get("line", 0)
        context = data.get("context", "")
        try:
            self._doc_viewer.highlight_and_scroll(line, line, context[:50])
        except Exception:  # noqa: BLE001
            pass

    def _on_in_clicked(self, item):
        """入链单击：跳转到引用来源文件（引用处在该文件中）。"""
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        self.link_jump.emit(data.get("path", ""))

    def _jump_item(self, item, role):
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        self.link_jump.emit(data.get("path", ""))

    def _on_node_clicked(self, path: str):
        """画布节点点击：可直接跳转（主窗口会判断是否为真实文件）。"""
        self.link_jump.emit(path)
