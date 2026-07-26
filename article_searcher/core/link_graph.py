"""
跨文件链接图谱（功能6 / P2-6）

核心逻辑（纯函数 + LinkGraphBuilder），不依赖 GUI、不调用 encode：
- 解析 Markdown 的 `[[wikilink]]` 与相对/绝对路径引用
  （如 `[text](./other.md)`、`[text](../dir/other.md)`、`[text](/abs/x.md)`）
- 多源感知的路径规范化：相对路径按「包含该文件的 Source 根」解析；
  wikilink 按 title / stem 跨源匹配（优先同源）。
- 构建文章关系图（节点=文章，边=引用），含 incoming 索引（谁引用了我）。

链接是文件内容的确定函数 → 仅读磁盘、不写 tags.json（与 file_tags / starred /
clusters 三权分立零冲突）。结果由调用方（Worker）内存缓存并按需失效。
"""

import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .parser import MARKDOWN_EXTENSIONS


@dataclass
class ExtractedLink:
    """从某文件抽取出的一条链接引用。"""
    source_path: str                 # 引用所在文件（绝对路径）
    target_raw: str                 # 原始目标：wikilink 名 或 相对/绝对路径
    target_resolved: Optional[str]  # 规范化后的索引内绝对路径；无法解析为 None
    link_type: str                  # 'wikilink' | 'relpath' | 'abspath'
    line: int                      # 在 source_path 中的行号（用于高亮引用处）
    context: str                   # 该行文本（用于列表/tooltip 展示）


@dataclass
class LinkNode:
    """图谱节点。"""
    path: str    # 索引内绝对路径（dangling 时为原始 target 文本）
    title: str   # 展示名（文件名 stem 或 title）
    kind: str    # 'article'（已索引）| 'missing'（悬挂链接）


@dataclass
class LinkEdge:
    """图谱有向边（source 引用了 target）。"""
    source: str
    target: str
    link_type: str
    line: int


@dataclass
class LinkGraph:
    """跨文件链接图谱。"""
    nodes: Dict[str, LinkNode] = field(default_factory=dict)
    edges: List[LinkEdge] = field(default_factory=list)
    incoming: Dict[str, List[str]] = field(default_factory=dict)  # target -> [source...]


# 仅抽取「行内」链接（Markdown 链接不跨行），故逐行解析即可。
_WIKILINK_RE = re.compile(r"\[\[([^\[\]]+)\]\]")
# 负向后顾：排除图片 `![alt](url)`
_MDLINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)]+)\)")
_EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "ftp://", "data:", "#", "//")


