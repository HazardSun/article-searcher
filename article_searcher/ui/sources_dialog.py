"""
多索引源管理对话框（功能11 + 功能10 开关）

承载：增删改索引源、逐源排除规则（每行一个 fnmatch glob）、启用开关，
以及「自动索引」「主题簇展示」两项全局开关。确定后回写 engine 与 config_store。
"""

import os
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QLineEdit, QPlainTextEdit, QCheckBox, QDialogButtonBox,
    QLabel, QFileDialog, QMessageBox,
)
from PyQt6.QtCore import Qt, pyqtSignal

from core.multisource import Source, SourceList
from core.config import ConfigStore


class SourcesDialog(QDialog):
    applied = pyqtSignal(dict)

    def __init__(self, engine, config_store: ConfigStore, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.config_store = config_store
        self._source_list = SourceList.from_dicts(config_store.config.index_sources)
        self.setWindowTitle("管理索引源")
        self.setMinimumWidth(620)
        self.setMinimumHeight(440)
        self._setup_ui()
        self._refresh_list()
        self._select_first()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        top = QHBoxLayout()
        top_label = QLabel("索引源（可添加多个文件夹，支持排除规则）")
        top_label.setObjectName("section")
        top.addWidget(top_label)
        top.addStretch()
        layout.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(12)

        # 左：源列表
        left = QVBoxLayout()
        self.list_widget = QListWidget()
        self.list_widget.setObjectName("source_list")
        self.list_widget.currentItemChanged.connect(self._on_select)
        left.addWidget(self.list_widget)
        btn_row = QHBoxLayout()
        self.add_btn = QPushButton("添加文件夹")
        self.add_btn.setObjectName("primary")
        self.del_btn = QPushButton("删除")
        self.del_btn.setObjectName("secondary")
        self.add_btn.clicked.connect(self._add)
        self.del_btn.clicked.connect(self._delete)
        btn_row.addWidget(self.add_btn)
        btn_row.addWidget(self.del_btn)
        left.addLayout(btn_row)
        body.addLayout(left, 1)

        # 右：编辑区
        right = QVBoxLayout()
        right.setSpacing(8)
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        path_row = QHBoxLayout()
        path_row.addWidget(self.path_edit, 1)
        self.browse_btn = QPushButton("浏览…")
        self.browse_btn.setObjectName("secondary")
        self.browse_btn.clicked.connect(self._browse)
        path_row.addWidget(self.browse_btn)
        right.addWidget(QLabel("源路径"))
        right.addLayout(path_row)

        right.addWidget(QLabel("排除规则（每行一个 glob，如 .git / node_modules / *.tmp）"))
        self.exclude_edit = QPlainTextEdit()
        self.exclude_edit.setObjectName("exclude_edit")
        self.exclude_edit.setPlaceholderText("一行一个，例如：\n.git\nnode_modules\n*.tmp")
        right.addWidget(self.exclude_edit, 1)

        self.enabled_chk = QCheckBox("启用此源（取消则停止索引该目录）")
        self.enabled_chk.setChecked(True)
        right.addWidget(self.enabled_chk)

        body.addLayout(right, 1)
        layout.addLayout(body)

        # 全局开关
        self.auto_index_chk = QCheckBox("启用自动索引（监听文件变更并增量更新，默认关闭）")
        self.auto_index_chk.setChecked(self.config_store.config.auto_index_enabled)
        self.cluster_chk = QCheckBox("左栏展示「主题簇」分组（语义自动聚类）")
        self.cluster_chk.setChecked(self.config_store.config.cluster_enabled)
        layout.addWidget(self.auto_index_chk)
        layout.addWidget(self.cluster_chk)

        hint = QLabel(
            "提示：排除规则相对各源根目录解释，大小写不敏感；"
            "自动索引仅监听已启用源，且默认关闭以避免后台重嵌占用资源。"
        )
        hint.setWordWrap(True)
        hint.setObjectName("subtitle")
        layout.addWidget(hint)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    # ------------------------------------------------------------------ #
    def _refresh_list(self):
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for s in self._source_list:
            mark = "✓" if s.enabled else "✗"
            item = QListWidgetItem(f"[{mark}] {s.path}")
            item.setData(Qt.ItemDataRole.UserRole, s.path)
            item.setToolTip("排除规则: " + (", ".join(s.exclude_rules) or "无"))
            self.list_widget.addItem(item)
        self.list_widget.blockSignals(False)

    def _select_first(self):
        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)

    def _current_source(self) -> Source:
        item = self.list_widget.currentItem()
        if not item:
            return None
        return self._source_list.get(item.data(Qt.ItemDataRole.UserRole))

    def _on_select(self, current, _prev):
        s = self._current_source()
        if not s:
            self.path_edit.clear()
            self.exclude_edit.clear()
            self.enabled_chk.setChecked(True)
            return
        self.path_edit.setText(s.path)
        self.exclude_edit.setPlainText("\n".join(s.exclude_rules))
        self.enabled_chk.setChecked(s.enabled)

    def _commit_current(self):
        s = self._current_source()
        if not s:
            return
        s.exclude_rules = [r.strip() for r in self.exclude_edit.toPlainText().splitlines() if r.strip()]
        s.enabled = self.enabled_chk.isChecked()

    def _add(self):
        folder = QFileDialog.getExistingDirectory(
            self, "选择索引文件夹", "", QFileDialog.Option.ShowDirsOnly
        )
        if not folder:
            return
        self._source_list.add(Source(folder))
        self._refresh_list()
        # 选中新增项
        for i in range(self.list_widget.count()):
            if self.list_widget.item(i).data(Qt.ItemDataRole.UserRole) == folder:
                self.list_widget.setCurrentRow(i)
                break

    def _delete(self):
        s = self._current_source()
        if not s:
            return
        reply = QMessageBox.question(
            self, "确认删除", f"从索引源中移除？\n{s.path}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._source_list.remove(s.path)
        self._refresh_list()
        self._select_first()

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(
            self, "选择索引文件夹", self.path_edit.text() or "",
            QFileDialog.Option.ShowDirsOnly
        )
        if folder:
            self.path_edit.setText(folder)

    def _on_accept(self):
        self._commit_current()
        self.applied.emit({
            "index_sources": self._source_list.to_dicts(),
            "auto_index_enabled": self.auto_index_chk.isChecked(),
            "cluster_enabled": self.cluster_chk.isChecked(),
        })
        self.accept()
