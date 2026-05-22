"""
主窗口 (优化版)
"""

import os
import logging
from pathlib import Path

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QLabel, QSplitter,
    QFileDialog, QMessageBox, QComboBox, QFrame,
    QGraphicsDropShadowEffect
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QPropertyAnimation, QEasingCurve, QTimer
from PyQt6.QtGui import QFont, QColor

from .styles import DARK_THEME, LIGHT_THEME
from .search_result_list import SearchResultList
from .document_viewer import DocumentViewer
from .tag_filter import TagFilterWidget
from .status_bar import StatusBarWidget
from .mindmap_viewer import MindMapViewer

logger = logging.getLogger(__name__)


class IndexingWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, engine, folder_path, incremental):
        super().__init__()
        self.engine = engine
        self.folder_path = folder_path
        self.incremental = incremental

    def run(self):
        try:
            def on_progress(current, total, filename):
                self.progress.emit(current, total, filename)
            stats = self.engine.load_folder(self.folder_path, self.incremental, on_progress)
            self.finished.emit(stats)
        except Exception as e:
            self.error.emit(str(e))


class SearchWorker(QThread):
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, engine, query, top_k, tag_filter):
        super().__init__()
        self.engine = engine
        self.query = query
        self.top_k = top_k
        self.tag_filter = tag_filter

    def run(self):
        try:
            results = self.engine.search(self.query, self.top_k, self.tag_filter)
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


