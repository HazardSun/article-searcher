"""
搜索历史补全器（功能3）

封装 QStringListModel + QCompleter，历史经 ConfigStore 读写：
- 聚焦空搜索框时弹出最近历史（≥10 条，不足则全显示）
- 输入中按前缀/包含匹配
- 选中（回车/点击）即触发搜索回调

依赖 core.config.ConfigStore 的 recent_searches 字段与 push_recent_search。
"""

from PyQt6.QtCore import Qt, QStringListModel
from PyQt6.QtWidgets import QCompleter, QLineEdit


class HistoryCompleter:
    """搜索历史补全器"""

    def __init__(self, config_store, on_select=None):
        self.config_store = config_store
        self._on_select = on_select
        self._line_edit = None

        self.model = QStringListModel()
        self.completer = QCompleter(self.model, None)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.completer.activated.connect(self._on_activated)

        self.refresh()

    def attach(self, line_edit: QLineEdit, on_select=None):
        """绑定到搜索框 QLineEdit。on_select(text) 在选中历史项时回调。"""
        self._line_edit = line_edit
        if on_select is not None:
            self._on_select = on_select
        line_edit.setCompleter(self.completer)

    def refresh(self):
        """从 ConfigStore 重新加载历史到模型。"""
        self.model.setStringList(list(self.config_store.config.recent_searches))

    def push(self, query: str):
        """写入一条历史并刷新补全模型。"""
        self.config_store.push_recent_search(query)
        self.refresh()

    def show_all(self):
        """聚焦空框时弹出全部历史（不依赖当前输入前缀）。"""
        if self._line_edit is None:
            return
        self.completer.setCompletionPrefix("")
        self.completer.complete()

    def _on_activated(self, text: str):
        if self._on_select and text:
            self._on_select(text)
