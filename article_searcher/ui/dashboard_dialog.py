"""
库概览仪表盘对话框（功能9 + 功能15 入口）

展示 engine.get_status() 的六项指标：索引源 / 文件数 / 字数（近似）/
标签分布 / 簇分布 / 最近更新 / 星标数。并提供「导出快照 / 恢复快照」入口
（信号交由主窗口执行真实备份/恢复，保证与主窗口 watcher/config 协调）。
"""

import os
from datetime import datetime

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QScrollArea, QWidget, QDialogButtonBox, QMessageBox,
)
from PyQt6.QtCore import pyqtSignal


def _fmt_ts(ts) -> str:
    if not ts:
        return "—"
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts)


class _StatCard(QFrame):
    def __init__(self, title, value, parent=None):
        super().__init__(parent)
        self.setObjectName("stat_card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)
        t = QLabel(title)
        t.setObjectName("stat_title")
        v = QLabel(str(value))
        v.setObjectName("stat_value")
        layout.addWidget(t)
        layout.addWidget(v)


class DashboardDialog(QDialog):
    backup_requested = pyqtSignal()
    restore_requested = pyqtSignal()

    def __init__(self, status: dict, parent=None):
        super().__init__(parent)
        self._status = status or {}
        self.setWindowTitle("库概览")
        self.setMinimumWidth(560)
        self.setMinimumHeight(520)
        self._setup_ui()
        self._fill()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        cards = QHBoxLayout()
        cards.setSpacing(10)
        cards.addWidget(_StatCard("索引源", self._status.get("sources", []).__len__()))
        cards.addWidget(_StatCard("文件数", self._status.get("total_files", 0)))
        cards.addWidget(_StatCard("字数(近似)", self._status.get("total_chars", 0)))
        cards.addWidget(_StatCard("星标", self._status.get("starred_count", 0)))
        layout.addLayout(cards)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(10)
        scroll.setWidget(self._body)
        layout.addWidget(scroll, 1)

        btn_row = QHBoxLayout()
        self.backup_btn = QPushButton("导出快照")
        self.backup_btn.setObjectName("primary")
        self.restore_btn = QPushButton("恢复快照")
        self.restore_btn.setObjectName("secondary")
        self.backup_btn.clicked.connect(self.backup_requested.emit)
        self.restore_btn.clicked.connect(self._on_restore)
        btn_row.addWidget(self.backup_btn)
        btn_row.addWidget(self.restore_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _section(self, title: str):
        label = QLabel(title)
        label.setObjectName("dash_section")
        self._body_layout.addWidget(label)

    def _kv(self, pairs):
        frame = QFrame()
        frame.setObjectName("dash_block")
        bl = QVBoxLayout(frame)
        bl.setContentsMargins(10, 8, 10, 8)
        bl.setSpacing(3)
        for k, v in pairs:
            row = QHBoxLayout()
            kk = QLabel(k)
            kk.setObjectName("dash_key")
            vv = QLabel(str(v))
            vv.setObjectName("dash_val")
            vv.setWordWrap(True)
            row.addWidget(kk)
            row.addWidget(vv, 1)
            bl.addLayout(row)
        self._body_layout.addWidget(frame)

    def _list_block(self, title, items, empty_text="（无）"):
        self._section(title)
        if not items:
            self._kv([(empty_text, "")])
            return
        frame = QFrame()
        frame.setObjectName("dash_block")
        bl = QVBoxLayout(frame)
        bl.setContentsMargins(10, 8, 10, 8)
        bl.setSpacing(3)
        for label, val in items:
            row = QHBoxLayout()
            kk = QLabel(label)
            kk.setObjectName("dash_key")
            kk.setWordWrap(True)
            vv = QLabel(str(val))
            vv.setObjectName("dash_val")
            row.addWidget(kk, 1)
            row.addWidget(vv)
            bl.addLayout(row)
        self._body_layout.addWidget(frame)

    def _fill(self):
        st = self._status
        # 索引源明细
        sources = st.get("sources", [])
        src_items = [(os.path.basename(s.get("path", "")) or s.get("path", ""),
                      f"{s.get('files', 0)} 文件 · {'启用' if s.get('enabled') else '停用'}"
                      + (f" · 排除 {len(s.get('excluded_rules', []))} 项" if s.get('excluded_rules') else ""))
                     for s in sources] or [("（无索引源）", "")]
        self._list_block("索引源", src_items)

        # 标签分布（Top 15）
        td = st.get("tag_distribution", {}) or {}
        top_tags = sorted(td.items(), key=lambda kv: kv[1], reverse=True)[:15]
        self._list_block("标签分布", [(t, c) for t, c in top_tags],
                         empty_text="（暂无标签）")

        # 簇分布
        cd = st.get("cluster_distribution", {}) or {}
        self._list_block("主题簇分布", [(t, c) for t, c in cd.items()],
                         empty_text="（未聚类，点「重新聚类」）")

        # 最近更新
        ru = st.get("recent_updates", []) or []
        ru_items = [(os.path.basename(r.get("file_path", "")), _fmt_ts(r.get("modified_time")))
                    for r in ru[:12]]
        self._list_block("最近更新", ru_items, empty_text="（无）")

    def _on_restore(self):
        reply = QMessageBox.warning(
            self, "恢复快照", "恢复将用快照覆盖当前索引（恢复前会自动备份当前索引，可撤销）。继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.restore_requested.emit()
