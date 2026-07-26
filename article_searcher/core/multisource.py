"""
多索引源模型（功能11 地基）

Source：单个索引源，含 path（根目录）、exclude_rules（fnmatch glob 排除规则，
大小写不敏感，匹配相对 source root 的路径或任意路径段）、enabled（是否启用）。

SourceList：多源管理（增删改 / 排除匹配），为 engine.watcher / 状态栏提供
统一的"已启用源"与"合并排除规则"视图。
"""

import os
import fnmatch
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any


def path_matches_exclude(file_path: str, root: str, patterns: List[str]) -> bool:
    """判断 file_path 是否命中任一排除规则。

    - 先求相对 root 的路径；
    - 若相对路径整体命中规则 → 命中；
    - 若相对路径的任一路径段（目录名 / 文件名）命中规则 → 命中
      （覆盖 .git / node_modules / *.tmp 这类常见写法）；
    - 大小写不敏感。

    与 `Source.matches_exclude` 保持一致语义（Source 内部调用本函数）。
    """
    if not patterns:
        return False
    try:
        rel = os.path.relpath(file_path, root)
    except ValueError:
        rel = os.path.basename(file_path)
    parts = rel.split(os.sep)
    for rule in patterns:
        if not rule:
            continue
        r = rule.lower()
        if fnmatch.fnmatch(rel.lower(), r):
            return True
        # 也允许 basename 直接命中（如规则写 "foo.tmp" 而 rel 为 "sub/foo.tmp"）
        if fnmatch.fnmatch(os.path.basename(file_path).lower(), r):
            return True
        if any(fnmatch.fnmatch(p.lower(), r) for p in parts):
            return True
    return False


@dataclass
class Source:
    """单个索引源"""
    path: str
    exclude_rules: List[str] = field(default_factory=list)
    enabled: bool = True

    def matches_exclude(self, file_path: str) -> bool:
        """相对 source root 计算相对路径后做 fnmatch（大小写不敏感）"""
        return path_matches_exclude(file_path, self.path, self.exclude_rules)

    def to_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "exclude_rules": list(self.exclude_rules), "enabled": self.enabled}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Source":
        return cls(
            path=d.get("path", ""),
            exclude_rules=list(d.get("exclude_rules", []) or []),
            enabled=bool(d.get("enabled", True)),
        )


class SourceList:
    """多源管理：增删改 / 排除匹配 / 派生视图"""

    def __init__(self, sources: Optional[List[Source]] = None):
        self._sources: List[Source] = list(sources) if sources else []

    # ---- 增删改 ----
    def add(self, src: Source):
        for i, s in enumerate(self._sources):
            if os.path.abspath(s.path) == os.path.abspath(src.path):
                self._sources[i] = src
                return
        self._sources.append(src)

    def remove(self, path: str):
        ap = os.path.abspath(path)
        self._sources = [s for s in self._sources if os.path.abspath(s.path) != ap]

    def update(self, path: str, **kw):
        ap = os.path.abspath(path)
        for s in self._sources:
            if os.path.abspath(s.path) == ap:
                for k, v in kw.items():
                    if hasattr(s, k):
                        setattr(s, k, v)
                return

    def get(self, path: str) -> Optional[Source]:
        ap = os.path.abspath(path)
        for s in self._sources:
            if os.path.abspath(s.path) == ap:
                return s
        return None

    # ---- 派生视图 ----
    def enabled_paths(self) -> List[str]:
        return [s.path for s in self._sources if s.enabled]

    def all_excludes(self) -> List[str]:
        ex: List[str] = []
        for s in self._sources:
            if s.enabled:
                ex.extend(s.exclude_rules)
        return ex

    def to_dicts(self) -> List[Dict[str, Any]]:
        return [s.to_dict() for s in self._sources]

    @classmethod
    def from_dicts(cls, dicts: List[Dict[str, Any]]) -> "SourceList":
        return cls([Source.from_dict(d) for d in (dicts or [])])

    def __iter__(self):
        return iter(self._sources)

    def __len__(self):
        return len(self._sources)
