"""
已索引文件管理面板
展示当前索引中的文件，支持：在资源管理器中打开、在右侧查看、重建单文件索引、移除。
修复：重建/移除改为后台线程执行（涉及模型编码，可能耗时数秒），
去除阻塞式结果弹窗，改为 notify 信号向状态栏输出。
新增：文件名过滤搜索框。
"""

from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QMessageBox, QLineEdit,
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread


class _FileOpWorker(QThread):
    """后台执行单文件重建 / 移除（重建涉及模型编码，不能放在 GUI 线程）"""
    finished = pyqtSignal(str, str)   # op, path
    error = pyqtSignal(str, str)      # op, error

    def __init__(self, engine, op: str, path: str):
        super().__init__()
        self.engine = engine
        self.op = op
        self.path = path

    def run(self):
        try:
            if self.op == "reindex":
                self.engine.reindex_file(self.path)
            elif self.op == "remove":
                self.engine.remove_file_from_index(self.path)
            self.finished.emit(self.op, self.path)
        except Exception as e:
            self.error.emit(self.op, str(e))


class IndexedFilesPanel(QWidget):
    changed = pyqtSignal()            # 索引发生变化（移除/重建）
    file_selected = pyqtSignal(str)   # 双击文件，请求在查看器中打开
    notify = pyqtSignal(str)          # 状态栏提示
    starred_changed = pyqtSignal(str, bool)  # (file_path, starred)

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self._worker = None
        self._all_files = []
        self._path_filter = None       # 主题簇过滤（功能5）
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(6)

        header = QHBoxLayout()
        self.label = QLabel("已索引文件")
        self.label.setObjectName("section")
        header.addWidget(self.label)
        header.addStretch()
        self.count_label = QLabel("")
        self.count_label.setObjectName("subtitle")
        header.addWidget(self.count_label)
        layout.addLayout(header)

        self.filter_input = QLineEdit()
        self.filter_input.setObjectName("filter_input")
        self.filter_input.setPlaceholderText("搜索文件名...")
        self.filter_input.setClearButtonEnabled(True)
        self.filter_input.setFixedHeight(30)
        self.filter_input.textChanged.connect(self._apply_filter)
        layout.addWidget(self.filter_input)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("file_list")
        self.list_widget.itemDoubleClicked.connect(self._on_double_click)
        self.list_widget.currentItemChanged.connect(self._on_selection_changed)
        layout.addWidget(self.list_widget)

        btn_row = QHBoxLayout()
        self.open_btn = QPushButton("打开")
        self.open_btn.setToolTip("在资源管理器中显示该文件")
        self.reindex_btn = QPushButton("重建")
        self.reindex_btn.setToolTip("重新解析并索引该文件")
        self.remove_btn = QPushButton("移除")
        self.remove_btn.setToolTip("从索引中移除该文件（不删除磁盘文件）")
        self.star_btn = QPushButton("☆ 星标")
        self.star_btn.setObjectName("star_btn")
        self.star_btn.setToolTip("切换星标（置顶收藏）")
        for b in (self.open_btn, self.reindex_btn, self.remove_btn, self.star_btn):
            b.setFixedHeight(32)
        self.open_btn.clicked.connect(self._open)
        self.reindex_btn.clicked.connect(self._reindex)
        self.remove_btn.clicked.connect(self._remove)
        self.star_btn.clicked.connect(self._toggle_star)
        btn_row.addWidget(self.open_btn)
        btn_row.addWidget(self.reindex_btn)
        btn_row.addWidget(self.remove_btn)
        btn_row.addWidget(self.star_btn)
        layout.addLayout(btn_row)

        self._set_buttons_enabled(False)

    def _set_buttons_enabled(self, enabled: bool):
        self.open_btn.setEnabled(enabled)
        self.reindex_btn.setEnabled(enabled)
        self.remove_btn.setEnabled(enabled)
        self.star_btn.setEnabled(enabled)

    def _busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def refresh(self):
        files = self.engine.list_indexed_files()
        # 星标置顶排序（功能7）
        self._all_files = sorted(
            files.keys(),
            key=lambda p: (not self.engine.is_starred_file(p), Path(p).name.lower()),
        )
        self._rebuild_list()

    def _rebuild_list(self):
        text = self.filter_input.text().strip().lower()
        self.list_widget.clear()
        shown = 0
        for path in self._all_files:
            if self._path_filter is not None and path not in self._path_filter:
                continue
            name = Path(path).name
            if text and text not in name.lower() and text not in path.lower():
                continue
            prefix = "★ " if self.engine.is_starred_file(path) else ""
            item = QListWidgetItem(f"{prefix}{name}\n  {path}")
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self.list_widget.addItem(item)
            shown += 1
        total = len(self._all_files)
        if self._path_filter is not None:
            total = shown
        self.label.setText("已索引文件")
        if text or self._path_filter is not None:
            self.count_label.setText(f"{shown}/{len(self._all_files)}")
        else:
            self.count_label.setText(f"共 {total} 个")

    def apply_path_filter(self, paths):
        """主题簇点击：仅显示指定文件集合（功能5）。"""
        self._path_filter = set(paths)
        self._rebuild_list()

    def clear_path_filter(self):
        self._path_filter = None
        self._rebuild_list()

    def _toggle_star(self):
        path = self._current_path()
        if not path:
            return
        if self.engine.is_starred_file(path):
            self.engine.unstar_file(path)
            starred = False
        else:
            self.engine.star_file(path)
            starred = True
        self.starred_changed.emit(path, starred)
        self.refresh()

    def _apply_filter(self, _text: str):
        self._rebuild_list()

    def _current_path(self) -> str:
        item = self.list_widget.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else ""

    def _on_selection_changed(self, current, _previous):
        self._set_buttons_enabled(current is not None and not self._busy())

    def _on_double_click(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self.file_selected.emit(path)

    def _open(self):
        path = self._current_path()
        if path:
            self.engine.open_file_in_explorer(path)

    def _start_op(self, op: str, path: str, status_text: str):
        if self._busy():
            self.notify.emit("有文件操作正在进行中，请稍候…")
            return
        self._set_buttons_enabled(False)
        self.notify.emit(status_text)
        self._worker = _FileOpWorker(self.engine, op, path)
        self._worker.finished.connect(self._on_op_finished)
        self._worker.error.connect(self._on_op_error)
        self._worker.start()

    def _reindex(self):
        path = self._current_path()
        if not path:
            return
        self._start_op("reindex", path, f"正在重建索引: {Path(path).name} …")

    def _remove(self):
        path = self._current_path()
        if not path:
            return
        reply = QMessageBox.question(
            self, "确认移除", f"从索引中移除？（不会删除磁盘文件）\n{path}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._start_op("remove", path, f"正在移除: {Path(path).name} …")

    def _on_op_finished(self, op: str, path: str):
        name = Path(path).name
        if op == "reindex":
            self.notify.emit(f"已重建索引: {name}")
        else:
            self.notify.emit(f"已从索引移除: {name}")
        self.refresh()
        self._set_buttons_enabled(self.list_widget.currentItem() is not None)
        self.changed.emit()

    def _on_op_error(self, op: str, error_msg: str):
        label = "重建索引" if op == "reindex" else "移除"
        self.notify.emit(f"{label}失败")
        self._set_buttons_enabled(self.list_widget.currentItem() is not None)
        QMessageBox.warning(self, "操作失败", f"{label}失败:\n{error_msg}")