class MainWindow(QMainWindow):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self._dark_mode = True
        self._setup_ui()
        self._apply_theme()
        self._update_status()

    def _setup_ui(self):
        self.setWindowTitle("智能文章整理与语义检索")
        self.setMinimumSize(1300, 850)
        self.resize(1500, 950)

        central = QWidget()
        central.setObjectName("central_widget")
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(16)

        self._setup_header(main_layout)
        self._setup_search_bar(main_layout)
        self._setup_tag_filter(main_layout)
        self._setup_vertical_splitter(main_layout)
        self._setup_workers()

        self.statusBar().hide()

    def _setup_header(self, layout):
        header = QHBoxLayout()
        header.setSpacing(16)

        left_header = QVBoxLayout()
        left_header.setSpacing(4)
        
        title = QLabel("智能文章整理与语义检索")
        title.setObjectName("title")
        left_header.addWidget(title)

        self.hardware_label = QLabel("检测中...")
        self.hardware_label.setObjectName("subtitle")
        left_header.addWidget(self.hardware_label)

        header.addLayout(left_header)
        header.addStretch()

        self.folder_btn = QPushButton("选择文件夹")
        self.folder_btn.setObjectName("primary")
        self.folder_btn.setFixedHeight(44)
        self.folder_btn.clicked.connect(self._select_folder)
        header.addWidget(self.folder_btn)

        self.refresh_btn = QPushButton("刷新索引")
        self.refresh_btn.setFixedHeight(44)
        self.refresh_btn.clicked.connect(self._refresh_index)
        self.refresh_btn.setEnabled(False)
        header.addWidget(self.refresh_btn)

        self.theme_btn = QPushButton("浅色模式")
        self.theme_btn.setFixedHeight(44)
        self.theme_btn.clicked.connect(self._toggle_theme)
        header.addWidget(self.theme_btn)

        layout.addLayout(header)

    def _setup_search_bar(self, layout):
        search_container = QFrame()
        search_container.setObjectName("search_container")
        search_layout = QHBoxLayout(search_container)
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_layout.setSpacing(12)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入自然语言搜索内容，支持语义模糊匹配...")
        self.search_input.returnPressed.connect(self._perform_search)
        self.search_input.setMinimumHeight(48)
        search_layout.addWidget(self.search_input, 1)

        self.device_combo = QComboBox()
        self.device_combo.setObjectName("device_selector")
        self.device_combo.setFixedHeight(48)
        self.device_combo.setFixedWidth(90)
        self.device_combo.setToolTip("选择模型运行硬件")

        gpu_devices = self.engine.embedding_engine.hardware_info.get('gpu_devices', [])
        self.device_combo.addItem("自动")
        self._device_map = {}
        for gpu in gpu_devices:
            label = f"GPU ({gpu['name']})"
            key = gpu['key']
            self.device_combo.addItem(label)
            self._device_map[label] = key
        if 'npu' in self.engine.embedding_engine.available_devices:
            self.device_combo.addItem("NPU")
        self.device_combo.addItem("CPU")

        current_dev = self.engine.embedding_engine.device
        self._combo_mute = True
        if current_dev == 'npu':
            self.device_combo.setCurrentText("NPU")
        elif current_dev == 'cpu':
            self.device_combo.setCurrentText("CPU")
        elif current_dev == 'cuda':
            gpu = next((g for g in gpu_devices if g['type'] == 'cuda'), None)
            if gpu:
                self.device_combo.setCurrentText(f"GPU ({gpu['name']})")
            else:
                self.device_combo.setCurrentIndex(0)
        elif current_dev.startswith('directml:'):
            gpu = next((g for g in gpu_devices if g['key'] == current_dev), None)
            if gpu:
                self.device_combo.setCurrentText(f"GPU ({gpu['name']})")
            else:
                self.device_combo.setCurrentIndex(0)
        else:
            self.device_combo.setCurrentIndex(0)
        self._combo_mute = False

        self.device_combo.currentIndexChanged.connect(self._on_device_changed)
        
        search_layout.addWidget(self.device_combo)

        self.top_k_combo = QComboBox()
        self.top_k_combo.addItems(["5", "10", "20", "50"])
        self.top_k_combo.setCurrentText("10")
        self.top_k_combo.setFixedHeight(48)
        self.top_k_combo.setFixedWidth(80)
        search_layout.addWidget(self.top_k_combo)

        self.search_btn = QPushButton("搜索")
        self.search_btn.setObjectName("primary")
        self.search_btn.setFixedHeight(48)
        self.search_btn.setFixedWidth(100)
        self.search_btn.clicked.connect(self._perform_search)
        search_layout.addWidget(self.search_btn)

        self.mindmap_btn = QPushButton("思维导图")
        self.mindmap_btn.setObjectName("primary")
        self.mindmap_btn.setFixedHeight(48)
        self.mindmap_btn.setFixedWidth(100)
        self.mindmap_btn.clicked.connect(self._generate_mindmap)
        self.mindmap_btn.setEnabled(False)
        search_layout.addWidget(self.mindmap_btn)

        layout.addWidget(search_container)

    def _on_device_changed(self, index: int):
        """处理设备切换"""
        if getattr(self, '_combo_mute', False):
            return

        text = self.device_combo.currentText()
        if text == "自动":
            target = "auto"
        elif text == "CPU":
            target = "cpu"
        elif text == "NPU":
            target = "npu"
        elif text in getattr(self, '_device_map', {}):
            target = self._device_map[text]
        else:
            target = "auto"

        self.status_bar.set_status(f"正在切换到 {text}...")
        self.search_btn.setEnabled(False)
        self.search_btn.setText("切换中...")

        try:
            self.engine.embedding_engine.set_device(target)
            self._update_status()
            self.status_bar.set_status(f"已切换到 {text}")
        except Exception as e:
            self.status_bar.set_status("切换失败")
            QMessageBox.warning(self, "设备切换失败", f"无法切换到 {text}:\n{str(e)}")
        finally:
            self.search_btn.setEnabled(True)
            self.search_btn.setText("搜索")

    def _setup_tag_filter(self, layout):
        self.tag_filter = TagFilterWidget()
        self.tag_filter.tag_selected.connect(self._on_tag_selected)
        layout.addWidget(self.tag_filter)

    def _setup_content_splitter(self, layout):
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(6)
        splitter.setChildrenCollapsible(False)

        left_panel = QFrame()
        left_panel.setFrameShape(QFrame.Shape.NoFrame)
        left_panel.setMinimumWidth(250)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)

        result_header = QLabel("搜索结果")
        result_header.setStyleSheet("font-size: 12px; color: #8a8a9a; padding: 0 4px;")
        left_layout.addWidget(result_header)

        self.result_list = SearchResultList()
        self.result_list.item_selected.connect(self._on_result_selected)
        left_layout.addWidget(self.result_list)

        splitter.addWidget(left_panel)

        right_panel = QFrame()
        right_panel.setFrameShape(QFrame.Shape.NoFrame)
        right_panel.setMinimumWidth(350)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)

        self.right_tabs = self._setup_right_tabs()
        right_layout.addWidget(self.right_tabs)

        splitter.addWidget(right_panel)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter)

    def _setup_right_tabs(self):
        """创建右侧选项卡（文档查看器 + 思维导图）"""
        from PyQt6.QtWidgets import QTabWidget

        tabs = QTabWidget()

        doc_widget = QWidget()
        doc_layout = QVBoxLayout(doc_widget)
        doc_layout.setContentsMargins(0, 0, 0, 0)
        self.doc_viewer = DocumentViewer()
        doc_layout.addWidget(self.doc_viewer)
        tabs.addTab(doc_widget, "文档")

        mindmap_widget = QWidget()
        mindmap_layout = QVBoxLayout(mindmap_widget)
        mindmap_layout.setContentsMargins(0, 0, 0, 0)
        self.mindmap_viewer = MindMapViewer()
        mindmap_layout.addWidget(self.mindmap_viewer)
        tabs.addTab(mindmap_widget, "思维导图")

        return tabs

    def _setup_vertical_splitter(self, layout):
        v_splitter = QSplitter(Qt.Orientation.Vertical)
        v_splitter.setHandleWidth(6)
        v_splitter.setChildrenCollapsible(False)

        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        self._setup_content_splitter(content_layout)
        v_splitter.addWidget(content_widget)

        self._status_bar = StatusBarWidget()
        v_splitter.addWidget(self._status_bar)

        v_splitter.setStretchFactor(0, 1)
        v_splitter.setStretchFactor(1, 0)
        layout.addWidget(v_splitter)

    def _setup_workers(self):
        self.indexing_worker = None
        self.search_worker = None

    def _select_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "选择文章文件夹", "",
            QFileDialog.Option.ShowDirsOnly
        )
        if folder:
            self._load_folder(folder)

    def _load_folder(self, folder_path: str):
        self.folder_btn.setText("索引中...")
        self.folder_btn.setEnabled(False)
        
        self.status_bar.set_status("正在初始化索引...")
        self.status_bar.show_progress(0, 100, "准备中...")

        self.indexing_worker = IndexingWorker(
            self.engine, folder_path, incremental=True
        )
        self.indexing_worker.progress.connect(self._on_indexing_progress)
        self.indexing_worker.finished.connect(self._on_indexing_finished)
        self.indexing_worker.error.connect(self._on_indexing_error)
        self.indexing_worker.start()

    def _refresh_index(self):
        if self.engine.current_folder:
            self._load_folder(self.engine.current_folder)

    def _on_indexing_progress(self, current: int, total: int, filename: str):
        pct = int(current / total * 100) if total > 0 else 0
        self.status_bar.show_progress(current, total, f"正在处理: {filename}")
        self.status_bar.set_status(f"索引中 {current}/{total} ({pct}%)")

    def _on_indexing_finished(self, stats):
        self.status_bar.hide_progress()
        self.status_bar.set_status("索引完成")
        self.status_bar.set_stats(stats['total_files'], stats['total_chunks'])

        self.folder_btn.setText("更换文件夹")
        self.folder_btn.setEnabled(True)
        self.refresh_btn.setEnabled(True)

        tags = self.engine.get_tag_counts()
        self.tag_filter.update_tags(tags)

        msg = (
            f"索引完成!\n\n"
            f"总文件: {stats['total_files']}\n"
            f"新增: {stats['new_files']}\n"
            f"更新: {stats['updated_files']}\n"
            f"未变化: {stats['unchanged_files']}\n"
            f"总切片: {stats['total_chunks']}"
        )

        if stats['errors']:
            msg += f"\n\n错误 ({len(stats['errors'])}):\n" + "\n".join(stats['errors'][:5])

        QMessageBox.information(self, "索引完成", msg)

    def _on_indexing_error(self, error_msg):
        self.status_bar.hide_progress()
        self.status_bar.set_status("索引失败")
        self.folder_btn.setText("选择文件夹")
        self.folder_btn.setEnabled(True)
        QMessageBox.critical(self, "索引错误", f"索引过程中发生错误:\n{error_msg}")

    def _perform_search(self):
        query = self.search_input.text().strip()
        if not query:
            return

        if not self.engine.current_folder:
            QMessageBox.warning(self, "提示", "请先选择并索引一个文件夹")
            return

        top_k = int(self.top_k_combo.currentText())
        tag_filter = self.tag_filter.selected_tag

        self.status_bar.set_status(f"搜索: {query}")
        self.search_btn.setText("搜索中...")
        self.search_btn.setEnabled(False)

        self.search_worker = SearchWorker(
            self.engine, query, top_k, tag_filter
        )
        self.search_worker.finished.connect(self._on_search_finished)
        self.search_worker.error.connect(self._on_search_error)
        self.search_worker.start()

    def _on_search_finished(self, results):
        self.result_list.display_results(results)
        self.status_bar.set_status(f"找到 {len(results)} 条结果")
        self.search_btn.setText("搜索")
        self.search_btn.setEnabled(True)
        self.mindmap_btn.setEnabled(len(results) > 0)

        if results:
            self._on_result_selected(results[0])

    def _on_search_error(self, error_msg):
        self.status_bar.set_status("搜索失败")
        self.search_btn.setText("搜索")
        self.search_btn.setEnabled(True)
        QMessageBox.critical(self, "搜索错误", f"搜索过程中发生错误:\n{error_msg}")

    def _on_result_selected(self, result: dict):
        meta = result.get('metadata', {})
        file_path = meta.get('file_path', '')
        start_line = meta.get('start_line', 0)
        end_line = meta.get('end_line', 0)
        content = result.get('content', '')

        if not file_path:
            return

        file_content = self.engine.get_file_content(file_path)
        is_html = file_path.lower().endswith(('.html', '.htm'))
        is_markdown = file_path.lower().endswith(('.md',))

        self.doc_viewer.display_file(file_path, file_content, is_html, is_markdown)
        self.doc_viewer.highlight_and_scroll(start_line, end_line, content[:50])

    def _on_tag_selected(self, tag: str):
        if self.search_input.text().strip():
            self._perform_search()

    def _generate_mindmap(self):
        """生成并显示思维导图"""
        if not hasattr(self, 'mindmap_viewer') or not self.result_list.list_widget.count():
            return

        self.status_bar.set_status("生成思维导图...")
        self.right_tabs.setCurrentIndex(1)

        try:
            from core.mindmap import MindMapGenerator
            generator = MindMapGenerator()

            results = []
            for i in range(self.result_list.list_widget.count()):
                item = self.result_list.list_widget.item(i)
                if item:
                    result = item.data(Qt.ItemDataRole.UserRole)
                    if result:
                        results.append(result)

            if not results:
                return

            mindmap_root = generator.generate_from_search_results(results, self.engine)
            self.mindmap_viewer.display_mindmap(mindmap_root)
            self.status_bar.set_status("思维导图已生成")

        except Exception as e:
            logger.error(f"Mindmap generation failed: {e}")
            self.status_bar.set_status("思维导图生成失败")
            QMessageBox.warning(self, "错误", f"思维导图生成失败:\n{str(e)}")

    def _toggle_theme(self):
        self._dark_mode = not self._dark_mode
        self._apply_theme()
        self.theme_btn.setText("深色模式" if self._dark_mode else "浅色模式")

    def _apply_theme(self):
        theme = DARK_THEME if self._dark_mode else LIGHT_THEME
        self.setStyleSheet(theme)

    def _update_status(self):
        status = self.engine.get_status()
        dev_info = status['embedding_model']
        device = dev_info.get('actual_device', 'cpu')
        model = dev_info['model'].split('/')[-1]
        hw = dev_info['hardware']

        gpu_devices = hw.get('gpu_devices', [])
        gpu_name = hw.get('gpu_name', '')

        if gpu_devices:
            names = [g['name'] for g in gpu_devices]
            if len(names) == 1:
                hw_text = f"{names[0]} | 当前: {device}"
            else:
                hw_text = f"{' / '.join(names[:2])} 等 | 当前: {device}"
        elif gpu_name:
            hw_text = f"{gpu_name} | 当前: {device}"
        else:
            hw_text = f"仅 CPU | 当前: {device}"

        self.hardware_label.setText(f"{hw_text} | 模型: {model}")

        self.status_bar.set_stats(status['indexed_files'], status['total_chunks'])
        self.status_bar.set_status(f"就绪 | {hw_text}")

    @property
    def status_bar(self):
        return self._status_bar
