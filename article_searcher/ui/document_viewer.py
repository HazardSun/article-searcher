"""
文档查看器组件
支持 Markdown/HTML 渲染、段落高亮和跳转
"""

import re
from typing import List
from PyQt6.QtWidgets import (
    QTextEdit, QVBoxLayout, QWidget, QLabel, QHBoxLayout, QPushButton
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import (
    QTextCursor, QTextCharFormat, QColor, QFont
)
from bs4 import BeautifulSoup
import marko

# 渲染上限：超过该长度的内容截断显示，防止 QTextEdit 渲染巨型文档
# 导致 GUI 线程长时间阻塞（表现为整机卡死）。
_RENDER_LIMIT = 300_000        # 纯文本上限（字符）
_RICH_RENDER_LIMIT = 200_000   # Markdown/HTML 富文本渲染上限（字符）


class DocumentViewer(QWidget):
    """文档查看器 - 支持高亮和跳转，大文件自动截断渲染"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_content = ""
        self._current_file = ""
        self._highlights = []
        self._theme = "dark"
        self._full_pending = None   # (file_path, content, is_html, is_markdown) 待加载全文
        self._setup_ui()

    def set_theme(self, theme: str):
        self._theme = theme if theme in ("dark", "light") else "dark"

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

        # 大文件截断提示条（默认隐藏）
        self.truncate_bar = QWidget()
        tb_layout = QHBoxLayout(self.truncate_bar)
        tb_layout.setContentsMargins(4, 0, 4, 0)
        self.truncate_label = QLabel("")
        self.truncate_label.setObjectName("subtitle")
        tb_layout.addWidget(self.truncate_label, 1)
        self.load_full_btn = QPushButton("仍要加载全文")
        self.load_full_btn.setFixedHeight(26)
        self.load_full_btn.clicked.connect(self._load_full_anyway)
        tb_layout.addWidget(self.load_full_btn)
        self.truncate_bar.hide()
        layout.addWidget(self.truncate_bar)

        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        layout.addWidget(self.text_edit)

    def display_file(self, file_path: str, content: str, is_html: bool = False,
                     is_markdown: bool = False, theme: str = None):
        """
        显示文件内容

        Args:
            file_path: 文件路径
            content: 文件原始内容
            is_html: 是否为 HTML 文件
            is_markdown: 是否为 Markdown 文件
            theme: 'dark' 或 'light'，控制渲染配色
        """
        if theme:
            self._theme = theme
        self._current_file = file_path
        self._current_content = content
        self._clear_highlights()
        self._full_pending = None
        self.truncate_bar.hide()

        from pathlib import Path
        file_name = Path(file_path).name
        self.file_label.setText(file_name)

        # 大文件截断保护：整篇渲染巨型文档会阻塞 GUI 线程
        limit = _RICH_RENDER_LIMIT if (is_html or is_markdown) else _RENDER_LIMIT
        if len(content) > limit:
            self._full_pending = (file_path, content, is_html, is_markdown)
            shown = content[:limit]
            self.truncate_label.setText(
                f"文档较大（约 {len(content) // 1000} 千字符），已截断显示前 {limit // 1000} 千字符"
            )
            self.truncate_bar.show()
            self._render_content(shown, is_html, is_markdown)
        else:
            self._render_content(content, is_html, is_markdown)

    def _render_content(self, content: str, is_html: bool, is_markdown: bool):
        if is_html:
            self.text_edit.setHtml(self._render_html(content))
        elif is_markdown:
            self.text_edit.setHtml(self._render_markdown(content))
        else:
            self.text_edit.setPlainText(content)

    def _load_full_anyway(self):
        """用户显式要求加载全文（明确知情，可能较慢）"""
        if not self._full_pending:
            return
        file_path, content, is_html, is_markdown = self._full_pending
        self._full_pending = None
        self.truncate_bar.hide()
        self._clear_highlights()
        self._render_content(content, is_html, is_markdown)

    def highlight_and_scroll(self, start_line: int, end_line: int, search_text: str = "",
                              matched_terms: List[str] = None):
        """
        高亮指定行范围并滚动到该位置

        Args:
            start_line: 起始行号 (0-indexed)
            end_line: 结束行号
            search_text: 搜索文本（用于精确高亮，旧调用兼容）
            matched_terms: 查询命中词列表（功能1）。传入时对所有命中词做精确高亮，
                           优先级高于 search_text；两者都为空时不高亮仅滚动。
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

        # 收集需要高亮的词：优先 matched_terms，否则退化为 search_text
        terms = []
        if matched_terms:
            terms = [t for t in matched_terms if t]
        elif search_text:
            terms = [search_text]

        cursor = self.text_edit.textCursor()
        current_line = start_line

        while current_line <= end_line and block.isValid():
            block_text = block.text()
            block_lower = block_text.lower()

            for term in terms:
                tl = term.lower()
                if not tl:
                    continue
                idx = block_lower.find(tl)
                while idx != -1:
                    start_pos = block.position() + idx
                    end_pos = block.position() + idx + len(term)
                    cursor.setPosition(start_pos)
                    cursor.setPosition(end_pos, QTextCursor.MoveMode.KeepAnchor)
                    cursor.mergeCharFormat(highlight_format)
                    self._highlights.append((start_pos, cursor.position()))

                    remaining = block_lower[idx + len(term):]
                    next_idx = remaining.find(tl)
                    if next_idx != -1:
                        idx = idx + len(term) + next_idx
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

        return self._wrap_html(html, self._theme)

    def _render_html(self, content: str) -> str:
        """清理并渲染 HTML"""
        try:
            soup = BeautifulSoup(content, 'lxml')
            for tag in soup.find_all(['script', 'style']):
                tag.decompose()
            body = soup.find('body')
            if body:
                return self._wrap_html(str(body), self._theme)
            return self._wrap_html(str(soup), self._theme)
        except Exception:
            return self._wrap_html(content, self._theme)

    def _wrap_html(self, body_html: str, theme: str = "dark") -> str:
        """包装 HTML 为完整的文档结构（按主题着色）"""
        if theme == "light":
            palette = dict(
                bg="#ffffff", fg="#1a1a2e", heading="#0f3460", sub="#533483",
                code_bg="#f1f5f9", border="#e2e8f0", quote_bg="#f8f9fa",
                th_bg="#e2e8f0", link="#4f46e5", mark_bg="#fde68a",
            )
        else:
            palette = dict(
                bg="#1a1a2e", fg="#e0e0e0", heading="#ffffff", sub="#a78bfa",
                code_bg="#16213e", border="#0f3460", quote_bg="#16213e",
                th_bg="#0f3460", link="#a78bfa", mark_bg="#ffff00",
            )

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
                    color: {palette['fg']};
                    background-color: {palette['bg']};
                    padding: 20px;
                    margin: 0;
                }}
                h1, h2, h3, h4, h5, h6 {{
                    color: {palette['heading']};
                    margin-top: 24px;
                    margin-bottom: 12px;
                }}
                h1 {{ font-size: 28px; border-bottom: 2px solid {palette['sub']}; padding-bottom: 8px; }}
                h2 {{ font-size: 22px; border-bottom: 1px solid {palette['border']}; padding-bottom: 6px; }}
                h3 {{ font-size: 18px; }}
                p {{ margin: 12px 0; }}
                code {{
                    background-color: {palette['code_bg']};
                    padding: 2px 6px;
                    border-radius: 4px;
                    font-family: 'Consolas', 'Courier New', monospace;
                    font-size: 13px;
                }}
                pre {{
                    background-color: {palette['code_bg']};
                    padding: 16px;
                    border-radius: 8px;
                    overflow-x: auto;
                    border: 1px solid {palette['border']};
                }}
                pre code {{ background: none; padding: 0; }}
                blockquote {{
                    border-left: 4px solid {palette['sub']};
                    margin: 12px 0;
                    padding: 8px 16px;
                    background-color: {palette['quote_bg']};
                    border-radius: 0 8px 8px 0;
                }}
                table {{ border-collapse: collapse; width: 100%; margin: 16px 0; }}
                th, td {{ border: 1px solid {palette['border']}; padding: 8px 12px; text-align: left; }}
                th {{ background-color: {palette['th_bg']}; color: {palette['heading']}; }}
                a {{ color: {palette['link']}; text-decoration: none; }}
                a:hover {{ text-decoration: underline; }}
                ul, ol {{ padding-left: 24px; }}
                li {{ margin: 4px 0; }}
                mark {{
                    background-color: {palette['mark_bg']};
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
