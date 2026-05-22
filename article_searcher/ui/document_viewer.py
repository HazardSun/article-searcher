"""
文档查看器组件
支持 Markdown/HTML 渲染、段落高亮和跳转
"""

import re
from PyQt6.QtWidgets import (
    QTextEdit, QVBoxLayout, QWidget, QLabel, QHBoxLayout, QPushButton
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import (
    QTextCursor, QTextCharFormat, QColor, QFont, QTextBlockFormat
)
from bs4 import BeautifulSoup
import marko


class DocumentViewer(QWidget):
    """文档查看器 - 支持高亮和跳转"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_content = ""
        self._current_file = ""
        self._highlights = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        top_bar = QHBoxLayout()

        self.file_label = QLabel("未选择文件")
        self.file_label.setObjectName("section")
        top_bar.addWidget(self.file_label)

        top_bar.addStretch()

        self.prev_btn = QPushButton("上一个")
        self.prev_btn.clicked.connect(self._prev_highlight)
        self.prev_btn.setEnabled(False)
        top_bar.addWidget(self.prev_btn)

        self.highlight_count_label = QLabel("0/0")
        top_bar.addWidget(self.highlight_count_label)

        self.next_btn = QPushButton("下一个")
        self.next_btn.clicked.connect(self._next_highlight)
        self.next_btn.setEnabled(False)
        top_bar.addWidget(self.next_btn)

        layout.addLayout(top_bar)

        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        layout.addWidget(self.text_edit)

    def display_file(self, file_path: str, content: str, is_html: bool = False, is_markdown: bool = False):
        """
        显示文件内容

        Args:
            file_path: 文件路径
            content: 文件原始内容
            is_html: 是否为 HTML 文件
            is_markdown: 是否为 Markdown 文件
        """
        self._current_file = file_path
        self._current_content = content
        self._clear_highlights()

        from pathlib import Path
        file_name = Path(file_path).name
        self.file_label.setText(file_name)

        if is_html:
            html_content = self._render_html(content)
            self.text_edit.setHtml(html_content)
        elif is_markdown:
            html_content = self._render_markdown(content)
            self.text_edit.setHtml(html_content)
        else:
            self.text_edit.setPlainText(content)

    def highlight_and_scroll(self, start_line: int, end_line: int, search_text: str = ""):
        """
        高亮指定行范围并滚动到该位置

        Args:
            start_line: 起始行号 (0-indexed)
            end_line: 结束行号
            search_text: 搜索文本（用于精确高亮）
        """
        self._clear_highlights()

        doc = self.text_edit.document()
        block = doc.findBlockByLineNumber(start_line)

        if not block.isValid():
            blocks = []
            block = doc.firstBlock()
            while block.isValid():
                blocks.append(block)
                block = block.next()

            if start_line < len(blocks):
                block = blocks[start_line]
            else:
                return

        highlight_format = QTextCharFormat()
        highlight_format.setBackground(QColor(255, 255, 0, 100))
        highlight_format.setForeground(QColor(0, 0, 0))

        cursor = self.text_edit.textCursor()
        current_line = start_line

        while current_line <= end_line and block.isValid():
            block_text = block.text()

            if search_text and search_text.lower() in block_text.lower():
                idx = block_text.lower().find(search_text.lower())
                while idx != -1:
                    cursor.setPosition(block.position() + idx)
                    cursor.setPosition(
                        block.position() + idx + len(search_text),
                        QTextCursor.MoveMode.KeepAnchor
                    )
                    cursor.mergeCharFormat(highlight_format)
                    self._highlights.append((block.position() + idx, cursor.position()))

                    remaining = block_text[idx + len(search_text):]
                    next_idx = remaining.lower().find(search_text.lower())
                    if next_idx != -1:
                        idx = idx + len(search_text) + next_idx
                    else:
                        break

            block = block.next()
            current_line += 1

        if self._highlights:
            self._current_highlight_idx = 0
            self._scroll_to_highlight(0)
            self._update_highlight_nav()
        elif start_line >= 0:
            cursor.setPosition(block.position() if block.isValid() else 0)
            self.text_edit.setTextCursor(cursor)
            self.text_edit.ensureCursorVisible()

    def _scroll_to_highlight(self, index: int):
        """滚动到指定高亮位置"""
        if not self._highlights or index < 0 or index >= len(self._highlights):
            return

        cursor = self.text_edit.textCursor()
        cursor.setPosition(self._highlights[index][0])
        self.text_edit.setTextCursor(cursor)
        self.text_edit.ensureCursorVisible()

    def _next_highlight(self):
        """下一个高亮"""
        if not self._highlights:
            return
        self._current_highlight_idx = (self._current_highlight_idx + 1) % len(self._highlights)
        self._scroll_to_highlight(self._current_highlight_idx)
        self._update_highlight_nav()

    def _prev_highlight(self):
        """上一个高亮"""
        if not self._highlights:
            return
        self._current_highlight_idx = (self._current_highlight_idx - 1) % len(self._highlights)
        self._scroll_to_highlight(self._current_highlight_idx)
        self._update_highlight_nav()

    def _update_highlight_nav(self):
        """更新导航按钮状态"""
        total = len(self._highlights)
        current = self._current_highlight_idx + 1 if total > 0 else 0
        self.highlight_count_label.setText(f"{current}/{total}")
        self.prev_btn.setEnabled(total > 1)
        self.next_btn.setEnabled(total > 1)

    def _clear_highlights(self):
        """清除所有高亮"""
        self._highlights = []
        self._current_highlight_idx = 0
        self.highlight_count_label.setText("0/0")
        self.prev_btn.setEnabled(False)
        self.next_btn.setEnabled(False)

    def _render_markdown(self, content: str) -> str:
        """将 Markdown 转换为 HTML"""
        try:
            html = marko.convert(content)
        except Exception:
            html = f"<pre>{content}</pre>"

        return self._wrap_html(html)

    def _render_html(self, content: str) -> str:
        """清理并渲染 HTML"""
        try:
            soup = BeautifulSoup(content, 'lxml')
            for tag in soup.find_all(['script', 'style']):
                tag.decompose()
            body = soup.find('body')
            if body:
                return self._wrap_html(str(body))
            return self._wrap_html(str(soup))
        except Exception:
            return self._wrap_html(content)

    def _wrap_html(self, body_html: str) -> str:
        """包装 HTML 为完整的文档结构"""
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{
                    font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
                    font-size: 14px;
                    line-height: 1.6;
                    color: #e0e0e0;
                    background-color: #1a1a2e;
                    padding: 20px;
                    margin: 0;
                }}
                h1, h2, h3, h4, h5, h6 {{
                    color: #ffffff;
                    margin-top: 24px;
                    margin-bottom: 12px;
                }}
                h1 {{ font-size: 28px; border-bottom: 2px solid #533483; padding-bottom: 8px; }}
                h2 {{ font-size: 22px; border-bottom: 1px solid #0f3460; padding-bottom: 6px; }}
                h3 {{ font-size: 18px; }}
                p {{ margin: 12px 0; }}
                code {{
                    background-color: #16213e;
                    padding: 2px 6px;
                    border-radius: 4px;
                    font-family: 'Consolas', 'Courier New', monospace;
                    font-size: 13px;
                }}
                pre {{
                    background-color: #16213e;
                    padding: 16px;
                    border-radius: 8px;
                    overflow-x: auto;
                    border: 1px solid #0f3460;
                }}
                pre code {{
                    background: none;
                    padding: 0;
                }}
                blockquote {{
                    border-left: 4px solid #533483;
                    margin: 12px 0;
                    padding: 8px 16px;
                    background-color: #16213e;
                    border-radius: 0 8px 8px 0;
                }}
                table {{
                    border-collapse: collapse;
                    width: 100%;
                    margin: 16px 0;
                }}
                th, td {{
                    border: 1px solid #0f3460;
                    padding: 8px 12px;
                    text-align: left;
                }}
                th {{
                    background-color: #0f3460;
                    color: #ffffff;
                }}
                a {{
                    color: #533483;
                    text-decoration: none;
                }}
                a:hover {{
                    text-decoration: underline;
                }}
                ul, ol {{
                    padding-left: 24px;
                }}
                li {{
                    margin: 4px 0;
                }}
                mark {{
                    background-color: #ffff00;
                    color: #000000;
                    padding: 2px 4px;
                    border-radius: 2px;
                }}
            </style>
        </head>
        <body>
            {body_html}
        </body>
        </html>
        """
