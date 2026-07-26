"""
应用内帮助 / 语法速查浮层（P0-1）

顶栏 `?` 按钮或 `?` 快捷键唤起，列出高级搜索语法速查，包括：
  tag: / path: / -排除词 / "短语" 与组合示例（含 AND/OR/NOT/括号示例）。
"""

from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton
from PyQt6.QtCore import Qt


class HelpOverlay(QDialog):
    """语法速查浮层（模态对话框）。"""

    SYNTAX_CHEATSHEET: str = (
        "<h3>高级搜索语法速查</h3>"
        "<p><b>基础过滤</b></p>"
        "<ul>"
        "<li><code>tag:技术</code> — 按标签过滤（可多个，默认取交集）</li>"
        "<li><code>path:笔记</code> — 按路径/文件名过滤（支持通配 <code>path:*.md</code>）</li>"
        "<li><code>-\"垃圾内容\"</code> 或 <code>-广告</code> — 排除含该词/短语的文档</li>"
        "<li><code>\"深度学习\"</code> — 精确短语（同时参与检索与语义强调）</li>"
        "</ul>"
        "<p><b>布尔组合（AND / OR / NOT / 括号）</b></p>"
        "<ul>"
        "<li><code>标签A 标签B</code> — 隐式 AND（相邻词默认求交）</li>"
        "<li><code>tag:A OR tag:B</code> — 标签并集（任一命中）</li>"
        "<li><code>tag:A NOT tag:B</code> — 含 A 且不含 B</li>"
        "<li><code>-tag:B</code> — 排除带 B 标签的文档</li>"
        "<li><code>\"深度学习\" OR \"神经网络\"</code> — 短语二选一命中</li>"
        "<li><code>深度学习 NOT 广告</code> — 含“深度学习”且不含“广告”</li>"
        "<li><code>(tag:A OR tag:B) NOT tag:C</code> — 括号分组改变优先级</li>"
        "</ul>"
        "<p><b>左栏多选</b>：勾选多个标签并切换「且/或」，等价于上述布尔组合。</p>"
        "<p><b>示例合集</b></p>"
        "<ul>"
        "<li><code>tag:技术 tag:教程</code> — 既是技术又是教程</li>"
        "<li><code>tag:技术 OR tag:教程</code> — 技术或教程</li>"
        "<li><code>tag:技术 NOT tag:广告</code> — 技术且非广告</li>"
        "<li><code>path:2024/*.md -广告</code> — 2024 目录下 markdown 且排除广告</li>"
        "</ul>"
        "<p style='color:#888;'>提示：按 <code>?</code> 或 <code>Esc</code> 关闭本窗口。</p>"
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("帮助 / 语法速查")
        self.setMinimumWidth(520)
        self.setMinimumHeight(420)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        label = QLabel(self.SYNTAX_CHEATSHEET)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setWordWrap(True)
        label.setOpenExternalLinks(False)
        layout.addWidget(label)

        close_btn = QPushButton("关闭 (Esc)")
        close_btn.setObjectName("primary")
        close_btn.setFixedHeight(36)
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.accept()
        else:
            super().keyPressEvent(event)
