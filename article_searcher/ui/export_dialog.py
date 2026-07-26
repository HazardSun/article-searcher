"""
导出对话框（功能13）

选择导出格式（Markdown / CSV）与要导出的字段，确认后由调用方执行 core.exporter。
字段默认勾选：文件路径 / 文件名 / 片段 / 评分；可选：语义分 / 词法分。
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QRadioButton, QButtonGroup,
    QCheckBox, QPushButton, QGroupBox, QFrame,
)
from PyQt6.QtCore import Qt

from core.exporter import DEFAULT_FIELDS, FIELD_LABELS

# 全部可选字段（顺序即对话框展示顺序）
ALL_FIELDS = ["file_path", "filename", "snippet", "score",
              "semantic_score", "lexical_score"]


class ExportDialog(QDialog):
    """导出格式与字段选择对话框"""

    def __init__(self, parent=None, result_count: int = 0):
        super().__init__(parent)
        self.setWindowTitle("导出搜索结果")
        self.setMinimumWidth(360)
        self._setup_ui(result_count)

    def _setup_ui(self, result_count: int):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        info = QLabel(f"将导出当前 {result_count} 条结果" if result_count
                      else "当前没有可导出的结果（仍可生成空表）")
        info.setObjectName("subtitle")
        layout.addWidget(info)

        # 格式
        fmt_group = QGroupBox("导出格式")
        fmt_layout = QHBoxLayout(fmt_group)
        self._fmt_btn = QButtonGroup(self)
        self.md_radio = QRadioButton("Markdown (.md)")
        self.csv_radio = QRadioButton("CSV (.csv)")
        self.md_radio.setChecked(True)
        self._fmt_btn.addButton(self.md_radio, 0)
        self._fmt_btn.addButton(self.csv_radio, 1)
        fmt_layout.addWidget(self.md_radio)
        fmt_layout.addWidget(self.csv_radio)
        fmt_layout.addStretch()
        layout.addWidget(fmt_group)

        # 字段
        field_group = QGroupBox("导出字段")
        field_layout = QVBoxLayout(field_group)
        self._field_boxes = {}
        for f in ALL_FIELDS:
            cb = QCheckBox(FIELD_LABELS.get(f, f))
            cb.setChecked(f in DEFAULT_FIELDS)
            self._field_boxes[f] = cb
            field_layout.addWidget(cb)
        layout.addWidget(field_group)

        # 按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        ok_btn = QPushButton("导出")
        ok_btn.setObjectName("primary")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

    def get_format(self) -> str:
        return "md" if self._fmt_btn.checkedId() == 0 else "csv"

    def get_fields(self) -> list:
        return [f for f, cb in self._field_boxes.items() if cb.isChecked()]