class LinkGraphBuilder:
    """链接抽取与图谱构建器（可单测、不调 encode）。"""

    # ------------------------------------------------------------------ #
    # 链接抽取
    # ------------------------------------------------------------------ #
    def extract_links(self, text: str, file_path: str) -> List[ExtractedLink]:
        """正则解析 markdown 文本中的链接引用。

        支持：
          - [[Name]] / [[Name|alias]] / [[Name#sec]] / [[dir/Name]] → wikilink
          - [text](./x.md) / [text](../d/x.md) / [text](/abs/x.md) → relpath/abspath
        跳过：代码围栏（```...```）内的链接、图片 `![](url)`、外部 URL。

        返回每条链接的 (target_raw, line, context) 并记录 link_type。
        """
        links: List[ExtractedLink] = []
        in_fence = False
        for idx, line in enumerate(text.split("\n")):
            stripped = line.strip()
            if stripped.startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue

            # wikilink：取 '|' 前、'#' 前的真实页面名
            for m in _WIKILINK_RE.finditer(line):
                raw = m.group(1).strip()
                name = raw.split("|", 1)[0].split("#", 1)[0].strip()
                if not name:
                    continue
                links.append(ExtractedLink(
                    source_path=file_path,
                    target_raw=name,
                    target_resolved=None,
                    link_type="wikilink",
                    line=idx,
                    context=line.strip()[:200],
                ))

            # markdown 链接
            for m in _MDLINK_RE.finditer(line):
                url = m.group(2).strip()
                if not url:
                    continue
                low = url.lower()
                if low.startswith(_EXTERNAL_PREFIXES):
                    continue
                link_type = "abspath" if url.startswith("/") else "relpath"
                links.append(ExtractedLink(
                    source_path=file_path,
                    target_raw=url,
                    target_resolved=None,
                    link_type=link_type,
                    line=idx,
                    context=line.strip()[:200],
                ))
        return links

    # ------------------------------------------------------------------ #
    # 规范化解析（多源感知）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _find_source_root(engine, file_path: str) -> Optional[str]:
        """返回包含 file_path 的 Source 根（绝对路径），找不到返回 None。"""
        ap = os.path.abspath(file_path)
        for s in (engine.sources or []):
            sp = os.path.abspath(s.path)
            if ap == sp or ap.startswith(sp + os.sep):
                return sp
        return None

    @staticmethod
    def _in_source(root_of: Dict[str, Optional[str]], fp: str, src_root: Optional[str]) -> bool:
        if src_root is None:
            return False
        return root_of.get(fp) == src_root

    def _build_maps(self, engine, indexed: Dict[str, dict]):
        """预构建 title/stem/basename → [绝对路径] 与 文件→源根 映射。"""
        title_map: Dict[str, List[str]] = defaultdict(list)
        stem_map: Dict[str, List[str]] = defaultdict(list)
        base_map: Dict[str, List[str]] = defaultdict(list)
        root_of: Dict[str, Optional[str]] = {}
        for fp in indexed:
            meta = indexed[fp] or {}
            title = (meta.get("title") or "").strip().lower()
            if title:
                title_map[title].append(fp)
            stem = os.path.splitext(os.path.basename(fp))[0].lower()
            if stem:
                stem_map[stem].append(fp)
            base_map[os.path.basename(fp).lower()].append(fp)
            root_of[fp] = self._find_source_root(engine, fp)
        return title_map, stem_map, base_map, root_of

    def _resolve(
        self,
        source_path: str,
        raw: str,
        link_type: str,
        indexed: Dict[str, dict],
        title_map: Dict[str, List[str]],
        stem_map: Dict[str, List[str]],
        base_map: Dict[str, List[str]],
        root_of: Dict[str, Optional[str]],
    ) -> Optional[str]:
        """基于预构建映射做确定性解析，返回索引内绝对路径或 None。

        优先级（设计 §3.1 / §8）：
          relpath/abspath：① 精确命中 indexed → ② 同源 basename → ③ 跨源 basename
          wikilink       ：① 同源 title   → ② 同源 stem   → ③ 跨源 title → ④ 跨源 stem
        同源命中失败时退回跨源；同名多源取排序后首个（确定性）。
        """
        src_root = root_of.get(os.path.abspath(source_path))

        if link_type == "wikilink":
            # "dir/Name" → 退化为 basename 的 stem 匹配
            leaf = raw.split("/")[-1].split("\\")[-1]
            stem_key = os.path.splitext(leaf)[0].lower()
            title_key = raw.strip().lower()
            # ① 同源 title
            cands = [fp for fp in title_map.get(title_key, [])
                     if self._in_source(root_of, fp, src_root)]
            if cands:
                return sorted(cands)[0]
            # ② 同源 stem
            cands = [fp for fp in stem_map.get(stem_key, [])
                     if self._in_source(root_of, fp, src_root)]
            if cands:
                return sorted(cands)[0]
            # ③ 跨源 title
            cands = title_map.get(title_key, [])
            if cands:
                return sorted(cands)[0]
            # ④ 跨源 stem
            cands = stem_map.get(stem_key, [])
            if cands:
                return sorted(cands)[0]
            return None

        # 路径类：计算候选绝对路径
        if link_type == "abspath":
            if os.path.isabs(raw):
                candidate = os.path.normpath(raw)
            else:
                # 以 '/' 开头但非绝对（如 Windows）：视为相对「源根」
                if src_root is not None:
                    candidate = os.path.normpath(os.path.join(src_root, raw.lstrip("/\\")))
                else:
                    candidate = os.path.normpath(os.path.join(os.path.dirname(source_path), raw.lstrip("/\\")))
        else:  # relpath
            candidate = os.path.normpath(os.path.join(os.path.dirname(source_path), raw))

        # ① 精确命中
        if candidate in indexed:
            return candidate
        base_key = os.path.basename(candidate).lower()
        # ② 同源 basename
        cands = [fp for fp in base_map.get(base_key, [])
                 if self._in_source(root_of, fp, src_root)]
        if cands:
            return sorted(cands)[0]
        # ③ 跨源 basename
        cands = base_map.get(base_key, [])
        if cands:
            return sorted(cands)[0]
        return None

    def resolve_link(
        self,
        source_path: str,
        raw: str,
        engine,
        link_type: Optional[str] = None,
    ) -> Optional[str]:
        """多源感知的链接规范化（可独立单测）。

        Args:
            source_path: 引用所在文件的绝对路径
            raw: 原始目标（wikilink 名 / 相对或绝对路径）
            engine: 提供 `sources`（List[Source]）与 `get_indexed_files()` 的引擎
            link_type: 'wikilink' | 'relpath' | 'abspath'；为 None 时按 raw 猜测
        """
        indexed = engine.get_indexed_files()
        if not indexed:
            return None
        if link_type is None:
            link_type = self._guess_type(raw)
        title_map, stem_map, base_map, root_of = self._build_maps(engine, indexed)
        return self._resolve(
            source_path, raw, link_type, indexed,
            title_map, stem_map, base_map, root_of,
        )

    @staticmethod
    def _guess_type(raw: str) -> str:
        """无显式类型时猜测：带扩展名的路径 → relpath/abspath；否则 wikilink。"""
        if raw.startswith("/"):
            return "abspath"
        base = os.path.basename(raw)
        ext = os.path.splitext(base)[1].lower()
        if ext and ext in MARKDOWN_EXTENSIONS:
            return "relpath"
        if "/" in raw or "\\" in raw:
            return "relpath"
        return "wikilink"

    # ------------------------------------------------------------------ #
    # 图谱构建
    # ------------------------------------------------------------------ #
    @staticmethod
    def _display_title(indexed: Dict[str, dict], fp: str) -> str:
        meta = indexed.get(fp, {}) or {}
        title = (meta.get("title") or "").strip()
        if title:
            return title
        return os.path.splitext(os.path.basename(fp))[0]

    def build(self, engine) -> LinkGraph:
        """遍历全部已索引的 markdown 文件，逐篇抽取+解析，聚合为 LinkGraph。

        仅调用 engine.get_indexed_files() / get_file_content()（读磁盘，不 encode），
        可在 Worker 线程执行。结果含 incoming 索引（谁引用了我）。
        """
        indexed = engine.get_indexed_files()
        title_map, stem_map, base_map, root_of = self._build_maps(engine, indexed)

        nodes: Dict[str, LinkNode] = {}
        edges: List[LinkEdge] = []
        incoming: Dict[str, List[str]] = defaultdict(list)

        # 1) 先收集所有已索引 markdown 文件作为文章节点
        md_files = sorted(
            fp for fp in indexed
            if os.path.splitext(fp)[1].lower() in MARKDOWN_EXTENSIONS
        )
        for fp in md_files:
            nodes[fp] = LinkNode(fp, self._display_title(indexed, fp), "article")

        # 2) 逐篇抽取链接并解析
        for fp in md_files:
            try:
                content = engine.get_file_content(fp)
            except Exception:  # noqa: BLE001 - 单文件读取失败不应中断整图
                content = ""
            for link in self.extract_links(content, fp):
                resolved = self._resolve(
                    fp, link.target_raw, link.link_type, indexed,
                    title_map, stem_map, base_map, root_of,
                )
                link.target_resolved = resolved
                if resolved:
                    if resolved not in nodes:
                        nodes[resolved] = LinkNode(
                            resolved, self._display_title(indexed, resolved), "article")
                    target_key = resolved
                else:
                    # dangling：以原始 target 文本作为悬挂节点 key
                    target_key = link.target_raw
                    if target_key not in nodes:
                        nodes[target_key] = LinkNode(
                            target_key, link.target_raw, "missing")
                edges.append(LinkEdge(fp, target_key, link.link_type, link.line))
                if fp not in incoming[target_key]:
                    incoming[target_key].append(fp)

        return LinkGraph(dict(nodes), edges, dict(incoming))
