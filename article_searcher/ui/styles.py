"""
样式表模块 - 现代化深色模式主题 (优化版)
"""

DARK_THEME = """
/* ===== 全局样式 ===== */
QMainWindow, QDialog {
    background-color: #121218;
    color: #e8e8ed;
    font-family: 'Segoe UI Variable', 'Segoe UI', 'Microsoft YaHei UI', sans-serif;
    font-size: 13px;
}

QWidget#central_widget {
    background-color: #121218;
}

/* ===== 按钮 ===== */
QPushButton {
    background-color: #1e1e26;
    color: #e8e8ed;
    border: 1px solid #2a2a35;
    border-radius: 8px;
    padding: 10px 18px;
    font-size: 13px;
    font-weight: 500;
}

QPushButton:pressed {
    background-color: #4a4a5a;
    padding-top: 11px;
    padding-bottom: 9px;
}

QPushButton:disabled {
    background-color: #1a1a22;
    color: #5a5a6a;
    border-color: #22222a;
}

QPushButton#primary {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #6366f1, stop:1 #8b5cf6);
    border: none;
    color: #ffffff;
    font-weight: 600;
}

QPushButton#primary:hover {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #7c7ff7, stop:1 #9d78f8);
}

QPushButton#primary:pressed {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #5558e6, stop:1 #7a4de8);
}

QPushButton#icon_btn {
    background: transparent;
    border: none;
    padding: 8px;
    border-radius: 6px;
}

QPushButton#icon_btn:hover {
    background-color: #2a2a35;
}

/* ===== 输入框 ===== */
QLineEdit {
    background-color: #1e1e26;
    color: #e8e8ed;
    border: 1px solid #2a2a35;
    border-radius: 10px;
    padding: 12px 16px;
    font-size: 14px;
    selection-background-color: #6366f1;
}

QLineEdit:focus {
    border-color: #6366f1;
    background-color: #22222c;
}

QLineEdit::placeholder {
    color: #6a6a7a;
}

QTextEdit, QPlainTextEdit {
    background-color: #1e1e26;
    color: #e8e8ed;
    border: 1px solid #2a2a35;
    border-radius: 10px;
    padding: 12px;
    selection-background-color: #6366f1;
    font-size: 14px;
    line-height: 1.6;
}

QTextEdit:focus, QPlainTextEdit:focus {
    border-color: #6366f1;
}

/* ===== 标签 ===== */
QLabel {
    color: #e8e8ed;
    background-color: transparent;
}

QLabel#title {
    font-size: 26px;
    font-weight: 700;
    color: #ffffff;
    letter-spacing: -0.5px;
}

QLabel#subtitle {
    font-size: 13px;
    color: #8a8a9a;
}

QLabel#section {
    font-size: 15px;
    font-weight: 600;
    color: #a78bfa;
    padding: 8px 0 4px 0;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}

QLabel#badge {
    background-color: #2a2a35;
    color: #a78bfa;
    border-radius: 12px;
    padding: 4px 10px;
    font-size: 11px;
    font-weight: 600;
}

/* ===== 列表视图 ===== */
QListView, QListWidget {
    background-color: #18181f;
    color: #e8e8ed;
    border: 1px solid #2a2a35;
    border-radius: 12px;
    outline: none;
    padding: 4px;
}

QListView::item, QListWidget::item {
    padding: 12px 14px;
    border-radius: 8px;
    margin: 2px 4px;
    border: 1px solid transparent;
}

QListView::item:hover, QListWidget::item:hover {
    background-color: #22222c;
    border-color: #2a2a35;
}

QListView::item:selected, QListWidget::item:selected {
    background-color: #2a2a35;
    color: #ffffff;
    border-color: #6366f1;
}

/* ===== 树形视图 ===== */
QTreeView, QTreeWidget {
    background-color: #18181f;
    color: #e8e8ed;
    border: 1px solid #2a2a35;
    border-radius: 12px;
    outline: none;
    gridline-color: transparent;
    padding: 4px;
}

QTreeView::item, QTreeWidget::item {
    padding: 8px 10px;
    border-radius: 6px;
    margin: 2px 4px;
}

QTreeView::item:hover, QTreeWidget::item:hover {
    background-color: #22222c;
}

QTreeView::item:selected, QTreeWidget::item:selected {
    background-color: #2a2a35;
    color: #ffffff;
}

QHeaderView::section {
    background-color: #1e1e26;
    color: #a0a0b0;
    padding: 10px;
    border: none;
    border-bottom: 2px solid #2a2a35;
    font-weight: 600;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}

/* ===== 滚动条 ===== */
QScrollBar:vertical {
    background-color: transparent;
    width: 8px;
    margin: 4px;
}

QScrollBar::handle:vertical {
    background-color: #3a3a4a;
    border-radius: 4px;
    min-height: 30px;
}

QScrollBar::handle:vertical:hover {
    background-color: #4a4a5a;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: none;
}

QScrollBar:horizontal {
    background-color: transparent;
    height: 8px;
    margin: 4px;
}

QScrollBar::handle:horizontal {
    background-color: #3a3a4a;
    border-radius: 4px;
    min-width: 30px;
}

/* ===== 分组框 ===== */
QGroupBox {
    background-color: #18181f;
    border: 1px solid #2a2a35;
    border-radius: 12px;
    margin-top: 16px;
    padding-top: 24px;
    font-weight: 600;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 16px;
    padding: 0 8px;
    color: #a78bfa;
    font-size: 14px;
}

/* ===== 选项卡 ===== */
QTabWidget::pane {
    background-color: #18181f;
    border: 1px solid #2a2a35;
    border-top: none;
    border-bottom-left-radius: 8px;
    border-bottom-right-radius: 8px;
}

QTabBar {
    background-color: transparent;
}

QTabBar::tab {
    background-color: #1e1e26;
    color: #8a8a9a;
    padding: 8px 20px;
    margin-right: 2px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    border: 1px solid #2a2a35;
    border-bottom: none;
    font-size: 13px;
}

QTabBar::tab:selected {
    background-color: #18181f;
    color: #ffffff;
    border-color: #6366f1;
}

QTabBar::tab:hover:!selected {
    background-color: #2a2a35;
    color: #e0e0e0;
}

/* ===== 进度条 ===== */
QProgressBar {
    background-color: #1e1e26;
    border: 1px solid #2a2a35;
    border-radius: 6px;
    text-align: center;
    height: 8px;
    color: transparent;
}

QProgressBar::chunk {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #6366f1, stop:1 #a78bfa);
    border-radius: 5px;
}

/* ===== 组合框 ===== */
QComboBox {
    background-color: #1e1e26;
    color: #e8e8ed;
    border: 1px solid #2a2a35;
    border-radius: 8px;
    padding: 8px 12px;
    min-width: 80px;
}

QComboBox:hover {
    border-color: #4a4a5a;
}

QComboBox::drop-down {
    border: none;
    padding-right: 8px;
    width: 20px;
}

QComboBox QAbstractItemView {
    background-color: #1e1e26;
    color: #e8e8ed;
    border: 1px solid #2a2a35;
    border-radius: 8px;
    selection-background-color: #6366f1;
    outline: none;
}

QComboBox#device_selector {
    background-color: #1a1a24;
    border: 1px solid #3a3a4a;
    font-weight: 600;
    color: #a78bfa;
}

QComboBox#device_selector:hover {
    border-color: #6366f1;
}

/* ===== 工具提示 ===== */
QToolTip {
    background-color: #2a2a35;
    color: #ffffff;
    border: 1px solid #4a4a5a;
    border-radius: 6px;
    padding: 8px;
    font-size: 12px;
}

/* ===== 状态栏 ===== */
QStatusBar {
    background-color: #18181f;
    color: #8a8a9a;
    border-top: 1px solid #2a2a35;
    font-size: 12px;
}

/* ===== 分割器 ===== */
QSplitter::handle {
    background-color: #2a2a35;
}

QSplitter::handle:horizontal {
    width: 4px;
    margin: 4px 0;
}

QSplitter::handle:vertical {
    height: 4px;
    margin: 0 4px;
}

QSplitter::handle:hover {
    background-color: #6366f1;
}

/* ===== 标签筛选按钮 ===== */
TagFilterWidget QPushButton {
    background-color: #1e1e26;
    color: #c0c0d0;
    border: 1px solid #2a2a35;
    border-radius: 14px;
    padding: 0 14px;
    font-size: 12px;
}

TagFilterWidget QPushButton:checked {
    background-color: #6366f1;
    border-color: #6366f1;
    color: #ffffff;
}

TagFilterWidget QPushButton:hover:!checked {
    background-color: #2a2a35;
    border-color: #4a4a5a;
}

TagFilterWidget QPushButton#filter_action {
    background-color: #2a2a35;
    border: 1px solid #3a3a4a;
    border-radius: 4px;
    font-size: 11px;
    padding: 2px 8px;
    color: #a0a0b0;
}

TagFilterWidget QPushButton#filter_action:hover {
    background-color: #3a3a4a;
    color: #e0e0e0;
}

TagFilterWidget QLineEdit#tag_search {
    background-color: #1a1a22;
    border: 1px solid #2a2a35;
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 12px;
    color: #c0c0d0;
}

TagFilterWidget QLineEdit#tag_search:focus {
    border-color: #6366f1;
    background-color: #1e1e28;
}

/* ===== 复选框 ===== */
QCheckBox {
    color: #e8e8ed;
    spacing: 8px;
}

QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 4px;
    border: 1px solid #2a2a35;
    background-color: #1e1e26;
}

QCheckBox::indicator:checked {
    background-color: #6366f1;
    border-color: #6366f1;
}

QCheckBox::indicator:hover {
    border-color: #6366f1;
}
"""

