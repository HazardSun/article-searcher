"""
主窗口（重设计版）
三栏布局：左=标签筛选+已索引文件（常驻），中=搜索结果，右=文档/思维导图。
所有耗时操作（索引/搜索/读文件/思维导图/设备切换/单文件重建）均在后台线程执行。
"""

import os
import re
import logging
from pathlib import Path

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QLabel, QSplitter, QFileDialog, QMessageBox,
    QComboBox, QFrame, QTabWidget, QDialog, QListWidget, QButtonGroup,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QEvent
from PyQt6.QtGui import QKeySequence, QShortcut

from .styles import DARK_THEME, LIGHT_THEME
from .search_result_list import SearchResultList, GroupMode
from .document_viewer import DocumentViewer
from .tag_filter import TagFilterWidget
from .status_bar import StatusBarWidget
from .mindmap_viewer import MindMapViewer
from .settings_dialog import SettingsDialog
from .indexed_files_panel import IndexedFilesPanel
from core.query_parser import (
    parse_query, ParsedQuery, build_tag_filter_parsed, combine_parsed,
)
from .help_overlay import HelpOverlay
from .history_completer import HistoryCompleter
from .related_panel import RelatedArticlesWidget
from .export_dialog import ExportDialog
from .quick_launcher import QuickLauncher
from core.config import ConfigStore
from core.multisource import Source, SourceList
from core.watcher import IndexWatcher
from core.clustering import cluster_files
from core.backup import backup_index, restore_index, BackupMeta, BackupIncompatible
from .sources_dialog import SourcesDialog
from .cluster_panel import ClusterPanel
from .batch_action_bar import BatchActionBar
from .dashboard_dialog import DashboardDialog
from .link_graph_panel import LinkGraphPanel
from .duplicate_dialog import DuplicateDialog

logger = logging.getLogger(__name__)

_MODE_MAP = {"hybrid": "混合检索", "semantic": "语义检索", "keyword": "关键词检索"}
_MODE_INV = {v: k for k, v in _MODE_MAP.items()}


class IndexingWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, engine, sources, incremental):
        super().__init__()
        self.engine = engine
        self.sources = sources
        self.incremental = incremental

    def run(self):
        try:
            def on_progress(current, total, filename):
                self.progress.emit(current, total, filename)
            stats = self.engine.load_folder(sources=self.sources, incremental=self.incremental, progress_callback=on_progress)
            self.finished.emit(stats)
        except Exception as e:
            self.error.emit(str(e))


class SearchWorker(QThread):
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, engine, parsed, top_k, mode, tag_filter=None):
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


class DeviceSwitchWorker(QThread):
    """后台切换运行设备 / 模型，避免阻塞 GUI 线程（模型重载可能耗时数秒）"""
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, engine, device=None, model=None):
        super().__init__()
        self.engine = engine
        self.device = device
        self.model = model

    def run(self):
        try:
            if self.model and self.model != self.engine.embedding_engine.model_name:
                self.engine.set_model(self.model, device=self.device)
            elif self.device:
                self.engine.set_device(self.device)
            self.finished.emit(self.engine.get_status())
        except Exception as e:
            self.error.emit(str(e))


class ContentWorker(QThread):
    """后台读取文件全文内容，避免大文件（PDF/DOCX）解析阻塞 GUI 线程"""
    finished = pyqtSignal(str, str)   # file_path, content
    error = pyqtSignal(str, str)      # file_path, error

    def __init__(self, engine, file_path):
        super().__init__()
        self.engine = engine
        self.file_path = file_path

    def run(self):
        try:
            content = self.engine.get_file_content(self.file_path)
            self.finished.emit(self.file_path, content)
        except Exception as e:
            self.error.emit(self.file_path, str(e))


class MindmapWorker(QThread):
    """后台生成思维导图数据（可能需要读取多个文件内容，避免阻塞 GUI）"""
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, engine, results):
        super().__init__()
        self.engine = engine
        self.results = results

    def run(self):
        try:
            from core.mindmap import MindMapGenerator
            root = MindMapGenerator().generate_from_search_results(self.results, self.engine)
            self.finished.emit(root)
        except Exception as e:
            self.error.emit(str(e))


class ClusterWorker(QThread):
    """后台语义聚簇（numpy KMeans，禁止 GUI 线程直接 encode）"""
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, engine):
        super().__init__()
        self.engine = engine

    def run(self):
        try:
            clusters = cluster_files(self.engine)
            self.finished.emit(clusters)
        except Exception as e:
            self.error.emit(str(e))


class BatchWorker(QThread):
    """后台批量操作（加标签 / 重建 / 移除），带进度信号"""
    finished = pyqtSignal(str, int)   # action, count
    error = pyqtSignal(str, str)      # action, error
    progress = pyqtSignal(int, int, str)

    def __init__(self, engine, action: str, paths, tags=None):
        super().__init__()
        self.engine = engine
        self.action = action
        self.paths = list(paths)
        self.tags = list(tags or [])

    def run(self):
        try:
            if self.action == "add_tags":
                self.engine.batch_add_tags(self.paths, self.tags)
            elif self.action == "reindex":
                self.engine.batch_reindex(
                    self.paths,
                    progress_callback=lambda c, t, n: self.progress.emit(c, t, n),
                )
            elif self.action == "remove":
                self.engine.batch_remove(self.paths)
            else:
                raise ValueError(f"未知批量操作: {self.action}")
            self.finished.emit(self.action, len(self.paths))
        except Exception as e:
            self.error.emit(self.action, str(e))


class BackupWorker(QThread):
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, db_path, out_zip, meta):
        super().__init__()
        self.db_path = db_path
        self.out_zip = out_zip
        self.meta = meta

    def run(self):
        try:
            path = backup_index(self.db_path, self.out_zip, self.meta)
            self.finished.emit(path)
        except Exception as e:
            self.error.emit(str(e))


