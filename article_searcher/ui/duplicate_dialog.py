"""
近似 / 重复文章检测对话框（功能12 / P2-12）

独立入口：顶栏「重复检测」按钮打开 `DuplicateDialog`，展示相似文章列表
（相似对 + 相似度%），点击跳转任一篇。

- 阈值下拉（0.80/0.85/0.90/0.95，默认 0.85）；
- 「开始检测」走 `DuplicateDetectWorker`（后台，复用 get_file_vector，不 encode）；
- 列表展示「文件A ↔ 文件B · 相似度%」；
- `pair_selected(file_a, file_b)` 信号（回传主窗口定位）；
- 大库 `n>max_files` 时显示警告 badge；
- 空结果友好提示。

结果为只读分析，不写 tags.json。
"""

from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QComboBox, QFrame,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize
from PyQt6.QtGui import QFont, QFontMetrics

# 与 core.dedup.find_duplicate_pairs 的默认软上限一致
_MAX_FILES = 5000


class DuplicateDetectWorker(QThread):
    """后台执行 find_duplicate_pairs，避免阻塞 GUI 线程。"""
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, engine, threshold: float, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.threshold = threshold

    def run(self):
        try:
            pairs = self.engine.find_duplicate_pairs(threshold=self.threshold)
            self.finished.emit(pairs)
        except Exception as e:  # noqa: BLE001
            self.error.emit(str(e))


class DuplicateDialog(QDialog):
    """近似/重复文章检测对话框。"""

    pair_selected = pyqtSignal(str, str)   # (file_a, file_b)

    def __init__(self, engine, theme: str = "dark", parent=None):
        super().__init__(parent)
        self.engine = engine
        self._worker = None
        self._theme = theme if theme in ("dark", "light") else "dark"
        self.setWindowTitle("近似 / 重复文章检测")
        self.resize(720, 560)
        self._setup_ui()
        self._refresh_badge()

    def set_theme(self, theme: str):
        self._theme = theme if theme in ("dark", "light") else "dark"

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # 顶部：阈值选择 + 检测按钮 + 警告 badge
        top = QHBoxLayout()
        label = QLabel("相似度阈值")
        label.setObjectName("section")
        top.addWidget(label)

        self.threshold_combo = QComboBox()
        self.threshold_combo.setObjectName("dup_threshold")
        self.threshold_combo.addItems(["0.80", "0.85", "0.90", "0.95"])
        self.threshold_combo.setCurrentText("0.85")
        self.threshold_combo.setFixedWidth(90)
        top.addWidget(self.threshold_combo)

        self.detect_btn = QPushButton("开始检测")
        self.detect_btn.setObjectName("primary")
        self.detect_btn.setFixedHeight(36)
        self.detect_btn.clicked.connect(self._on_detect)
        top.addWidget(self.detect_btn)

        self.badge = QLabel("")
        self.badge.setObjectName("badge")
        self.badge.hide()
        top.addWidget(self.badge)

        top.addStretch()
        layout.addLayout(top)

        hint = QLabel("基于全库文件级向量（余弦相似度）找出高度相似的文章对，"
                      "用于发现重复或近似内容。检测为只读分析，不会修改任何文件。")
        hint.setObjectName("subtitle")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 结果列表
        self.result_list = QListWidget()
        self.result_list.setObjectName("dup_list")
        self.result_list.setWordWrap(True)
        self.result_list.itemClicked.connect(self._on_item_clicked)
        self.result_list.itemDoubleClicked.connect(self._on_item_double)
        layout.addWidget(self.result_list, 1)

        self.status_label = QLabel("点击「开始检测」开始。")
        self.status_label.setObjectName("subtitle")
        layout.addWidget(self.status_label)

    def _refresh_badge(self):
        """文件数超过软上限时显示警告 badge（检测将仅取前 N 篇）。"""
        try:
            n = len(self.engine.get_indexed_files())
        except Exception:  # noqa: BLE001
            n = 0
        if n > _MAX_FILES:
            self.badge.setText(f"⚠ 文件数 {n} > {_MAX_FILES}，仅检测前 {_MAX_FILES} 篇")
            self.badge.show()
        else:
            self.badge.hide()

    # ------------------------------------------------------------------ #
    # 检测
    # ------------------------------------------------------------------ #
    def _on_detect(self):
        if self._worker is not None and self._worker.isRunning():
            return
        threshold = float(self.threshold_combo.currentText())
        self.detect_btn.setEnabled(False)
        self.detect_btn.setText("检测中...")
        self.status_label.setText("正在计算全库相似度（后台）…")
        self.result_list.clear()
        tip = QListWidgetItem("正在检测…")
        tip.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        tip.setFlags(Qt.ItemFlag.NoItemFlags)
        self.result_list.addItem(tip)

        self._worker = DuplicateDetectWorker(self.engine, threshold, self)
        self._worker.finished.connect(self._on_ready)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_ready(self, pairs):
        self.detect_btn.setEnabled(True)
        self.detect_btn.setText("开始检测")
        self.result_list.blockSignals(True)
        self.result_list.clear()
        self.result_list.blockSignals(False)

        if not pairs:
            self.status_label.setText("未发现相似度达到阈值的文章对。")
            item = QListWidgetItem("未找到近似/重复文章 🎉")
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.result_list.addItem(item)
            return

        font = self.result_list.font()
        fm = QFontMetrics(font)
        for p in pairs:
            sim = p.similarity
            text = (f"{Path(p.file_a).name}  ↔  {Path(p.file_b).name}"
                    f"   ·   {sim * 100:.1f}%")
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole,
                          {"file_a": p.file_a, "file_b": p.file_b})
            item.setToolTip(
                f"{p.file_a}\n{p.file_b}\n相似度: {sim * 100:.2f}%")
            self._size_item(item, fm, text, self.result_list)
            self.result_list.addItem(item)

        self.status_label.setText(
            f"共发现 {len(pairs)} 对近似/重复文章（阈值 "
            f"{self.threshold_combo.currentText()}）。单击打开左侧，双击打开右侧。")

    def _on_error(self, msg):
        self.detect_btn.setEnabled(True)
        self.detect_btn.setText("开始检测")
        self.result_list.clear()
        self.status_label.setText("检测失败。")
        item = QListWidgetItem("检测失败：" + msg)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        self.result_list.addItem(item)

    @staticmethod
    def _size_item(item, fm, text, list_widget):
        # 使用列表控件自身的 viewport 宽度（与兄弟面板一致），
        # 避免在 item 尚未 addItem 时 item.listWidget() 为 None 导致崩溃。
        vw = max(1, (list_widget.viewport().width() - 24)
                   // max(fm.horizontalAdvance("中"), 1))
        lines = max(1, (len(text) // vw) + 1)
        item.setSizeHint(QSize(0, fm.height() + lines * (fm.height() + 2) + 8))

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #
    def _on_item_clicked(self, item):
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        # 单击 → 打开 file_a（左侧）
        self.pair_selected.emit(data["file_a"], data["file_b"])

    def _on_item_double(self, item):
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        # 双击 → 打开 file_b（右侧）
        self.pair_selected.emit(data["file_b"], data["file_a"])
