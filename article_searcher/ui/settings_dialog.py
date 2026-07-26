"""
设置对话框
集中管理：运行设备、Embedding 模型、主题、返回结果数、检索模式与切片参数。
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QComboBox, QLineEdit,
    QSpinBox, QDialogButtonBox, QLabel,
)
from PyQt6.QtCore import pyqtSignal

from core.config import AppConfig

_MODE_MAP = {
    "hybrid": "混合检索",
    "semantic": "语义检索",
    "keyword": "关键词检索",
}
_MODE_INV = {v: k for k, v in _MODE_MAP.items()}


class SettingsDialog(QDialog):
    applied = pyqtSignal(dict)

    def __init__(self, engine, config: AppConfig, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.config = config
        self.setWindowTitle("设置")
        self.setMinimumWidth(460)
        self._setup_ui()
        self._load()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        form = QFormLayout()
        form.setSpacing(12)

        self.device_combo = QComboBox()
        self.device_combo.addItem("自动 (推荐)", "auto")
        for d in self.engine.embedding_engine.devices:
            self.device_combo.addItem(d.label, d.key)
        form.addRow("运行设备", self.device_combo)

        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("如 BAAI/bge-small-zh-v1.5")
        form.addRow("Embedding 模型", self.model_edit)

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["深色", "浅色"])
        form.addRow("界面主题", self.theme_combo)

        self.topk_combo = QComboBox()
        self.topk_combo.addItems(["5", "10", "20", "50"])
        form.addRow("返回结果数", self.topk_combo)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(list(_MODE_MAP.values()))
        form.addRow("检索模式", self.mode_combo)

        self.chunk_spin = QSpinBox()
        self.chunk_spin.setRange(100, 3000)
        self.chunk_spin.setSingleStep(50)
        self.chunk_spin.setSuffix(" 字符")
        form.addRow("切片最大长度", self.chunk_spin)

        self.overlap_spin = QSpinBox()
        self.overlap_spin.setRange(0, 500)
        self.overlap_spin.setSuffix(" 字符")
        form.addRow("切片重叠长度", self.overlap_spin)

        layout.addLayout(form)

        hint = QLabel(
            "提示：调整「切片」参数仅对后续新建索引生效；"
            "切换设备/模型会重新加载模型，可能耗时数秒。"
        )
        hint.setWordWrap(True)
        hint.setObjectName("subtitle")
        layout.addWidget(hint)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _load(self):
        c = self.config
        idx = self.device_combo.findData(c.device)
        if idx >= 0:
            self.device_combo.setCurrentIndex(idx)
        self.model_edit.setText(c.model)
        self.theme_combo.setCurrentText("深色" if c.theme == "dark" else "浅色")
        idx = self.topk_combo.findText(str(c.top_k))
        if idx >= 0:
            self.topk_combo.setCurrentIndex(idx)
        self.mode_combo.setCurrentText(_MODE_MAP.get(c.search_mode, "混合检索"))
        self.chunk_spin.setValue(c.chunk_max)
        self.overlap_spin.setValue(c.chunk_overlap)

    def accept(self):
        data = {
            "device": self.device_combo.currentData() or "auto",
            "model": self.model_edit.text().strip() or self.config.model,
            "theme": "dark" if self.theme_combo.currentText() == "深色" else "light",
            "top_k": int(self.topk_combo.currentText()),
            "search_mode": _MODE_INV[self.mode_combo.currentText()],
            "chunk_max": self.chunk_spin.value(),
            "chunk_overlap": self.overlap_spin.value(),
        }
        self.applied.emit(data)
        super().accept()