class RestoreWorker(QThread):
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, zip_path, db_path, mode, current_meta):
        super().__init__()
        self.zip_path = zip_path
        self.db_path = db_path
        self.mode = mode
        self.current_meta = current_meta

    def run(self):
        try:
            result = restore_index(
                self.zip_path, self.db_path, mode=self.mode,
                current_meta=self.current_meta,
            )
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class MainWindow(QMainWindow):
    def __init__(self, engine, config_store: ConfigStore):
        super().__init__()
        self.engine = engine
        self.config_store = config_store
        self._dark_mode = (self.config_store.config.theme != "light")
        self._pending_tag_parsed = None      # 左栏多选筛选（增量 P0-3）
        self._group_mode = GroupMode.FLAT    # 结果分组模式（增量 P0-2）
        self._init_sources_from_config()
        self._setup_ui()
        self._setup_shortcuts()
        self._apply_theme()
        self._setup_watcher()
        # 推迟重活：watcher 扫描（递归全盘已索引源）与状态刷新不阻塞首屏，
        # 改到 Qt 事件循环启动、窗口首次 paint 之后再发生。
        QTimer.singleShot(0, lambda: self._maybe_start_watcher())
        QTimer.singleShot(0, self._update_status)

    # ------------------------------------------------------------------ #
    # 初始化辅助
    # ------------------------------------------------------------------ #
    def _init_sources_from_config(self):
        """将 config 的 index_sources（或旧 last_folder）同步到 engine 多源模型。"""
        cfg = self.config_store.config
        if cfg.index_sources:
            self.engine.set_sources([Source(**s) for s in cfg.index_sources])
        elif cfg.last_folder:
            self.engine.set_sources([Source(cfg.last_folder)])

    def _setup_watcher(self):
        """创建文件监听自动索引 watcher（默认关闭，按 config 恢复）。"""
        self.watcher = IndexWatcher(
            self.engine,
            sources_getter=lambda: self.engine.sources,
            debounce_ms=self.config_store.config.auto_index_debounce_ms,
            enabled=False,
            on_status=self.status_bar.set_status,
            on_done=self._on_auto_indexed,
        )
        if self.config_store.config.auto_index_enabled:
            # 实际启动推迟到首屏之后（见 __init__ 中的 QTimer.singleShot）
            pass

    def _maybe_start_watcher(self):
        """首屏显示后异步启动 watcher（递归扫描已索引源目录）。"""
        if self.config_store.config.auto_index_enabled:
            self.watcher.start()

    def _on_auto_indexed(self, stats: dict):
        """自动增量索引完成后（来自后台线程）→ 线程安全地刷新 UI。"""
        QTimer.singleShot(0, lambda: self._apply_auto_index_done(stats))

    def _apply_auto_index_done(self, stats: dict):
        self.files_panel.refresh()
        self._update_status()
        self.tag_filter.update_tags(self.engine.get_tag_counts())
        self.cluster_panel.refresh()
        msg = (f"已自动增量更新 · 新增 {stats.get('new_files', 0)} "
               f"· 更新 {stats.get('updated_files', 0)}")
        self.status_bar.set_status(msg)

    # ------------------------------------------------------------------ #
    # UI 构建
    # ------------------------------------------------------------------ #
    def _setup_ui(self):
        self.setWindowTitle("智能文章整理与语义检索")
        self.setMinimumSize(1200, 800)
        self.resize(1500, 950)

        central = QWidget()
        central.setObjectName("central_widget")
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(16, 12, 16, 8)
        main_layout.setSpacing(10)

        # 状态栏需先创建（主区域的组件会连接其信号）
        self._status_bar = StatusBarWidget()

        self._setup_header(main_layout)
        self._setup_search_bar(main_layout)
        self._setup_chip_bar(main_layout)
        self._setup_main_area(main_layout)

        main_layout.addWidget(self._status_bar)

        self._setup_workers()
        self.statusBar().hide()

    def _setup_header(self, layout):
        header = QHBoxLayout()
        header.setSpacing(12)

        left_header = QVBoxLayout()
        left_header.setSpacing(2)
        title = QLabel("智能文章整理与语义检索")
        title.setObjectName("title")
        left_header.addWidget(title)
        self.hardware_label = QLabel("检测中...")
        self.hardware_label.setObjectName("subtitle")
        left_header.addWidget(self.hardware_label)
        header.addLayout(left_header)
        header.addStretch()

        self.folder_btn = QPushButton("管理索引源")
        self.folder_btn.setObjectName("primary")
        self.folder_btn.setFixedHeight(40)
        self.folder_btn.setToolTip("管理多索引源 / 排除规则 / 自动索引 (Ctrl+O)")
        self.folder_btn.clicked.connect(self._open_sources)
        header.addWidget(self.folder_btn)

        self.refresh_btn = QPushButton("刷新索引")
        self.refresh_btn.setFixedHeight(40)
        self.refresh_btn.setToolTip("增量刷新当前索引源 (F5)")
        self.refresh_btn.clicked.connect(self._refresh_index)
        self.refresh_btn.setEnabled(bool(self.engine.current_folder))
        header.addWidget(self.refresh_btn)

        self.dashboard_btn = QPushButton("概览")
        self.dashboard_btn.setFixedHeight(40)
        self.dashboard_btn.setToolTip("查看库概览仪表盘与快照备份/恢复")
        self.dashboard_btn.clicked.connect(self._open_dashboard)
        header.addWidget(self.dashboard_btn)

        self.dup_btn = QPushButton("重复检测")
        self.dup_btn.setFixedHeight(40)
        self.dup_btn.setToolTip("检测全库近似/重复文章（功能12）")
        self.dup_btn.clicked.connect(self._open_duplicate_dialog)
        header.addWidget(self.dup_btn)

        self.settings_btn = QPushButton("设置")
        self.settings_btn.setFixedHeight(40)
        self.settings_btn.setToolTip("打开设置 (Ctrl+,)")
        self.settings_btn.clicked.connect(self._open_settings)
        header.addWidget(self.settings_btn)

        self.theme_btn = QPushButton("浅色模式" if self._dark_mode else "深色模式")
        self.theme_btn.setFixedHeight(40)
        self.theme_btn.setToolTip("切换明暗主题 (Ctrl+T)")
        self.theme_btn.clicked.connect(self._toggle_theme)
        header.addWidget(self.theme_btn)

        # 增量（P0-1）：帮助 / 语法速查入口
        self.help_btn = QPushButton("?")
        self.help_btn.setFixedHeight(40)
        self.help_btn.setFixedWidth(40)
        self.help_btn.setToolTip("帮助 / 语法速查 (?)")
        self.help_btn.clicked.connect(self._open_help)
        header.addWidget(self.help_btn)

        layout.addLayout(header)

    def _setup_search_bar(self, layout):
        search_container = QFrame()
        search_container.setObjectName("search_container")
        search_layout = QHBoxLayout(search_container)
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_layout.setSpacing(10)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入自然语言或关键词搜索内容...  (Ctrl+F 聚焦)")
        self.search_input.returnPressed.connect(self._perform_search)
        self.search_input.setMinimumHeight(44)
        self.search_input.setClearButtonEnabled(True)
        search_layout.addWidget(self.search_input, 1)

        # 设备选择
        self.device_combo = QComboBox()
        self.device_combo.setObjectName("device_selector")
        self.device_combo.setFixedHeight(44)
        self.device_combo.setFixedWidth(150)
        self.device_combo.setToolTip("选择模型运行的硬件设备")
        self._populate_device_combo()
        self.device_combo.currentIndexChanged.connect(self._on_device_changed)
        search_layout.addWidget(self.device_combo)

        # 检索模式
        self.mode_combo = QComboBox()
        self.mode_combo.setFixedHeight(44)
        self.mode_combo.setFixedWidth(120)
        self.mode_combo.addItems(list(_MODE_MAP.values()))
        cur = _MODE_MAP.get(self.engine.search_mode, "混合检索")
        self.mode_combo.setCurrentText(cur)
        self.mode_combo.setToolTip("语义 / 关键词 / 混合检索")
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        search_layout.addWidget(self.mode_combo)

        self.top_k_combo = QComboBox()
        self.top_k_combo.addItems(["5", "10", "20", "50"])
        self.top_k_combo.setCurrentText(str(self.config_store.config.top_k))
        self.top_k_combo.setFixedHeight(44)
        self.top_k_combo.setFixedWidth(72)
        self.top_k_combo.setToolTip("返回结果数量")
        search_layout.addWidget(self.top_k_combo)

        self.search_btn = QPushButton("搜索")
        self.search_btn.setObjectName("primary")
        self.search_btn.setFixedHeight(44)
        self.search_btn.clicked.connect(self._perform_search)
        search_layout.addWidget(self.search_btn)

        self.mindmap_btn = QPushButton("思维导图")
        self.mindmap_btn.setFixedHeight(44)
        self.mindmap_btn.setToolTip("基于当前搜索结果生成思维导图")
        self.mindmap_btn.clicked.connect(self._generate_mindmap)
        self.mindmap_btn.setEnabled(False)
        search_layout.addWidget(self.mindmap_btn)

        layout.addWidget(search_container)

    def _setup_chip_bar(self, layout):
        """搜索栏下方的解析条件提示条（功能2）：实时展示 tag:/path:/-排除，可删除。"""
        self.chip_container = QFrame()
        self.chip_container.setObjectName("chip_bar")
        chip_layout = QHBoxLayout(self.chip_container)
        chip_layout.setContentsMargins(0, 0, 0, 0)
        chip_layout.setSpacing(6)

        self.parse_warn_label = QLabel("")
        self.parse_warn_label.setObjectName("warn_label")
        self.parse_warn_label.hide()
        chip_layout.addWidget(self.parse_warn_label)
        chip_layout.addStretch()

        self.chip_layout = chip_layout
        self.chip_container.hide()
        layout.addWidget(self.chip_container)

    def _render_chips(self, parsed: ParsedQuery):
        """根据解析结果渲染 chip；无过滤条件时隐藏整条。"""
        # 清空旧 chip（保留 warn 标签与 stretch）
        while self.chip_layout.count() > 2:
            item = self.chip_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        chips = []
        for t in parsed.tag_filters:
            chips.append(("tag", t, f"标签: {t}"))
        for p in parsed.path_filters:
            chips.append(("path", p, f"路径: {p}"))
        for e in parsed.exclude_terms:
            chips.append(("exclude", e, f"排除: {e}"))

        if not chips:
            self.chip_container.hide()
            return

        for kind, value, label in chips:
            chip = QPushButton(label)
            chip.setObjectName("chip")
            chip.setProperty("kind", kind)
            chip.setProperty("value", value)
            chip.clicked.connect(lambda _checked=False, k=kind, v=value: self._remove_condition(k, v))
            self.chip_layout.insertWidget(self.chip_layout.count() - 2, chip)
        self.chip_container.show()

    def _remove_condition(self, kind: str, value: str):
        """点击 chip 删除对应条件，并重新搜索。"""
        token = {
            "tag": f"tag:{value}",
            "path": f"path:{value}",
            "exclude": f"-{value}",
        }.get(kind, "")
        if not token:
            return
        raw = self.search_input.text()
        import re
        new_text = re.sub(r"\s*" + re.escape(token), "", raw).strip()
        self.search_input.setText(new_text)
        if new_text:
            self._perform_search()
        else:
            self.chip_container.hide()
            self.result_list.display_results([])

    def _show_parse_warn(self, warn: str):
        self.parse_warn_label.setText("⚠ " + warn)
        self.parse_warn_label.show()

    def _hide_parse_warn(self):
        self.parse_warn_label.hide()

    def _populate_device_combo(self):
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        self.device_combo.addItem("自动 (推荐)", "auto")
        for d in self.engine.embedding_engine.devices:
            self.device_combo.addItem(d.label, d.key)
        cur = self.config_store.config.device
        idx = self.device_combo.findData(cur)
        if idx >= 0:
            self.device_combo.setCurrentIndex(idx)
        else:
            self.device_combo.setCurrentIndex(0)
        self.device_combo.blockSignals(False)

    def _setup_main_area(self, layout):
        """三栏布局：左=标签+文件面板（常驻），中=搜索结果，右=文档/思维导图"""
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(6)
        splitter.setChildrenCollapsible(False)

        # ---- 左栏：标签筛选 + 已索引文件 ----
        left_panel = QFrame()
        left_panel.setFrameShape(QFrame.Shape.NoFrame)
        left_panel.setMinimumWidth(230)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        self.tag_filter = TagFilterWidget()
        # 增量（P0-3）：改连 tags_selected（多选 + AND/OR）
        self.tag_filter.tags_selected.connect(self._on_tag_selected)
        self.tag_filter.setMaximumHeight(260)
        left_layout.addWidget(self.tag_filter)

        # 主题簇面板（功能5）：与手动标签视觉隔离，紫色边框
        self.cluster_panel = ClusterPanel(self.engine)
        self.cluster_panel.setMaximumHeight(180)
        self.cluster_panel.cluster_selected.connect(self._on_cluster_selected)
        self.cluster_panel.cluster_cleared.connect(self._clear_cluster_filter)
        self.cluster_panel.recluster_requested.connect(self._on_recluster)
        self.cluster_panel.set_visible_enabled(self.config_store.config.cluster_enabled)
        left_layout.addWidget(self.cluster_panel)

        # 批量操作浮动条（功能8）：多选后浮出
        self.batch_bar = BatchActionBar()
        self.batch_bar.add_tags_requested.connect(self._on_batch_add_tags)
        self.batch_bar.reindex_requested.connect(self._on_batch_reindex)
        self.batch_bar.remove_requested.connect(self._on_batch_remove)
        self.batch_bar.cancelled.connect(lambda: self.batch_bar.clear())
        self.batch_bar.hide()
        left_layout.addWidget(self.batch_bar)

        self.files_panel = IndexedFilesPanel(self.engine)
        self.files_panel.file_selected.connect(self._show_file_in_viewer)
        self.files_panel.changed.connect(self._on_index_changed)
        self.files_panel.notify.connect(self.status_bar.set_status)
        self.files_panel.starred_changed.connect(self._on_starred_changed)
        self.files_panel.list_widget.itemSelectionChanged.connect(self._on_files_selection_changed)
        self.files_panel.list_widget.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        left_layout.addWidget(self.files_panel, 1)

        splitter.addWidget(left_panel)

        # ---- 中栏：搜索结果 ----
        mid_panel = QFrame()
        mid_panel.setFrameShape(QFrame.Shape.NoFrame)
        mid_panel.setMinimumWidth(260)
        mid_layout = QVBoxLayout(mid_panel)
        mid_layout.setContentsMargins(0, 0, 0, 0)
        mid_layout.setSpacing(4)

        self.result_list = SearchResultList()
        self.result_list.set_sources(self.engine.sources)
        self.result_list.item_selected.connect(self._on_result_selected)
        self.result_list.export_requested.connect(self._open_export_dialog)
        self.result_list.item_starred.connect(self._toggle_result_star)
        self.result_list.set_star_provider(lambda fp: self.engine.is_starred_file(fp))
        self.result_list.list_widget.itemSelectionChanged.connect(self._on_results_selection_changed)

        # 增量（P0-2）：分组切换控件
        group_bar = QHBoxLayout()
        group_bar.setContentsMargins(0, 0, 0, 0)
        group_bar.setSpacing(4)
        group_label = QLabel("分组:")
        group_label.setObjectName("subtitle")
        group_bar.addWidget(group_label)
        self._group_btn_group = QButtonGroup(self)
        self._group_buttons = []
        for text, mode in (
            ("扁平", GroupMode.FLAT),
            ("按文件", GroupMode.BY_FILE),
            ("按标签", GroupMode.BY_TAG),
            ("按源", GroupMode.BY_SOURCE),
        ):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setFixedHeight(28)
            b.setProperty("group_mode", mode)
            self._group_btn_group.addButton(b)
            self._group_buttons.append((b, mode))
            if mode == GroupMode.FLAT:
                b.setChecked(True)
            b.clicked.connect(lambda _checked=False, m=mode: self.result_list.set_group_mode(m))
            group_bar.addWidget(b)
        group_bar.addStretch()
        mid_layout.addLayout(group_bar)

        mid_layout.addWidget(self.result_list)

        splitter.addWidget(mid_panel)

        # ---- 右栏：文档 / 思维导图 ----
        right_panel = QFrame()
        right_panel.setFrameShape(QFrame.Shape.NoFrame)
        right_panel.setMinimumWidth(360)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)

        self.right_tabs = QTabWidget()

        doc_widget = QWidget()
        doc_layout = QVBoxLayout(doc_widget)
        doc_layout.setContentsMargins(0, 0, 0, 0)
        self.doc_viewer = DocumentViewer()
        doc_layout.addWidget(self.doc_viewer)
        self.right_tabs.addTab(doc_widget, "文档")

        mindmap_widget = QWidget()
        mindmap_layout = QVBoxLayout(mindmap_widget)
        mindmap_layout.setContentsMargins(0, 0, 0, 0)
        self.mindmap_viewer = MindMapViewer()
        mindmap_layout.addWidget(self.mindmap_viewer)
        self.right_tabs.addTab(mindmap_widget, "思维导图")

        # 相关文章（功能4）
        related_widget = QWidget()
        related_layout = QVBoxLayout(related_widget)
        related_layout.setContentsMargins(0, 0, 0, 0)
        self.related_panel = RelatedArticlesWidget(self.engine)
        self.related_panel.related_selected.connect(self._on_result_selected)
        related_layout.addWidget(self.related_panel)
        self.right_tabs.addTab(related_widget, "相关")

        # 链接图谱（功能6 / P2-6）：右栏「相关」之后新增 Tab
        linkgraph_widget = QWidget()
        linkgraph_layout = QVBoxLayout(linkgraph_widget)
        linkgraph_layout.setContentsMargins(0, 0, 0, 0)
        self.link_graph_panel = LinkGraphPanel(self.engine)
        self.link_graph_panel.set_doc_viewer(self.doc_viewer)
        self.link_graph_panel.link_jump.connect(self._on_node_jumped)
        linkgraph_layout.addWidget(self.link_graph_panel)
        self.right_tabs.addTab(linkgraph_widget, "链接图谱")
        self._link_tab_index = self.right_tabs.count() - 1
        self.right_tabs.currentChanged.connect(self._on_right_tab_changed)
        right_layout.addWidget(self.right_tabs)
        splitter.addWidget(right_panel)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 2)
        splitter.setSizes([260, 380, 760])
        layout.addWidget(splitter, 1)

    def _setup_workers(self):
        self.indexing_worker = None
        self.search_worker = None
        self._device_worker = None
        self._mindmap_worker = None
        self._content_workers = []
        self._cluster_worker = None
        self._batch_worker = None
        self._backup_worker = None
        self._restore_worker = None
        self._pending_file = None
        self._pending_doc = None
        self._search_running = False
        self._search_pending = False
        self._last_results = []
        self._last_parsed = None
        self._last_raw_query = ""
        self._cluster_filter_files = None

        # 搜索历史补全（功能3）
        self.history_completer = HistoryCompleter(
            self.config_store, on_select=self._on_history_selected)
        self.history_completer.attach(self.search_input, self._on_history_selected)
        self.search_input.installEventFilter(self)

        # Ctrl+K 快速检索浮层（功能14）
        self.quick_launcher = None

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self._focus_search)
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self._open_sources)
        QShortcut(QKeySequence("F5"), self, activated=self._refresh_index)
        QShortcut(QKeySequence("Ctrl+T"), self, activated=self._toggle_theme)
        QShortcut(QKeySequence("Ctrl+,"), self, activated=self._open_settings)
        QShortcut(QKeySequence("Ctrl+K"), self, activated=self._toggle_quick_launcher)
        QShortcut(QKeySequence("?"), self, activated=self._open_help)

    def _focus_search(self):
        self.search_input.setFocus()
        self.search_input.selectAll()

    # ------------------------------------------------------------------ #
    # 搜索历史（功能3）：聚焦空框弹出历史
    # ------------------------------------------------------------------ #
    def eventFilter(self, obj, event):
        if obj is self.search_input and event.type() == QEvent.Type.FocusIn:
            # 空框聚焦时弹出最近历史
            if not self.search_input.text().strip() and self.config_store.config.recent_searches:
                self.history_completer.show_all()
        return super().eventFilter(obj, event)

    def _on_history_selected(self, text: str):
        self.search_input.setText(text)
        self._perform_search()

    # ------------------------------------------------------------------ #
    # Ctrl+K 快速检索浮层（功能14）
    # ------------------------------------------------------------------ #
    def _toggle_quick_launcher(self):
        if self.quick_launcher is None:
            self.quick_launcher = QuickLauncher(
                self.engine, self, on_activate=self._on_launcher_activate)
        self.quick_launcher.show()
        self.quick_launcher.raise_()

    def _on_launcher_activate(self, result: dict):
        """浮层回传定位请求：切回主窗口并选中结果（不内嵌预览）。"""
        self.show()
        self.raise_()
        self.activateWindow()
        self.right_tabs.setCurrentIndex(0)
        self._on_result_selected(result)

    # ------------------------------------------------------------------ #
    # 导出（功能13）
    # ------------------------------------------------------------------ #
    def _open_export_dialog(self):
        if not self._last_results:
            QMessageBox.information(self, "导出", "当前没有可导出的搜索结果。")
            return
        dlg = ExportDialog(self, result_count=len(self._last_results))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        fmt = dlg.get_format()
        fields = dlg.get_fields()
        if not fields:
            QMessageBox.warning(self, "导出", "请至少选择一个导出字段。")
            return
        default_name = "search_results.md" if fmt == "md" else "search_results.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出结果", default_name,
            "Markdown (*.md)" if fmt == "md" else "CSV (*.csv)",
        )
        if not path:
            return
        try:
            if fmt == "md":
                from core.exporter import export_markdown
                export_markdown(self._last_results, fields, path)
            else:
                from core.exporter import export_csv
                export_csv(self._last_results, fields, path)
            self.status_bar.set_status(f"已导出 {len(self._last_results)} 条结果到 {path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"导出过程中发生错误:\n{e}")

    # ------------------------------------------------------------------ #
    # 文件夹索引
    # ------------------------------------------------------------------ #
    def _open_sources(self):
        dlg = SourcesDialog(self.engine, self.config_store, self)
        dlg.applied.connect(self._on_sources_applied)
        dlg.exec()

    def _on_sources_applied(self, data: dict):
        sources = [Source(**s) for s in data["index_sources"]]
        self.engine.set_sources(sources)
        self.config_store.update(
            index_sources=data["index_sources"],
            auto_index_enabled=data["auto_index_enabled"],
            cluster_enabled=data["cluster_enabled"],
        )
        self._invalidate_link_cache()
        # 自动索引开关
        if data["auto_index_enabled"]:
            self.watcher.set_enabled(True)
        else:
            self.watcher.set_enabled(False)
        # 主题簇展示开关
        self.cluster_panel.set_visible_enabled(data["cluster_enabled"])
        # 重新索引新源
        if sources:
            self._load_folder(sources)

    def _load_folder(self, sources=None):
        # 单飞保护：索引进行中禁止重复触发
        if self.indexing_worker is not None and self.indexing_worker.isRunning():
            self.status_bar.set_status("索引正在进行中，请稍候…")
            return
        if sources is None:
            sources = self.engine.sources
        if not sources:
            QMessageBox.information(self, "管理索引源", "请先添加至少一个索引源。")
            return

        self.folder_btn.setText("索引中...")
        self.folder_btn.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        self.status_bar.set_status("正在初始化索引...")
        self.status_bar.show_progress(0, 100, "准备中...")

        self.indexing_worker = IndexingWorker(self.engine, list(sources), incremental=True)
        self.indexing_worker.progress.connect(self._on_indexing_progress)
        self.indexing_worker.finished.connect(self._on_indexing_finished)
        self.indexing_worker.error.connect(self._on_indexing_error)
        self.indexing_worker.start()

    def _refresh_index(self):
        if self.engine.sources:
            self._load_folder(self.engine.sources)

    def _on_indexing_progress(self, current: int, total: int, filename: str):
        pct = int(current / total * 100) if total > 0 else 0
        self.status_bar.show_progress(current, total, f"正在处理: {filename}")
        self.status_bar.set_status(f"索引中 {current}/{total} ({pct}%)")

    def _on_indexing_finished(self, stats):
        self.status_bar.hide_progress()
        self.status_bar.set_stats(stats.get("total_files", 0), stats.get("total_chunks", 0))

        self.folder_btn.setText("管理索引源")
        self.folder_btn.setEnabled(True)
        self.refresh_btn.setEnabled(True)

        # 旧 last_folder 兼容字段：保留首个源路径
        if self.engine.current_folder:
            self.config_store.update(last_folder=self.engine.current_folder)
        tags = self.engine.get_tag_counts()
        self.tag_filter.update_tags(tags)
        self.result_list.set_sources(self.engine.sources)
        self.files_panel.refresh()
        self.cluster_panel.refresh()
        self._update_status()
        self._invalidate_link_cache()

        # 索引完成后若开启自动索引，重启监听以覆盖最新源路径
        if self.config_store.config.auto_index_enabled:
            self.watcher.restart()

        # 非模态：完成信息展示在状态栏，仅错误时弹窗
        src_n = stats.get("sources", 0)
        msg = (
            f"索引完成 | 源 {src_n} · 文件 {stats.get('total_files', 0)} · 新增 {stats.get('new_files', 0)}"
            f" · 更新 {stats.get('updated_files', 0)} · 切片 {stats.get('total_chunks', 0)}"
            + (f" · 孤儿清理 {stats.get('orphans_removed', 0)}" if stats.get("orphans_removed") else "")
        )
        self.status_bar.set_status(msg)
        if stats.get("errors"):
            detail = "\n".join(stats.get("errors", [])[:8])
            QMessageBox.warning(
                self, "部分文件处理失败",
                f"{len(stats.get('errors', []))} 个文件处理失败：\n{detail}",
            )

    def _on_indexing_error(self, error_msg):
        self.status_bar.hide_progress()
        self.status_bar.set_status("索引失败")
        self.folder_btn.setText("管理索引源")
        self.folder_btn.setEnabled(True)
        self.refresh_btn.setEnabled(bool(self.engine.current_folder))
        QMessageBox.critical(self, "索引错误", f"索引过程中发生错误:\n{error_msg}")

    # ------------------------------------------------------------------ #
    # 主题簇（功能5）
    # ------------------------------------------------------------------ #
    def _on_cluster_selected(self, files: list):
        self.files_panel.apply_path_filter(list(files))
        self._cluster_filter_files = set(files)
        self.status_bar.set_status(f"已按主题簇过滤：{len(files)} 个文件")

    def _clear_cluster_filter(self):
        self.files_panel.clear_path_filter()
        self._cluster_filter_files = None
        self.status_bar.set_status("已清除主题簇筛选")

    def _on_recluster(self):
        if self._cluster_worker is not None and self._cluster_worker.isRunning():
            self.status_bar.set_status("聚类正在进行中，请稍候…")
            return
        if not self.engine.get_status().get("total_files", 0):
            QMessageBox.information(self, "重新聚类", "当前没有已索引文件，无法聚类。")
            return
        self.status_bar.set_status("正在语义聚簇（后台）…")
        self._cluster_worker = ClusterWorker(self.engine)
        self._cluster_worker.finished.connect(self._on_cluster_done)
        self._cluster_worker.error.connect(lambda e: self.status_bar.set_status(f"聚类失败: {e}"))
        self._cluster_worker.start()

    def _on_cluster_done(self, clusters: list):
        self.engine.tag_manager.set_clusters(clusters)
        self.engine.tag_manager.save()
        self.cluster_panel.refresh()
        self.status_bar.set_status(f"聚类完成：{len(clusters)} 个主题簇")

    # ------------------------------------------------------------------ #
    # 星标（功能7）
    # ------------------------------------------------------------------ #
    def _on_starred_changed(self, path: str, starred: bool):
        self.files_panel.refresh()
        self.status_bar.set_status(f"已{'星标' if starred else '取消星标'}: {os.path.basename(path)}")
        self._update_status()

    def _toggle_result_star(self, fp: str):
        if self.engine.is_starred_file(fp):
            self.engine.unstar_file(fp)
        else:
            self.engine.star_file(fp)
        # 重新渲染结果列表以反映星标置顶/图标（若有搜索结果）
        if self.search_input.text().strip() and self._last_parsed is not None:
            self._perform_search()
        else:
            self.files_panel.refresh()
            self._update_status()

    # ------------------------------------------------------------------ #
    # 批量操作（功能8）
    # ------------------------------------------------------------------ #
    def _on_files_selection_changed(self):
        paths = self._selected_file_paths()
        self.batch_bar.set_targets(paths)

    def _on_results_selection_changed(self):
        paths = []
        for item in self.result_list.list_widget.selectedItems():
            r = item.data(Qt.ItemDataRole.UserRole)
            if r:
                fp = r.get("metadata", {}).get("file_path", "")
                if fp:
                    paths.append(fp)
        if paths:
            self.batch_bar.set_targets(paths)

    def _selected_file_paths(self):
        paths = []
        for item in self.files_panel.list_widget.selectedItems():
            p = item.data(Qt.ItemDataRole.UserRole)
            if p:
                paths.append(p)
        return paths

    def _on_batch_add_tags(self, paths):
        if not paths:
            return
        from PyQt6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(
            self, "批量加标签", "输入标签（多个用空格或逗号分隔）：",
        )
        if not ok or not text.strip():
            return
        tags = [t.strip() for t in re.split(r"[,\s]+", text.strip()) if t.strip()]
        if not tags:
            return
        self._run_batch("add_tags", paths, tags=tags)

    def _on_batch_reindex(self, paths):
        if not paths:
            return
        self._run_batch("reindex", paths)

    def _on_batch_remove(self, paths):
        if not paths:
            return
        reply = QMessageBox.question(
            self, "确认批量移除",
            f"从索引中移除选中的 {len(paths)} 个文件？（不会删除磁盘文件）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._run_batch("remove", paths)

    def _run_batch(self, action, paths, tags=None):
        if self._batch_worker is not None and self._batch_worker.isRunning():
            self.status_bar.set_status("批量操作正在进行中，请稍候…")
            return
        self.batch_bar.setVisible(True)
        self.status_bar.set_status(f"正在批量{self._batch_action_name(action)} {len(paths)} 项…")
        self._batch_worker = BatchWorker(self.engine, action, paths, tags=tags)
        self._batch_worker.progress.connect(
            lambda c, t, n: self.status_bar.show_progress(c, t, f"批量{self._batch_action_name(action)}: {n}")
        )
        self._batch_worker.finished.connect(lambda a, n: self._on_batch_done(a, n))
        self._batch_worker.error.connect(self._on_batch_error)
        self._batch_worker.start()

    @staticmethod
    def _batch_action_name(action):
        return {"add_tags": "加标签", "reindex": "重建", "remove": "移除"}.get(action, action)

    def _on_batch_done(self, action, count):
        self.status_bar.hide_progress()
        self.status_bar.set_status(f"批量{self._batch_action_name(action)}完成：{count} 项")
        self.batch_bar.clear()
        self.files_panel.refresh()
        self.tag_filter.update_tags(self.engine.get_tag_counts())
        self.cluster_panel.refresh()
        self._update_status()

    def _on_batch_error(self, action, error_msg):
        self.status_bar.hide_progress()
        self.status_bar.set_status(f"批量{self._batch_action_name(action)}失败")
        QMessageBox.critical(self, "批量操作失败", f"{error_msg}")

    # ------------------------------------------------------------------ #
    # 库概览仪表盘 + 备份/恢复（功能9 / 15）
    # ------------------------------------------------------------------ #
    def _open_dashboard(self):
        dlg = DashboardDialog(self.engine.get_status(), self)
        dlg.backup_requested.connect(self._on_backup)
        dlg.restore_requested.connect(self._on_restore)
        dlg.exec()

    def _current_backup_meta(self) -> BackupMeta:
        info = self.engine.embedding_engine.get_status_info()
        dim = self.engine.vector_store.get_embedding_dim() or self.engine.embedding_engine.dimension
        return BackupMeta(
            app_version="2.0",
            model=info.get("model", ""),
            embedding_dim=int(dim or 0),
            created_at="",
            sources=[s.to_dict() for s in self.engine.sources],
        )

    def _on_backup(self):
        if self._backup_worker is not None and self._backup_worker.isRunning():
            self.status_bar.set_status("备份正在进行中，请稍候…")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出索引快照", "article_index_backup.zip", "ZIP (*.zip)",
        )
        if not path:
            return
        self.status_bar.set_status("正在导出快照…")
        meta = self._current_backup_meta()
        self._backup_worker = BackupWorker(self.engine.vector_store.db_path, path, meta)
        self._backup_worker.finished.connect(self._on_backup_done)
        self._backup_worker.error.connect(lambda e: self.status_bar.set_status(f"备份失败: {e}"))
        self._backup_worker.start()

    def _on_backup_done(self, path):
        self.status_bar.set_status(f"已导出快照: {path}")
        QMessageBox.information(self, "导出快照", f"索引快照已保存到：\n{path}")

    def _on_restore(self):
        if self._restore_worker is not None and self._restore_worker.isRunning():
            self.status_bar.set_status("恢复正在进行中，请稍候…")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "恢复索引快照", "", "ZIP (*.zip)",
        )
        if not path:
            return
        current_meta = self._current_backup_meta()
        self.status_bar.set_status("正在恢复快照…")
        self._restore_worker = RestoreWorker(path, self.engine.vector_store.db_path, "overwrite", current_meta)
        self._restore_worker.finished.connect(self._on_restore_done)
        self._restore_worker.error.connect(self._on_restore_error)
        self._restore_worker.start()

    def _on_restore_done(self, result: dict):
        self._reload_index_state()
        self.files_panel.refresh()
        self.tag_filter.update_tags(self.engine.get_tag_counts())
        self.cluster_panel.refresh()
        self._update_status()
        self._invalidate_link_cache()
        self.status_bar.set_status(f"恢复完成：{result.get('files', 0)} 个文件。{result.get('note', '')}")
        QMessageBox.information(self, "恢复快照", f"已恢复索引。\n{result.get('note', '')}")

    def _on_restore_error(self, error_msg):
        self.status_bar.set_status("恢复失败")
        QMessageBox.critical(self, "恢复失败", f"无法恢复快照：\n{error_msg}")

    def _reload_index_state(self):
        """恢复快照后，重新加载内存中的索引状态（向量库 / 标签 / 词法）。"""
        vs = self.engine.vector_store
        vs._client = None
        vs._collection = None
        vs._index_meta = vs._load_index_meta()
        self.engine.tag_manager.load()
        self.engine.lexical._load()

    def _on_index_changed(self):
        self._update_status()
        tags = self.engine.get_tag_counts()
        self.tag_filter.update_tags(tags)

    # ------------------------------------------------------------------ #
    # 检索（单飞 + 排队最后一次）
    # ------------------------------------------------------------------ #
    def _perform_search(self):
        query = self.search_input.text().strip()
        if not query and self._pending_tag_parsed is None:
            return
        if not self.engine.current_folder:
            self.status_bar.set_status("请先选择并索引一个文件夹")
            return

        if self._search_running:
            # 已有搜索在跑：记录待办，结束后自动用最新输入再搜一次
            self._search_pending = True
            return

        top_k = int(self.top_k_combo.currentText())
        mode = _MODE_INV[self.mode_combo.currentText()]

        # 高级语法解析（功能2 / 增量 P1-1）：失败降级不崩溃
        text_parsed = parse_query(query)
        self._last_raw_query = query
        if not text_parsed.is_valid and text_parsed.warn:
            self._show_parse_warn(text_parsed.warn)
        else:
            self._hide_parse_warn()
        self._render_chips(text_parsed)

        # 增量（P0-3）：合并文本框解析与左栏多选筛选
        combined = combine_parsed(text_parsed, self._pending_tag_parsed)
        self._last_parsed = combined

        self._search_running = True
        self.status_bar.set_status(f"搜索: {query}")
        self.search_btn.setText("搜索中...")
        self.search_btn.setEnabled(False)

        self.search_worker = SearchWorker(self.engine, combined, top_k, mode)
        self.search_worker.finished.connect(self._on_search_finished)
        self.search_worker.error.connect(self._on_search_error)
        self.search_worker.start()

    def _search_done_common(self):
        self._search_running = False
        self.search_btn.setText("搜索")
        self.search_btn.setEnabled(True)
        if self._search_pending:
            self._search_pending = False
            QTimer.singleShot(0, self._perform_search)

    def _on_search_finished(self, results):
        self.result_list.display_results(results, group_mode=self._group_mode)
        self.status_bar.set_status(f"找到 {len(results)} 条结果")
        self.mindmap_btn.setEnabled(len(results) > 0)
        self._last_results = results
        self.right_tabs.setCurrentIndex(0)
        # 搜索成功 → 写入历史（功能3）
        q = self.search_input.text().strip()
        if q:
            self.history_completer.push(q)
        if results:
            self._on_result_selected(results[0])
        self._search_done_common()

    def _on_search_error(self, error_msg):
        self.status_bar.set_status("搜索失败")
        self._search_done_common()
        QMessageBox.critical(self, "搜索错误", f"搜索过程中发生错误:\n{error_msg}")

    def _on_result_selected(self, result: dict):
        meta = result.get("metadata", {})
        file_path = meta.get("file_path", "")
        if not file_path:
            return
        is_html = file_path.lower().endswith((".html", ".htm"))
        is_markdown = file_path.lower().endswith(
            (".md", ".markdown", ".mdown", ".mkd", ".mdx", ".mdwn"))
        snippet = result.get("content", "")
        start_line = meta.get("start_line", 0)
        end_line = meta.get("end_line", 0)
        matched = result.get("matched_terms", []) or []
        self._pending_doc = (file_path, is_html, is_markdown, start_line, end_line, snippet, matched)
        self.right_tabs.setCurrentIndex(0)
        # 触发相似文章推荐（功能4）
        self.related_panel.load(file_path)
        self.status_bar.set_status("正在加载文档…")
        self._load_content(file_path)

    def _show_file_in_viewer(self, file_path: str):
        is_html = file_path.lower().endswith((".html", ".htm"))
        is_markdown = file_path.lower().endswith(
            (".md", ".markdown", ".mdown", ".mkd", ".mdx", ".mdwn"))
        self._pending_doc = (file_path, is_html, is_markdown, 0, 0, "", [])
        self.result_list.reset_dedup()
        self.right_tabs.setCurrentIndex(0)
        # 文件面板双击也触发相似推荐
        self.related_panel.load(file_path)
        self.status_bar.set_status("正在加载文档…")
        self._load_content(file_path)

    def _load_content(self, file_path: str):
        """后台加载文件全文，避免 GUI 线程被阻塞"""
        self._pending_file = file_path
        worker = ContentWorker(self.engine, file_path)
        worker.finished.connect(self._on_content_loaded)
        worker.error.connect(self._on_content_error)
        worker.finished.connect(lambda *a: self._cleanup_content_worker(worker))
        worker.error.connect(lambda *a: self._cleanup_content_worker(worker))
        self._content_workers.append(worker)
        worker.start()

    def _on_content_loaded(self, file_path: str, content: str):
        if self._pending_file != file_path or not self._pending_doc:
            return  # 已有更新的请求，丢弃过期结果
        if not content:
            self.status_bar.set_status("无法读取该文件内容")
            return
        file_path, is_html, is_markdown, start_line, end_line, snippet, matched = self._pending_doc
        self.doc_viewer.set_theme("dark" if self._dark_mode else "light")
        self.doc_viewer.display_file(
            file_path, content, is_html, is_markdown,
            theme="dark" if self._dark_mode else "light",
        )
        if snippet:
            # 优先按命中词精确高亮（功能1）；无命中词时退化为片段前缀
            self.doc_viewer.highlight_and_scroll(
                start_line, end_line, snippet[:50], matched_terms=matched)
        # 同步高亮链接图谱中该文章的入/出链并填充列表（功能6）
        if hasattr(self, "link_graph_panel"):
            try:
                self.link_graph_panel.highlight_for_file(file_path)
            except Exception:  # noqa: BLE001
                pass
        self.status_bar.set_status("就绪")

    def _on_content_error(self, file_path: str, error_msg: str):
        if self._pending_file != file_path:
            return
        self.status_bar.set_status(f"读取文件失败：{error_msg}")

    def _cleanup_content_worker(self, worker):
        if worker in self._content_workers:
            self._content_workers.remove(worker)

    # ------------------------------------------------------------------ #
    # P2-6 链接图谱 / P2-12 重复检测 集成
    # ------------------------------------------------------------------ #
    def _on_right_tab_changed(self, index: int):
        """右栏 Tab 切换到「链接图谱」时触发后台构建（按需）。"""
        if getattr(self, "_link_tab_index", -1) == index:
            self.link_graph_panel.load()

    def _on_node_jumped(self, path: str):
        """画布节点点击 → 跳转对应文章（仅当为真实文件）。"""
        if path and os.path.isfile(path):
            self._show_file_in_viewer(path)

    def _on_dup_pair_selected(self, file_a: str, file_b: str):
        """相似对选中 → 跳转任一篇（优先 a，回退 b）。"""
        if file_a and os.path.isfile(file_a):
            self._show_file_in_viewer(file_a)
        elif file_b and os.path.isfile(file_b):
            self._show_file_in_viewer(file_b)

    def _open_duplicate_dialog(self):
        """打开近似/重复文章检测对话框（功能12 / P2-12）。"""
        dlg = DuplicateDialog(
            self.engine, "dark" if self._dark_mode else "light", self)
        dlg.pair_selected.connect(self._on_dup_pair_selected)
        dlg.exec()

    def _invalidate_link_cache(self):
        """索引/源变化后令链接图谱缓存失效（下次 Tab 重算）。"""
        if hasattr(self, "link_graph_panel"):
            self.link_graph_panel.invalidate()

    def _on_tag_selected(self, tags: list, op: str = "AND"):
        """增量（P0-3）：左栏多选标签 + AND/OR → 构造 pending ParsedQuery。

        空框时仅做标签浏览；有文本时与文本框解析 AND 合并后搜索。
        """
        tags = list(tags or [])
        if not tags:
            self._pending_tag_parsed = None
            if not self.search_input.text().strip():
                return
        else:
            self._pending_tag_parsed = build_tag_filter_parsed(tags, op)
        self._perform_search()

    # ------------------------------------------------------------------ #
    # 增量（P0-1）：帮助 / 语法速查浮层
    # ------------------------------------------------------------------ #
    def _open_help(self):
        """唤起语法速查浮层（顶栏 `?` 按钮或 `?` 快捷键）。"""
        dlg = HelpOverlay(self)
        dlg.exec()

    # ------------------------------------------------------------------ #
    # 思维导图（后台生成）
    # ------------------------------------------------------------------ #
    def _generate_mindmap(self):
        results = self._last_results
        if not results:
            return
        if self._mindmap_worker is not None and self._mindmap_worker.isRunning():
            self.status_bar.set_status("思维导图正在生成中…")
            return
        self.status_bar.set_status("生成思维导图...")
        self.mindmap_btn.setEnabled(False)
        self.mindmap_btn.setText("生成中...")
        self.right_tabs.setCurrentIndex(1)

        self._mindmap_worker = MindmapWorker(self.engine, results)
        self._mindmap_worker.finished.connect(self._on_mindmap_ready)
        self._mindmap_worker.error.connect(self._on_mindmap_error)
        self._mindmap_worker.start()

    def _on_mindmap_ready(self, root_node):
        self.mindmap_btn.setEnabled(True)
        self.mindmap_btn.setText("思维导图")
        try:
            self.mindmap_viewer.display_mindmap(root_node)
            self.status_bar.set_status("思维导图已生成")
        except Exception as e:
            logger.error(f"Mindmap render failed: {e}")
            self.status_bar.set_status("思维导图渲染失败")

    def _on_mindmap_error(self, error_msg):
        self.mindmap_btn.setEnabled(True)
        self.mindmap_btn.setText("思维导图")
        self.status_bar.set_status("思维导图生成失败")
        QMessageBox.warning(self, "错误", f"思维导图生成失败:\n{error_msg}")

    # ------------------------------------------------------------------ #
    # 设备 / 模式 / 主题 / 设置
    # ------------------------------------------------------------------ #
    def _on_device_changed(self, index: int):
        target = self.device_combo.currentData() or "auto"
        self.status_bar.set_status(f"正在切换到 {self.device_combo.currentText()}...")
        self.search_btn.setEnabled(False)
        self.search_btn.setText("切换中...")
        self.settings_btn.setEnabled(False)
        self.device_combo.setEnabled(False)
        self._device_worker = DeviceSwitchWorker(self.engine, device=target)
        self._device_worker.finished.connect(self._on_device_switched)
        self._device_worker.error.connect(self._on_device_switch_error)
        self._device_worker.start()

    def _on_device_switched(self, status):
        self.config_store.update(device=self.device_combo.currentData() or "auto")
        self._update_status()
        self.status_bar.set_status(f"已切换到 {self.device_combo.currentText()}")
        self.search_btn.setEnabled(True)
        self.search_btn.setText("搜索")
        self.settings_btn.setEnabled(True)
        self.device_combo.setEnabled(True)

    def _on_device_switch_error(self, error_msg):
        self.status_bar.set_status("切换失败")
        self.search_btn.setEnabled(True)
        self.search_btn.setText("搜索")
        self.settings_btn.setEnabled(True)
        self.device_combo.setEnabled(True)
        QMessageBox.warning(self, "设备切换失败", f"无法切换到该设备:\n{error_msg}")

    def _on_mode_changed(self, index: int):
        mode = _MODE_INV[self.mode_combo.currentText()]
        self.engine.set_search_mode(mode)
        self.config_store.update(search_mode=mode)
        self._update_context_bar()

    def _toggle_theme(self):
        self._dark_mode = not self._dark_mode
        self._apply_theme()
        self.theme_btn.setText("深色模式" if self._dark_mode else "浅色模式")
        self.config_store.update(theme="dark" if self._dark_mode else "light")

    def _apply_theme(self):
        theme = DARK_THEME if self._dark_mode else LIGHT_THEME
        self.setStyleSheet(theme)
        mode = "dark" if self._dark_mode else "light"
        if hasattr(self, "doc_viewer"):
            self.doc_viewer.set_theme(mode)
        if hasattr(self, "mindmap_viewer"):
            self.mindmap_viewer.set_theme(mode)
        if hasattr(self, "link_graph_panel"):
            self.link_graph_panel.set_theme(mode)

    def _open_settings(self):
        dlg = SettingsDialog(self.engine, self.config_store.config, self)
        dlg.applied.connect(self._on_settings_applied)
        dlg.exec()

    def _on_settings_applied(self, data: dict):
        self.config_store.update(**data)
        # 切片参数（影响后续索引）
        from core.chunker import ChunkConfig
        self.engine.chunker.config = ChunkConfig(
            max_chunk_size=data["chunk_max"], overlap_size=data["chunk_overlap"]
        )
        # 检索模式
        self.engine.set_search_mode(data["search_mode"])
        idx = self.mode_combo.findText(_MODE_MAP.get(data["search_mode"], "混合检索"))
        if idx >= 0:
            self.mode_combo.setCurrentIndex(idx)
        # top_k
        idx = self.top_k_combo.findText(str(data["top_k"]))
        if idx >= 0:
            self.top_k_combo.setCurrentIndex(idx)
        # 主题
        self._dark_mode = (data["theme"] != "light")
        self._apply_theme()
        self.theme_btn.setText("深色模式" if self._dark_mode else "浅色模式")

        # 设备 / 模型切换较重，放入后台线程执行（模型重载可能耗时数秒）
        new_device = data["device"]
        new_model = data["model"]
        device_changed = (new_device != self.engine.embedding_engine.device)
        model_changed = bool(new_model) and (new_model != self.engine.embedding_engine.model_name)
        if device_changed or model_changed:
            self.status_bar.set_status("正在重新加载模型…")
            self.settings_btn.setEnabled(False)
            self.device_combo.setEnabled(False)
            self._device_worker = DeviceSwitchWorker(
                self.engine,
                device=new_device,
                model=new_model if model_changed else None,
            )
            self._device_worker.finished.connect(
                lambda s: self._on_settings_device_done(s, new_device)
            )
            self._device_worker.error.connect(self._on_settings_device_error)
            self._device_worker.start()
        else:
            self._populate_device_combo()
            self._update_status()
            self.status_bar.set_status("设置已保存并生效")

    def _on_settings_device_done(self, status, new_device):
        self.settings_btn.setEnabled(True)
        self.device_combo.setEnabled(True)
        self._populate_device_combo()
        self._update_status()
        self.status_bar.set_status("设置已保存，模型已重新加载")

    def _on_settings_device_error(self, error_msg):
        self.settings_btn.setEnabled(True)
        self.device_combo.setEnabled(True)
        QMessageBox.warning(self, "设备/模型切换失败", str(error_msg))

    # ------------------------------------------------------------------ #
    # 状态
    # ------------------------------------------------------------------ #
    def _update_status(self):
        status = self.engine.get_status()
        dev_info = status.get("embedding_model") or {}
        actual = dev_info.get("actual_device", "cpu")
        model = dev_info.get("model", "").split("/")[-1]
        backend = dev_info.get("backend", "")
        hw = dev_info.get("hardware", {})
        devices = hw.get("devices", [])
        names = [d.get("name", "") for d in devices
                 if d.get("kind") in ("cuda", "dml", "npu")]

        if names:
            hw_text = f"{' / '.join(names[:2])} 等" if len(names) > 2 else " / ".join(names)
        else:
            hw_text = "仅 CPU"

        self.hardware_label.setText(f"{hw_text} | 当前: {actual} | 后端: {backend} | 模型: {model}")
        self.status_bar.set_stats(
            status.get("indexed_files", 0), status.get("total_chunks", 0),
            sources=len(status.get("sources", []) or []),
        )
        self.status_bar.set_status("就绪")
        self._actual_device = actual
        self._update_context_bar()

    def _update_context_bar(self):
        mode_text = self.mode_combo.currentText() if hasattr(self, "mode_combo") else ""
        device = getattr(self, "_actual_device", "")
        parts = [p for p in (mode_text, device) if p]
        self.status_bar.set_context(" · ".join(parts))

    # ------------------------------------------------------------------ #
    # 资源 / 生命周期：窗口关闭时优雅终止后台线程与文件监听
    # ------------------------------------------------------------------ #
    def closeEvent(self, event):
        """窗口关闭：按序终止后台 worker 与文件监听，避免进程无法退出或退出异常。

        步骤：
        1) 停止 IndexWatcher（watchdog 非守护线程，若不停止可能阻止进程退出）；
        2) 对所有后台 QThread worker 执行 quit() + wait(2000)。
        所有 worker 实例成员名均来自本文件 grep 确认（见 _setup_workers）。
        """
        # 1) 停止文件监听（watchdog 非守护线程）
        watcher = getattr(self, "watcher", None)
        if watcher is not None:
            try:
                watcher.stop()
            except Exception:  # noqa: BLE001
                pass

        # 2) 终止所有后台 QThread worker（quit + wait）
        worker_attrs = (
            "indexing_worker", "search_worker", "_device_worker",
            "_mindmap_worker", "_cluster_worker", "_batch_worker",
            "_backup_worker", "_restore_worker",
        )
        for attr in worker_attrs:
            worker = getattr(self, attr, None)
            if isinstance(worker, QThread):
                self._stop_worker(worker)

        # 内容加载 worker 为局部创建后追加进列表，统一清理
        for worker in list(getattr(self, "_content_workers", []) or []):
            self._stop_worker(worker)

        # 关闭可能存在的快速检索浮层（其为子窗口，含独立后台检索 worker）
        quick_launcher = getattr(self, "quick_launcher", None)
        if quick_launcher is not None:
            try:
                quick_launcher.close()
            except Exception:  # noqa: BLE001
                pass

        super().closeEvent(event)

    @staticmethod
    def _stop_worker(worker):
        """优雅停止单个 QThread：quit() 后最多等待 2000ms。"""
        if worker is None:
            return
        try:
            worker.quit()
        except Exception:  # noqa: BLE001
            pass
        try:
            worker.wait(2000)
        except Exception:  # noqa: BLE001
            pass

    @property
    def status_bar(self):
        return self._status_bar
