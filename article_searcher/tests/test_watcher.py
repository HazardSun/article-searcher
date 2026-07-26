"""
P1 功能 10 文件监听自动索引：防抖 / 过滤 / 降级 测试。

不依赖真实文件系统事件与 watchdog 实现：
- 用 FakeTimer / FakeThread 接管 core.watcher.threading，使防抖回调可同步触发、可断言；
- 验证「多次事件仅触发一次 flush」「临时文件 / 排除规则命中被忽略」；
- 验证 watchdog 缺失时 set_enabled(True) 安全降级（不崩溃、保持 disabled）。
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core.watcher as watcher_mod
from core.watcher import IndexWatcher
from core.multisource import Source


class FakeTimer:
    instances = []

    def __init__(self, interval, fn, args=None, kwargs=None):
        self.interval = interval
        self.fn = fn
        self.args = args or ()
        self.kwargs = kwargs or {}
        self.cancelled = False
        FakeTimer.instances.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True


class FakeThread:
    def __init__(self, target=None, daemon=None, args=(), kwargs=None):
        self.target = target
        self.daemon = daemon
        self.args = args
        self.kwargs = kwargs or {}

    def start(self):
        # 同步执行，便于断言
        self.target(*self.args, **self.kwargs)

    def join(self, timeout=None):
        pass


class FakeEngine:
    def __init__(self):
        self.load_calls = []

    def load_folder(self, sources=None, incremental=True):
        self.load_calls.append((sources, incremental))
        return {"total_files": len(sources) if sources else 0}


def make_sources_getter(paths):
    sources = [Source(p, enabled=True) for p in paths]
    return lambda: sources


class TestWatcherRelevance(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.excl = os.path.join(self.dir, "secret")
        os.makedirs(self.excl, exist_ok=True)
        self.src = Source(self.dir, exclude_rules=["secret"], enabled=True)
        self.getter = lambda: [self.src]
        self.w = IndexWatcher(FakeEngine(), self.getter, debounce_ms=10)

    def test_temp_files_ignored(self):
        self.assertFalse(self.w._is_relevant(os.path.join(self.dir, "~$report.docx")))
        self.assertFalse(self.w._is_relevant(os.path.join(self.dir, "note.tmp")))
        self.assertFalse(self.w._is_relevant(os.path.join(self.dir, ".swp")))

    def test_excluded_path_ignored(self):
        self.assertFalse(self.w._is_relevant(
            os.path.join(self.excl, "x.md")))

    def test_normal_path_relevant(self):
        self.assertTrue(self.w._is_relevant(os.path.join(self.dir, "ok.md")))

    def test_disabled_source_ignored(self):
        self.src.enabled = False
        self.assertFalse(self.w._is_relevant(os.path.join(self.dir, "ok.md")))


class TestWatcherDebounce(unittest.TestCase):
    def setUp(self):
        FakeTimer.instances = []
        self.engine = FakeEngine()
        self.dir = tempfile.mkdtemp()
        self.getter = make_sources_getter([self.dir])
        self.w = IndexWatcher(self.engine, self.getter, debounce_ms=10,
                              on_status=None, on_done=None)
        # 防抖逻辑仅在 watcher 启用时处理事件；测试直接验证调度行为，
        # 故绕过 start()（避免真实 watchdog observer），置 enabled 即可。
        self.w._enabled = True
        self._patcher_t = mock.patch.object(watcher_mod.threading, "Timer", FakeTimer)
        self._patcher_th = mock.patch.object(watcher_mod.threading, "Thread", FakeThread)
        self._patcher_t.start()
        self._patcher_th.start()

    def tearDown(self):
        self._patcher_t.stop()
        self._patcher_th.stop()

    def test_multiple_events_collapse_to_one_flush(self):
        # 连发 3 次事件，应只安排 1 个 Timer（前两次被 cancel）
        self.w._on_raw_event(os.path.join(self.dir, "a.md"))
        self.w._on_raw_event(os.path.join(self.dir, "b.md"))
        self.w._on_raw_event(os.path.join(self.dir, "c.md"))
        # 仅最后一个 Timer 存活
        alive = [t for t in FakeTimer.instances if not t.cancelled]
        self.assertEqual(len(alive), 1)
        self.assertEqual(len(FakeTimer.instances), 3)

    def test_irrelevant_event_does_not_schedule(self):
        before = len(FakeTimer.instances)
        self.w._on_raw_event(os.path.join(self.dir, "note.tmp"))
        self.assertEqual(len(FakeTimer.instances), before)

    def test_debounced_flush_calls_load_folder(self):
        self.w._on_raw_event(os.path.join(self.dir, "a.md"))
        # 手动触发防抖回调
        self.w._debounced_flush()
        self.assertEqual(len(self.engine.load_calls), 1)
        sources, incremental = self.engine.load_calls[0]
        self.assertTrue(incremental)
        self.assertEqual([s.path for s in sources], [self.dir])

    def test_debounce_does_not_double_fire(self):
        self.w._on_raw_event(os.path.join(self.dir, "a.md"))
        self.w._debounced_flush()
        # 再次触发（模拟新一批事件）
        self.w._on_raw_event(os.path.join(self.dir, "b.md"))
        self.w._debounced_flush()
        self.assertEqual(len(self.engine.load_calls), 2)


class TestWatcherNoWatchdog(unittest.TestCase):
    def test_set_enabled_true_safe_when_no_watchdog(self):
        with mock.patch.object(watcher_mod, "HAVE_WATCHDOG", False):
            w = IndexWatcher(FakeEngine(), lambda: [], debounce_ms=10)
            w.set_enabled(True)  # 不应抛异常
            self.assertFalse(w.is_enabled())
            w.set_enabled(False)
            self.assertFalse(w.is_enabled())


if __name__ == "__main__":
    unittest.main(verbosity=2)
