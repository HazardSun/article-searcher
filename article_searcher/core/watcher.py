"""
文件监听自动索引（功能10）—— watchdog

- 仅监听「已索引源」（sources_getter 返回的 enabled 源路径）；
- 排除规则命中路径、临时文件（~$ / *.tmp / .swp）直接忽略；
- 防抖（默认 1.5s）合并多次事件，触发 engine.load_folder(incremental=True)；
- 原生 Observer 不可用时回退 PollingObserver（轮询，代价略高但稳定）；
- watchdog 未安装时 set_enabled(True) 仅记录警告、不崩溃（HAVE_WATCHDOG=False）。
所有重嵌入均在后台线程执行，不阻塞 GUI 与 watcher 观察线程。
"""

import os
import logging
import threading
from typing import Callable, List, Optional

from .multisource import path_matches_exclude

try:
    from watchdog.observers import Observer
    from watchdog.observers.polling import PollingObserver
    from watchdog.events import FileSystemEventHandler
    HAVE_WATCHDOG = True
except Exception:  # pragma: no cover - 环境相关
    HAVE_WATCHDOG = False
    Observer = None
    PollingObserver = None
    FileSystemEventHandler = object

logger = logging.getLogger(__name__)

_TEMP_PREFIXES = ("~$",)
_TEMP_SUFFIXES = (".tmp", ".swp", ".part")


def _path_under(path: str, root: str) -> bool:
    """判断 path 是否位于 root 目录树内（含 root 自身）。

    用于判定文件变更是否落在某个「已启用」索引源范围内，
    避免对未索引目录的变更误触发增量重建。
    """
    ap = os.path.abspath(path)
    ar = os.path.abspath(root)
    return ap == ar or ap.startswith(ar + os.sep)


class _Handler(FileSystemEventHandler if HAVE_WATCHDOG else object):
    def __init__(self, watcher):
        super().__init__()
        self._watcher = watcher

    def on_any_event(self, event):
        paths = [event.src_path]
        dest = getattr(event, "dest_path", None)
        if dest:
            paths.append(dest)
        for p in paths:
            self._watcher._on_raw_event(p)


class IndexWatcher:
    """监听已索引源的文件变更并触发增量重建（防抖）。"""

    def __init__(
        self,
        engine,
        sources_getter: Callable[[], List],
        debounce_ms: int = 1500,
        enabled: bool = False,
        on_status: Optional[Callable[[str], None]] = None,
        on_done: Optional[Callable[[dict], None]] = None,
    ):
        self._engine = engine
        self._sources_getter = sources_getter
        self._debounce_ms = debounce_ms
        self._on_status = on_status
        self._on_done = on_done
        self._enabled = False
        self._observer = None
        self._timer: Optional[threading.Timer] = None
        self._timer_lock = threading.Lock()
        self._indexing = False
        if enabled:
            self.set_enabled(True)

    # ------------------------------------------------------------------ #
    # 开关
    # ------------------------------------------------------------------ #
    def set_enabled(self, enabled: bool):
        if enabled:
            self.start()
        else:
            self.stop()

    def is_enabled(self) -> bool:
        return self._enabled

    def start(self):
        if not HAVE_WATCHDOG:
            logger.warning("watchdog 未安装，自动索引不可用（请 pip install watchdog）")
            self._enabled = False
            return
        if self._enabled:
            return
        sources = self._sources_getter() or []
        paths = [s.path for s in sources if getattr(s, "enabled", True)]
        if not paths:
            logger.info("无已启用索引源，自动索引未启动")
            self._enabled = False
            return
        try:
            self._observer = Observer()
        except Exception:  # pragma: no cover
            try:
                self._observer = PollingObserver()
            except Exception as e:
                logger.warning("watchdog observer 创建失败，回退仍失败: %s", e)
                self._enabled = False
                return
        handler = _Handler(self)
        for p in paths:
            if os.path.isdir(p):
                try:
                    self._observer.schedule(handler, p, recursive=True)
                except Exception as e:
                    logger.warning("监听源失败 %s: %s", p, e)
        try:
            self._observer.start()
        except Exception as e:
            logger.warning("watcher.start 失败: %s", e)
            self._enabled = False
            return
        self._enabled = True
        logger.info("自动索引已启动，监听 %d 个源", len(paths))

    def stop(self):
        with self._timer_lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        if self._observer is not None:
            try:
                self._observer.unschedule_all()
                self._observer.stop()
                self._observer.join(timeout=2)
            except Exception as e:
                logger.warning("停止 watcher 失败: %s", e)
            self._observer = None
        self._enabled = False

    def restart(self):
        """源列表变化后重启监听（保持 enabled 状态）。"""
        was = self._enabled
        self.stop()
        if was:
            self.start()

    # ------------------------------------------------------------------ #
    # 事件过滤
    # ------------------------------------------------------------------ #
    def _is_relevant(self, path: str) -> bool:
        base = os.path.basename(path)
        if base.startswith(_TEMP_PREFIXES) or base.lower().endswith(_TEMP_SUFFIXES):
            return False
        sources = self._sources_getter() or []
        # 文件必须落在某个「已启用」源目录下，才属于可索引范围；
        # 禁用源的目录视为未监听，其下文件变更不应触发增量重建。
        under_enabled = False
        for s in sources:
            if not getattr(s, "enabled", True):
                continue
            if _path_under(path, s.path):
                under_enabled = True
            if path_matches_exclude(path, s.path, getattr(s, "exclude_rules", [])):
                return False
        return under_enabled

    def _on_raw_event(self, path: str):
        if not self._enabled:
            return
        if not self._is_relevant(path):
            return
        self._schedule_flush()

    def _schedule_flush(self):
        with self._timer_lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce_ms / 1000.0, self._debounced_flush)
            self._timer.daemon = True
            self._timer.start()

    def _debounced_flush(self):
        with self._timer_lock:
            self._timer = None
        if self._indexing:
            # 上一轮仍在跑：稍后重试（事件不丢）
            self._schedule_flush()
            return
        self._indexing = True
        try:
            if self._on_status:
                try:
                    self._on_status("检测到文件变更，正在增量更新索引…")
                except Exception:
                    pass
            sources = self._sources_getter() or []

            def run():
                try:
                    stats = self._engine.load_folder(sources=list(sources), incremental=True)
                    if self._on_done:
                        try:
                            self._on_done(stats)
                        except Exception:
                            pass
                except Exception as e:
                    logger.error("自动索引失败: %s", e)
                finally:
                    self._indexing = False

            t = threading.Thread(target=run, daemon=True)
            t.start()
        except Exception:
            self._indexing = False
