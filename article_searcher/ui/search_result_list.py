"""
搜索结果列表组件
- 中栏结果项用富文本卡片（QLabel + setItemWidget）渲染：
  标题 / 文件 / 标签 / 命中片段，命中关键词用 <span> 黄色高亮（功能1）。
- 保持去重逻辑（按 chunk 标识）与动态行高（QFontMetrics 估算，防遮挡）。

导出入口（功能13）：列表头「导出」按钮发出 export_requested 信号。

增量（P0-2）：支持分组渲染。
- GroupMode 枚举：FLAT / BY_FILE / BY_TAG / BY_SOURCE。
- display_results(results, group_mode=FLAT)：默认 FLAT 与旧行为逐行一致（向后兼容）。
- 同文件多命中折叠为一篇文件卡（取最佳片段），可展开 chunk 列表。
"""

import os
import html
import math
from enum import Enum
from PyQt6.QtWidgets import (
    QListWidget, QListWidgetItem, QVBoxLayout, QWidget, QLabel, QHBoxLayout,
    QPushButton,
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QFont, QFontMetrics

# 高亮配色（黄底黑字，深浅主题通用，保证可读性）
_MARK_BG_DARK = "#fde047"
_MARK_FG_DARK = "#000000"
_MARK_BG_LIGHT = "#facc15"
_MARK_FG_LIGHT = "#000000"


class GroupMode(Enum):
    """结果分组模式（设计 §3.6）"""
    FLAT = "flat"        # 现状：每 chunk 一张卡
    BY_FILE = "file"     # 同文件多命中折叠为一篇文件卡（最佳片段 + 可展开 chunk）
    BY_TAG = "tag"       # 按结果 file_tags 分组
    BY_SOURCE = "source" # 按所属索引源根（engine.sources 路径前缀归属）分组


def _result_key(result: dict):
    """生成结果唯一标识，用于去重"""
    meta = result.get('metadata', {})
    return (
        meta.get('file_path', ''),
        meta.get('start_line', -1),
        meta.get('end_line', -1),
    )


class ResultCard(QWidget):
    """单个搜索结果的富文本卡片"""

    def __init__(self, result: dict, dark: bool = True, starred: bool = False,
                 star_handler=None, on_select=None, parent=None):
        super().__init__(parent)
        self.result = result
        self._dark = dark
        self._starred = starred
        self._star_handler = star_handler
        self._on_select = on_select
        self._mark_bg = _MARK_BG_DARK if dark else _MARK_BG_LIGHT
        self._mark_fg = _MARK_FG_DARK if dark else _MARK_FG_LIGHT
        self._snippet_plain = result.get("snippet", "") or ""
        self._tags = result.get("file_tags", []) or []
        self._setup_ui()

    def _file_path(self) -> str:
        return self.result.get("metadata", {}).get("file_path", "")

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        meta = self.result.get('metadata', {})
        similarity = self.result.get('similarity', 0) or 0
        rrf = self.result.get('rrf_score')
        mode = self.result.get('search_mode', '')
        matched_terms = self.result.get('matched_terms', []) or []
        file_name = meta.get('file_name', 'Unknown')
        title = meta.get('title', file_name) or file_name
        start_line = meta.get('start_line', 0)

        # 顶部行：标题（左，拉伸）+ 星标按钮（右）
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        score_pct = f"{similarity * 100:.1f}%"
        score_label = score_pct + (f" · RRF {rrf:.4f}" if rrf else "")
        mode_badge = f" · {mode}" if mode else ""
        title_html = self._esc(f"[{score_label}{mode_badge}] {title}")
        title_label = QLabel(title_html)
        title_label.setWordWrap(True)
        title_label.setTextFormat(Qt.TextFormat.RichText)
        title_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        top.addWidget(title_label, 1)

        if self._star_handler is not None:
            self.star_btn = QPushButton("★" if self._starred else "☆")
            self.star_btn.setObjectName("star_btn")
            self.star_btn.setFixedSize(28, 28)
            self.star_btn.setToolTip("切换星标（置顶收藏）")
            self.star_btn.clicked.connect(self._on_star_clicked)
            top.addWidget(self.star_btn)
        layout.addLayout(top)

        file_html = self._esc(f"{file_name}  ·  行 {start_line + 1}")
        tag_html = ""
        if self._tags:
            tag_html = "  ".join("#" + self._esc(t) for t in self._tags[:5])
        snippet_html = self._highlight(self._snippet_plain, matched_terms)

        body = file_html
        if tag_html:
            body += f"<br/>{tag_html}"
        body += f"<br/>{snippet_html}"

        self.label = QLabel(body)
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.label.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.label.setOpenExternalLinks(False)
        layout.addWidget(self.label)

    def _on_star_clicked(self):
        if self._star_handler is None:
            return
        self._starred = not self._starred
        self.star_btn.setText("★" if self._starred else "☆")
        self._star_handler(self._file_path())

    @staticmethod
    def _esc(text: str) -> str:
        return html.escape(text or "")

    def _highlight(self, snippet: str, matched_terms) -> str:
        """将命中词包裹为黄色高亮 span（去重叠：短词若被长词包含则跳过）。"""
        if not snippet:
            return ""
        terms = sorted(set(matched_terms or []), key=lambda t: -len(t))
        kept = []
        for t in terms:
            if not t:
                continue
            if any(t in k for k in kept):
                continue
            kept.append(t)
        esc = self._esc(snippet)
        for t in kept:
            et = self._esc(t)
            if et:
                span = (f"<span style='background-color:{self._mark_bg};"
                        f"color:{self._mark_fg};border-radius:2px;padding:0 2px;'>"
                        f"{et}</span>")
                esc = esc.replace(et, span)
        return esc

    def height_for_width(self, width: int) -> int:
        """估算卡片高度（按可用宽度折行），防止文字被遮挡。"""
        if width <= 0:
            width = 360
        fm = QFontMetrics(self.label.font())
        pad_v = 20
        char_w = max(fm.horizontalAdvance("中"), 1)
        per_line = max(1, (width - 24) // char_w)
        lines = 2  # 标题 + 文件行
        if self._tags:
            lines += 1
        snip = self._snippet_plain
        snip_lines = max(1, math.ceil(len(snip) / per_line)) if snip else 1
        lines += snip_lines
        return pad_v + lines * fm.height() + (lines - 1) * 4


class FileGroupCard(QWidget):
    """按文件分组卡片（P0-2 BY_FILE）：标题/标签/最佳片段 + 展开 chunk 列表。

    点击卡片（最佳片段区域）发射最佳 chunk 结果；展开后子 chunk 点击发射对应 chunk。
    """

    def __init__(self, file_path, results, best_result, dark=True,
                 on_select=None, parent=None):
        super().__init__(parent)
        self.file_path = file_path
        self.results = list(results)
        self.best_result = best_result
        self._dark = dark
        self._on_select = on_select
        self._expanded = False
        self._setup_ui()

    @staticmethod
    def _esc(text: str) -> str:
        return html.escape(text or "")

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)
        meta = self.best_result.get("metadata", {}) or {}
        file_name = meta.get("file_name", "Unknown")
        title = meta.get("title", file_name) or file_name
        tags = self.best_result.get("file_tags", []) or []
        snippet = self.best_result.get("snippet", "") or ""
        hit_count = len(self.results)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        self._arrow = QLabel("▸" if not self._expanded else "▾")
        top.addWidget(self._arrow)
        title_html = self._esc(f"{title}  ·  {hit_count} 处命中")
        title_label = QLabel(title_html)
        title_label.setWordWrap(True)
        title_label.setTextFormat(Qt.TextFormat.RichText)
        top.addWidget(title_label, 1)
        top.addWidget(QLabel(self._esc(file_name)))
        layout.addLayout(top)

        if tags:
            tag_html = "  ".join("#" + self._esc(t) for t in tags[:5])
            tl = QLabel(tag_html)
            tl.setWordWrap(True)
            tl.setTextFormat(Qt.TextFormat.RichText)
            layout.addWidget(tl)

        snip = QLabel(self._esc(snippet))
        snip.setWordWrap(True)
        snip.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(snip)

        self._sub_area = QWidget()
        self._sub_layout = QVBoxLayout(self._sub_area)
        self._sub_layout.setContentsMargins(16, 4, 4, 4)
        self._sub_area.hide()
        layout.addWidget(self._sub_area)

        # 头部整体可点击：选择最佳 chunk
        self._header_widget = title_label.parentWidget() or self
        title_label.mousePressEvent = lambda e: self._emit_best()
        self._arrow.mousePressEvent = lambda e: self.toggle_expand()

    def _emit_best(self):
        if self._on_select is not None:
            self._on_select(self.best_result)

    def toggle_expand(self):
        self._expanded = not self._expanded
        self._arrow.setText("▾" if self._expanded else "▸")
        if self._expanded:
            self._build_subcards()
            self._sub_area.show()
        else:
            self._sub_area.hide()

    def _build_subcards(self):
        while self._sub_layout.count():
            item = self._sub_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for r in self.results:
            card = ResultCard(r, self._dark, on_select=self._on_select)
            card.setObjectName("subcard")
            self._sub_layout.addWidget(card)

    def height_for_width(self, width: int) -> int:
        base = 80
        if self._expanded:
            base += max(1, len(self.results)) * 80
        return base


class GroupHeaderCard(QWidget):
    """通用分组卡片（P0-2 BY_TAG / BY_SOURCE）：组名 + 数量 + 最佳片段。

    点击卡片发射组内最佳（相似度最高）结果。
    """

    def __init__(self, group_name, results, dark=True, on_select=None, parent=None):
        super().__init__(parent)
        self.group_name = group_name
        self.results = list(results)
        self._dark = dark
        self._on_select = on_select
        self.best_result = max(
            self.results, key=lambda r: (r.get("similarity") or 0)) if self.results else {}
        self._setup_ui()

    @staticmethod
    def _esc(text: str) -> str:
        return html.escape(text or "")

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        name_label = QLabel(self._esc(f"▣ {self.group_name}"))
        name_label.setWordWrap(True)
        header.addWidget(name_label, 1)
        count_label = QLabel(f"{len(self.results)} 条")
        header.addWidget(count_label)
        layout.addLayout(header)

        if self.best_result:
            snippet = self.best_result.get("snippet", "") or ""
            fn = self.best_result.get("metadata", {}).get("file_name", "")
            sub = QLabel(self._esc(f"{fn}：{snippet}"))
            sub.setWordWrap(True)
            sub.setTextFormat(Qt.TextFormat.RichText)
            layout.addWidget(sub)

    def height_for_width(self, width: int) -> int:
        return 80

    def mousePressEvent(self, event):
        if self._on_select is not None and self.best_result:
            self._on_select(self.best_result)
        super().mousePressEvent(event)


class SearchResultList(QWidget):
    """搜索结果列表组件"""

    item_selected = pyqtSignal(dict)
    export_requested = pyqtSignal()
    item_starred = pyqtSignal(str)   # 点击某结果星标按钮（file_path）
    group_changed = pyqtSignal(object)  # 新增：分组模式变化（GroupMode）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_emitted_key = None
        self._results = []
        self._dark_mode = True
        self._star_handler = None
        self._star_provider = None
        self._group_mode = GroupMode.FLAT
        self._sources = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self.header_label = QLabel("搜索结果")
        self.header_label.setObjectName("section")
        header.addWidget(self.header_label)
        header.addStretch()

        self.export_btn = QPushButton("导出")
        self.export_btn.setObjectName("secondary")
        self.export_btn.setFixedHeight(28)
        self.export_btn.setToolTip("将当前结果导出为 Markdown / CSV")
        self.export_btn.clicked.connect(self.export_requested)
        header.addWidget(self.export_btn)
        layout.addLayout(header)

        self.list_widget = QListWidget()
        self.list_widget.setWordWrap(True)
        self.list_widget.setUniformItemSizes(False)
        self.list_widget.currentItemChanged.connect(self._on_item_changed)
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        # 多选支持（功能8）：Ctrl/Shift 多选触发批量操作条
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        # 右键菜单：导出
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self.list_widget)

    def set_theme(self, dark: bool):
        self._dark_mode = dark

    def set_star_handler(self, handler):
        """handler(file_path) 在点击星标按钮时调用。"""
        self._star_handler = handler

    def set_star_provider(self, provider):
        """provider(file_path) -> bool，决定星标按钮初始状态。"""
        self._star_provider = provider

    def set_sources(self, sources):
        """设置多索引源（供 BY_SOURCE 分组做路径前缀归属）。"""
        self._sources = list(sources or [])

    def _on_context_menu(self, pos):
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        act = menu.addAction("导出结果…")
        act.triggered.connect(self.export_requested)
        menu.exec(self.list_widget.mapToGlobal(pos))

    def display_results(self, results: list,
                        group_mode: GroupMode = GroupMode.FLAT) -> None:
        """显示搜索结果（增量 P0-2：支持 group_mode 分组渲染）。

        group_mode 默认 FLAT，旧调用 display_results(results) 行为不变。
        """
        self._group_mode = group_mode
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        self.list_widget.blockSignals(False)
        self._last_emitted_key = None
        self._results = list(results)

        if not results:
            item = QListWidgetItem("未找到匹配结果")
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list_widget.addItem(item)
            self.header_label.setText("搜索结果 (0 条)")
            return

        if group_mode == GroupMode.FLAT:
            self._fill_cards()
        else:
            self._fill_grouped(results, group_mode)
        self.header_label.setText(f"搜索结果 ({len(results)} 条)")
        self.group_changed.emit(group_mode)

    def _fill_cards(self):
        width = self.list_widget.viewport().width() or 360
        for result in self._results:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, result)
            fp = result.get("metadata", {}).get("file_path", "")
            starred = bool(self._star_provider(fp)) if self._star_provider else False
            card = ResultCard(
                result, self._dark_mode, starred=starred,
                star_handler=(lambda p: self.item_starred.emit(p)) if self._star_handler else None,
            )
            h = card.height_for_width(width)
            item.setSizeHint(QSize(width, h))
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, card)

    def _fill_grouped(self, results, mode: GroupMode):
        width = self.list_widget.viewport().width() or 360
        if mode == GroupMode.BY_FILE:
            self._render_by_file(results, width)
        elif mode == GroupMode.BY_TAG:
            self._render_by_tag(results, width)
        elif mode == GroupMode.BY_SOURCE:
            self._render_by_source(results, width)

    def _render_by_file(self, results, width):
        by_file = {}
        order = []
        for r in results:
            fp = r.get("metadata", {}).get("file_path", "")
            if fp not in by_file:
                by_file[fp] = []
                order.append(fp)
            by_file[fp].append(r)
        for fp in order:
            chunks = by_file[fp]
            best = max(chunks, key=lambda r: (r.get("similarity") or 0))
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, best)
            card = FileGroupCard(
                file_path=fp, results=chunks, best_result=best,
                dark=self._dark_mode, on_select=self.item_selected.emit,
                parent=self.list_widget,
            )
            h = card.height_for_width(width)
            item.setSizeHint(QSize(width, h))
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, card)

    def _render_by_tag(self, results, width):
        tag_map = {}
        untagged = []
        for r in results:
            tags = r.get("file_tags", []) or []
            if not tags:
                untagged.append(r)
                continue
            for t in tags:
                tag_map.setdefault(t, []).append(r)
        items = [(t, tag_map[t]) for t in sorted(tag_map.keys())]
        if untagged:
            items.append(("未标记", untagged))
        self._render_group_cards(items, width)

    def _render_by_source(self, results, width):
        src_map = {}
        other = []
        for r in results:
            fp = r.get("metadata", {}).get("file_path", "")
            matched = None
            for s in self._sources:
                try:
                    if os.path.abspath(fp).startswith(os.path.abspath(s.path)):
                        matched = s.path
                        break
                except Exception:
                    continue
            if matched:
                src_map.setdefault(matched, []).append(r)
            else:
                other.append(r)
        items = []
        for s in self._sources:
            if s.path in src_map:
                items.append((s.path, src_map[s.path]))
        if other:
            items.append(("其他", other))
        self._render_group_cards(items, width)

    def _render_group_cards(self, items, width):
        for name, members in items:
            item = QListWidgetItem()
            card = GroupHeaderCard(
                group_name=name, results=members,
                dark=self._dark_mode, on_select=self.item_selected.emit,
                parent=self.list_widget,
            )
            item.setData(Qt.ItemDataRole.UserRole, card.best_result)
            h = card.height_for_width(width)
            item.setSizeHint(QSize(width, h))
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, card)

    def set_group_mode(self, mode: GroupMode) -> None:
        """用当前结果以新分组模式重渲染。"""
        self._group_mode = mode
        if self._results:
            self.display_results(self._results, group_mode=mode)
        else:
            self.group_changed.emit(mode)

    def _relayout(self):
        width = self.list_widget.viewport().width() or 360
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            r = item.data(Qt.ItemDataRole.UserRole)
            if not r:
                continue
            card = self.list_widget.itemWidget(item)
            h = card.height_for_width(width) if card else 60
            item.setSizeHint(QSize(width, h))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._results:
            self._relayout()

    def reset_dedup(self):
        """外部切换了文档（如文件面板双击）后重置去重状态，
        使用户再点击同一结果项时仍能重新加载。"""
        self._last_emitted_key = None

    def _emit_result(self, result: dict):
        key = _result_key(result)
        if key == self._last_emitted_key:
            return  # 去重：同一结果不重复发射
        self._last_emitted_key = key
        self.item_selected.emit(result)

    def _on_item_changed(self, current, previous):
        if current:
            result = current.data(Qt.ItemDataRole.UserRole)
            if result:
                self._emit_result(result)

    def _on_item_clicked(self, item):
        result = item.data(Qt.ItemDataRole.UserRole)
        if result:
            self._emit_result(result)

    def clear(self):
        self.list_widget.clear()
        self._last_emitted_key = None
        self._results = []
        self.header_label.setText("搜索结果")