LIGHT_THEME = """
/* ===== 全局样式 ===== */
QMainWindow, QDialog {
    background-color: #f8f9fa;
    color: #1a1a2e;
    font-family: 'Segoe UI Variable', 'Segoe UI', 'Microsoft YaHei UI', sans-serif;
}

QWidget {
    background-color: #f8f9fa;
    color: #1a1a2e;
}

QPushButton {
    background-color: #ffffff;
    color: #1a1a2e;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    padding: 10px 18px;
    font-weight: 500;
}

QPushButton:hover {
    background-color: #f1f5f9;
    border-color: #cbd5e1;
}

QPushButton:pressed {
    background-color: #e2e8f0;
}

QPushButton#primary {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #4f46e5, stop:1 #7c3aed);
    border: none;
    color: #ffffff;
    font-weight: 600;
}

QPushButton#primary:hover {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #5b54e8, stop:0 #8b45ed);
}

QLineEdit, QTextEdit, QPlainTextEdit {
    background-color: #ffffff;
    color: #1a1a2e;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    padding: 12px 16px;
    selection-background-color: #4f46e5;
}

QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {
    border-color: #4f46e5;
}

QListView, QListWidget, QTreeView, QTreeWidget {
    background-color: #ffffff;
    color: #1a1a2e;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
}

QListView::item:selected, QListWidget::item:selected,
QTreeView::item:selected, QTreeWidget::item:selected {
    background-color: #4f46e5;
    color: #ffffff;
}

QHeaderView::section {
    background-color: #f1f5f9;
    color: #64748b;
    border-bottom: 2px solid #4f46e5;
}

QScrollBar:vertical {
    background-color: transparent;
    width: 8px;
}

QScrollBar::handle:vertical {
    background-color: #cbd5e1;
    border-radius: 4px;
}

QScrollBar::handle:vertical:hover {
    background-color: #94a3b8;
}

QProgressBar::chunk {
    background-color: #4f46e5;
}

QTabWidget::pane {
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-top: none;
    border-bottom-left-radius: 8px;
    border-bottom-right-radius: 8px;
}

QTabBar {
    background-color: transparent;
}

QTabBar::tab {
    background-color: #f8f9fa;
    color: #64748b;
    padding: 8px 20px;
    margin-right: 2px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    border: 1px solid #e2e8f0;
    border-bottom: none;
    font-size: 13px;
}

QTabBar::tab:selected {
    background-color: #ffffff;
    color: #1a1a2e;
    border-color: #4f46e5;
}

QTabBar::tab:hover:!selected {
    background-color: #f1f5f9;
    color: #334155;
}

QSplitter::handle {
    background-color: #e2e8f0;
}

QSplitter::handle:horizontal {
    width: 4px;
    margin: 4px 0;
}

QSplitter::handle:vertical {
    height: 4px;
    margin: 0 4px;
}

QSplitter::handle:hover {
    background-color: #4f46e5;
}

TagFilterWidget QPushButton {
    background-color: #ffffff;
    color: #334155;
    border: 1px solid #e2e8f0;
    border-radius: 14px;
    padding: 0 14px;
    font-size: 12px;
}

TagFilterWidget QPushButton:checked {
    background-color: #4f46e5;
    border-color: #4f46e5;
    color: #ffffff;
}

TagFilterWidget QPushButton:hover:!checked {
    background-color: #f1f5f9;
    border-color: #94a3b8;
}

TagFilterWidget QPushButton#filter_action {
    background-color: #f1f5f9;
    border: 1px solid #e2e8f0;
    border-radius: 4px;
    font-size: 11px;
    padding: 2px 8px;
    color: #64748b;
}

TagFilterWidget QPushButton#filter_action:hover {
    background-color: #e2e8f0;
    color: #334155;
}

TagFilterWidget QLineEdit#tag_search {
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 12px;
    color: #334155;
}

TagFilterWidget QLineEdit#tag_search:focus {
    border-color: #4f46e5;
}
"""
