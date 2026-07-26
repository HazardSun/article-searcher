"""
主窗口关闭生命周期（ui/main_window.py closeEvent）集成测试 —— UI-003 回归。

目标：回归验证 UI-003 修复后，MainWindow.closeEvent 能优雅终止后台资源，
避免进程无法退出或退出异常：

- IndexWatcher（watchdog 非守护线程）被 stop()；
- 所有后台 QThread worker 在 closeEvent 内被 quit() + wait() 终止。

设计要点：
- offscreen Qt 平台；
- 用 tests._ui_fakes.FlexEngine（不加载 chromadb / onnx）；
- 显式启动一个 QThread worker 并挂到 mw.indexing_worker，以真实触发
  closeEvent 内的 _stop_worker(quit + wait) 路径；
- 直接调用 mw.closeEvent(QCloseEvent()) 以确定性地触发（无需事件循环）。

注意：IndexWatcher 暴露的是 is_enabled() 而非 is_alive()（后者是底层
watchdog Observer 的接口）。本测试断言「被关闭后应不再处于 enabled 状态」，
即等价于「watcher 已停止 / 不再 alive」，符合 UI-003 的设计意图。
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QThread  # noqa: E402
from PyQt6.QtGui import QCloseEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from ui.main_window import MainWindow  # noqa: E402
from core.config import ConfigStore  # noqa: E402
from tests._ui_fakes import FlexEngine  # noqa: E402


class _SleepWorker(QThread):
    """后台 worker 桩：run 中短暂休眠，确保 closeEvent 触发时其仍在运行，
    从而真实走 quit() + wait() 终止路径。"""

    def run(self):
        time.sleep(0.4)


class MainWindowCloseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _make_window(self):
        tmp = tempfile.mkdtemp()
        cs = ConfigStore(tmp)
        # 关闭自动索引，避免 closeEvent 内 watcher.restart() 触发真实监听
        cs.update(auto_index_enabled=False)
        eng = FlexEngine()
        win = MainWindow(eng, cs)
        return win, tmp

    def test_close_stops_watcher_and_workers(self):
        win, _ = self._make_window()

        # 启动一个后台 worker 并挂到 indexing_worker，模拟运行中的后台线程
        worker = _SleepWorker()
        win.indexing_worker = worker
        worker.start()
        self.assertTrue(worker.isRunning(), "前置：worker 应处于运行状态")

        # 触发 closeEvent（确定性，不依赖事件循环）
        try:
            win.closeEvent(QCloseEvent())
        except Exception as exc:  # noqa: BLE001
            self.fail(f"closeEvent 抛出异常（UI-003 复现）: {exc}")

        # 1) watcher 应被停止（IndexWatcher 暴露 is_enabled；关闭后为 False）
        self.assertFalse(
            win.watcher.is_enabled(),
            "closeEvent 后 watcher 仍 enabled（未停止）",
        )

        # 2) 各后台 worker 应在 2s 内停止运行
        stopped = worker.wait(2000)
        self.assertTrue(stopped, "worker.wait(2000) 超时，worker 未停止")
        self.assertFalse(worker.isRunning(), "closeEvent 后 worker 仍在运行")

        # 3) 其余已知 worker 属性若为 QThread 也应已停止（多数初始为 None）
        for attr in ("search_worker", "_device_worker", "_mindmap_worker",
                     "_cluster_worker", "_batch_worker", "_backup_worker",
                     "_restore_worker"):
            w = getattr(win, attr, None)
            if isinstance(w, QThread):
                self.assertFalse(w.isRunning(), f"{attr} 仍在运行")

    def test_close_without_running_workers(self):
        """无任何运行中的 worker 时，closeEvent 也应干净返回（不抛异常）。"""
        win, _ = self._make_window()
        try:
            win.closeEvent(QCloseEvent())
        except Exception as exc:  # noqa: BLE001
            self.fail(f"无 worker 时 closeEvent 抛出异常（UI-003 复现）: {exc}")
        self.assertFalse(win.watcher.is_enabled())


if __name__ == "__main__":
    unittest.main(verbosity=2)
