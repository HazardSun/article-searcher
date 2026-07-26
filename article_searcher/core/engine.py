"""
文章搜索引擎
整合文件扫描、解析、切片、向量化、词法索引与检索的核心引擎。
新增能力：
- 混合检索（语义 / 关键词 / 混合，RRF 融合）
- 本地词法索引（BM25）持久化
- 文件管理（移除、重建单文件索引、在资源管理器中打开）
- 增量索引修复（基于持久化 MD5 正确跳过未变化文件）
"""

import os
import sys
import re
import fnmatch
import logging
import collections
from pathlib import Path
from typing import List, Optional, Dict, Any, Union

from .parser import FileScanner, ContentParser, FileMetadata, TextChunk, MARKDOWN_EXTENSIONS
from .chunker import SemanticChunker, ChunkConfig
from .embedding import EmbeddingEngine
from .vectorstore import VectorStore
from .tagger import TagManager
from .lexical import LexicalIndex
from . import search as search_fusion
from .query_parser import (
    ParsedQuery, BoolExpr, TagNode, PathNode, TermNode,
    NotNode, AndNode, OrNode, build_tag_filter_parsed, combine_parsed,
)
from .multisource import Source, SourceList

logger = logging.getLogger(__name__)

SEARCH_MODES = ("semantic", "keyword", "hybrid")


class ArticleSearchEngine:
    """文章搜索引擎 - 核心业务逻辑"""

    def __init__(
        self,
        db_path: str = None,
        model_cache_dir: str = None,
        embedding_model: str = None,
        device: str = None,
        chunk_max: int = 800,
        chunk_overlap: int = 100,
        search_mode: str = "hybrid",
        batch_size: int = 32,
        priority: str = None,
    ):
        self._priority = priority
        self.embedding_engine = EmbeddingEngine(
            model_name=embedding_model,
            device=device,
            cache_dir=model_cache_dir,
            batch_size=batch_size,
            priority=priority,
        )
        self.vector_store = VectorStore(
            db_path=db_path,
            embedding_engine=self.embedding_engine,
        )
        self.file_scanner = FileScanner()
        self.content_parser = ContentParser()
        self.chunker = SemanticChunker(
            ChunkConfig(max_chunk_size=chunk_max, overlap_size=chunk_overlap)
        )
        # 标签持久化：重启后标签筛选 / 展示仍然有效
        tags_path = os.path.join(self.vector_store.db_path, "tags.json")
        self.tag_manager = TagManager(save_path=tags_path)
        self.tag_manager.load()
        self.lexical = LexicalIndex(os.path.join(self.vector_store.db_path, "lexical_index.json"))

        self._search_mode = search_mode if search_mode in SEARCH_MODES else "hybrid"
        # P1：多源模型（功能11）。保留 current_folder 兼容 shim（返回首个启用源）。
        self._sources: List[Source] = []
        # 切换模型后索引维度可能与旧向量不一致（需要重建索引）
        self._index_stale = False
        # 文件全文按需读取的 LRU 缓存（上限防止内存无限增长）
        self._content_cache: "collections.OrderedDict" = collections.OrderedDict()
        self._content_cache_max = 20

    # ------------------------------------------------------------------ #
    # 属性
    # ------------------------------------------------------------------ #
    @property
    def search_mode(self) -> str:
        return self._search_mode

    def set_search_mode(self, mode: str):
        if mode in SEARCH_MODES:
            self._search_mode = mode

    # ------------------------------------------------------------------ #
    # 多源（功能11）
    # ------------------------------------------------------------------ #
    @property
    def current_folder(self) -> Optional[str]:
        """兼容 shim：返回首个启用源路径；无源时返回 None。"""
        for s in self._sources:
            if s.enabled:
                return s.path
        return self._sources[0].path if self._sources else None

    @property
    def sources(self) -> List[Source]:
        return self._sources

    def set_sources(self, sources):
        norm = []
        for s in (sources or []):
            norm.append(s if isinstance(s, Source) else Source.from_dict(s))
        self._sources = norm

    def add_source(self, src):
        src = src if isinstance(src, Source) else Source.from_dict(src)
        ap = os.path.abspath(src.path)
        for i, s in enumerate(self._sources):
            if os.path.abspath(s.path) == ap:
                self._sources[i] = src
                return
        self._sources.append(src)

    def remove_source(self, path: str):
        ap = os.path.abspath(path)
        self._sources = [s for s in self._sources if os.path.abspath(s.path) != ap]

    def update_source(self, path: str, **kw):
        ap = os.path.abspath(path)
        for s in self._sources:
            if os.path.abspath(s.path) == ap:
                for k, v in kw.items():
                    if hasattr(s, k):
                        setattr(s, k, v)
                return

    # ------------------------------------------------------------------ #
    # 运行时设备 / 模型切换
    # ------------------------------------------------------------------ #
    def set_device(self, device: str):
        """切换运行设备（会触发模型按需重载）"""
        self.embedding_engine.set_device(device)

    def set_model(self, model_name: str, device: str = None):
        """切换 Embedding 模型并重建引擎（向量库与索引保持不变）"""
        old_dim = self.vector_store.get_embedding_dim()
        if device:
            self.embedding_engine.set_device(device)
        self.embedding_engine.set_model(model_name)
        self.vector_store.embedding_engine = self.embedding_engine
        new_dim = self.embedding_engine.dimension
        if old_dim and new_dim and old_dim != new_dim:
            # 向量维度变化，旧索引与当前模型不兼容，需重建
            self._index_stale = True
        self.vector_store.set_embedding_dim(new_dim)

    # ------------------------------------------------------------------ #
    # 索引
    # ------------------------------------------------------------------ #
    def load_folder(
        self,
        folder_path: str = None,
        sources: List[Source] = None,
        incremental: bool = True,
        progress_callback=None,
    ) -> Dict[str, Any]:
        """遍历多源（功能11）增量/全量重建索引。

        兼容旧调用：单传 folder_path 时退化为单源；都不传则用 self._sources。
        默认仅处理 enabled 源；逐源 scan_directory(exclude_patterns=src.exclude_rules)。
        """
        if sources is None:
            if folder_path is not None:
                sources = [Source(folder_path)]
            else:
                sources = self._sources

        norm_sources = []
        for s in (sources or []):
            norm_sources.append(s if isinstance(s, Source) else Source.from_dict(s))
        # 记录当前源列表（便于 get_status / 兼容 shim 返回首源）
        self._sources = list(norm_sources)

        stats = {
            "total_files": 0, "new_files": 0, "updated_files": 0,
            "unchanged_files": 0, "total_chunks": 0, "errors": [],
            "orphans_removed": 0,
            "sources": len([s for s in norm_sources if s.enabled]),
        }

        # 1) 逐源扫描（应用排除规则），收集全部磁盘文件
        all_files = []
        all_disk_paths = set()
        for src in norm_sources:
            if not src.enabled:
                continue
            try:
                files = self.file_scanner.scan_directory(
                    src.path, exclude_patterns=src.exclude_rules)
            except Exception as e:
                stats["errors"].append(f"扫描源失败 {src.path}: {str(e)}")
                logger.error(stats["errors"][-1])
                continue
            for fm in files:
                all_files.append(fm)
                all_disk_paths.add(fm.file_path)

        total = len(all_files)
        # 关键：取「加载前」已索引文件的快照（dict 浅拷贝），
        # 避免下方 _process_file → add_chunks 就地修改同一 dict 导致计数错位。
        indexed_files = dict(self.vector_store.get_indexed_files())

        # 2) 逐文件处理（增量跳过未变化）
        for idx, file_meta in enumerate(all_files):
            try:
                if progress_callback:
                    progress_callback(idx + 1, total, file_meta.file_name)

                if incremental and file_meta.file_path in indexed_files:
                    stored_md5 = self.vector_store.get_file_md5(file_meta.file_path)
                    if stored_md5 and stored_md5 == file_meta.md5_hash:
                        stats["unchanged_files"] += 1
                        continue

                self._process_file(file_meta)
                self.vector_store.set_file_md5(file_meta.file_path, file_meta.md5_hash)
                if file_meta.file_path in indexed_files:
                    stats["updated_files"] += 1
                else:
                    stats["new_files"] += 1
            except Exception as e:
                error_msg = f"处理 {file_meta.file_name} 出错: {str(e)}"
                stats["errors"].append(error_msg)
                logger.error(error_msg)

        # 3) 孤儿清理：不在任何已启用源磁盘扫描中的已索引文件
        for path in list(self.vector_store.get_indexed_files().keys()):
            if path not in all_disk_paths:
                self.remove_file_from_index(path)
                stats["orphans_removed"] += 1

        # 索引完成后统一落盘标签 / 词法索引（避免逐文件频繁写盘）
        self.tag_manager.save()
        self.lexical.save()

        stats["total_chunks"] = self.vector_store.get_chunk_count()
        # total_files = 孤儿清理后的最终已索引文件数
        stats["total_files"] = len(self.vector_store.get_indexed_files())
        logger.info("索引完成: %s", stats)
        return stats

    def _process_file(self, file_meta: FileMetadata):
        file_path = file_meta.file_path
        ext = file_meta.file_extension

        self.vector_store.delete_by_file(file_path)
        self.lexical.remove_file(file_path)
        self.tag_manager.remove_file(file_path)

        if ext == ".pdf":
            paragraphs = self.content_parser.parse_pdf(file_path)
            content = "\n".join(p["content"] for p in paragraphs)
        elif ext == ".docx":
            paragraphs = self.content_parser.parse_docx(file_path)
            content = "\n".join(p["content"] for p in paragraphs)
        elif ext == ".txt":
            content = Path(file_path).read_text(encoding="utf-8", errors="ignore")
            paragraphs = self.content_parser.parse_text(content)
        elif ext.lower() in MARKDOWN_EXTENSIONS:
            content = Path(file_path).read_text(encoding="utf-8", errors="ignore")
            paragraphs = self.content_parser.parse_markdown(content)
        else:
            content = Path(file_path).read_text(encoding="utf-8", errors="ignore")
            paragraphs = self.content_parser.parse_html(content)

        chunks = self.chunker.chunk_article(
            file_path=file_path,
            file_name=file_meta.file_name,
            title=file_meta.title,
            paragraphs=paragraphs,
        )
        if not chunks:
            return

        texts = [c.content for c in chunks]
        embeddings = self.embedding_engine.encode(texts)

        self.vector_store.add_chunks(chunks, embeddings)
        self.vector_store.set_embedding_dim(self.embedding_engine.dimension)
        self.lexical.add_chunks(chunks, defer_save=True)

        title = file_meta.title or file_meta.file_name
        tags = self.tag_manager.generate_tags(file_path, content, title)
        file_meta.tags = tags

    # ------------------------------------------------------------------ #
    # 检索
    # ------------------------------------------------------------------ #
    def search(
        self,
        query: str = "",
        top_k: int = 10,
        tag_filter: Optional[Union[str, List[str]]] = None,
        mode: str = None,
        path_filter: Optional[List[str]] = None,
        exclude_terms: Optional[List[str]] = None,
        parsed: "ParsedQuery" = None,
    ) -> List[dict]:
        """检索（向后兼容扩展版）。

        支持通过 `parsed`（ParsedQuery）传入结构化过滤条件；亦可直接传
        tag_filter / path_filter / exclude_terms。两种入口二选一，parsed 优先。
        """
        # 1) 解析 / 归一化过滤条件
        if parsed is not None:
            clean_query = parsed.clean_query
            tag_filters = list(parsed.tag_filters)
            path_filters = list(parsed.path_filters)
            exclude_terms = list(parsed.exclude_terms)
            phrase = parsed.phrase or ""
        else:
            clean_query = query
            if tag_filter:
                tag_filters = [tag_filter] if isinstance(tag_filter, str) else list(tag_filter)
            else:
                tag_filters = []
            path_filters = list(path_filter or [])
            exclude_terms = list(exclude_terms or [])
            phrase = ""

        # === 增量（P1-1）布尔 AST 分支：仅当 parsed 含布尔时走新路径 ===
        # 旧「扁平字段」路径 100% 保留；此分支门控，不影响任何旧调用/旧测试。
        if parsed is not None and parsed.has_boolean:
            return self._eval_bool_path(parsed, top_k, mode)

        # 左栏标签筛选（兼容旧行为：单个 tag_filter 字符串）并入查询的 tag 过滤
        if tag_filter and isinstance(tag_filter, str) and tag_filter not in tag_filters:
            tag_filters.append(tag_filter)

        # 空查询：若存在 tag / path 过滤，进入"按 metadata 浏览"模式，
        # 直接返回这些文件对应的全部 chunk；仅当无任何过滤时才返回 []。
        if not clean_query.strip():
            if not tag_filters and not path_filters:
                return []
            return self._browse_by_metadata(tag_filters, path_filters, top_k)

        mode = mode or self._search_mode

        # 2) 计算 allowed_files 交集（tag ∩ path）
        allowed_files = self._resolve_allowed_files(tag_filters, path_filters)
        if allowed_files is not None and not allowed_files:
            return []  # 任一过滤条件无命中 → 空集

        filter_metadata = (
            {"file_path": list(allowed_files)} if allowed_files else None
        )

        semantic = None
        lexical = None

        if mode == "semantic":
            query_embedding = self.embedding_engine.encode_single(clean_query)
            semantic = self.vector_store.search(
                query_embedding=query_embedding,
                top_k=max(top_k * 3, 30),
                filter_metadata=filter_metadata,
            )
            results = semantic[:top_k]
        elif mode == "keyword":
            lexical = self.lexical.search(clean_query, top_k=max(top_k * 3, 30))
            if allowed_files is not None:
                lexical = [r for r in lexical
                           if r["metadata"].get("file_path") in allowed_files]
            results = lexical[:top_k]
        else:  # hybrid
            query_embedding = self.embedding_engine.encode_single(clean_query)
            semantic = self.vector_store.search(
                query_embedding=query_embedding,
                top_k=max(top_k * 3, 30),
                filter_metadata=filter_metadata,
            )
            lexical = self.lexical.search(clean_query, top_k=max(top_k * 3, 30))
            results = search_fusion.rrf(semantic, lexical, top_n=top_k)
            # 混合模式下对最终结果再过滤词法独有的结果（语义结果已按 file_path 过滤）
            if allowed_files is not None:
                results = [r for r in results
                           if r["metadata"].get("file_path") in allowed_files]

        # 3) exclude_terms → 融合后内容级后过滤（语义排除为近似，非向量级）
        if exclude_terms:
            results = [r for r in results
                       if not self._content_has_any(r.get("content", "") or "", exclude_terms)]

        # 归一化展示用评分，避免关键词/混合模式显示 0%
        self._normalize_display_scores(results, mode)

        # 4) 补充 file_tags / matched_terms / 增强 snippet
        for result in results:
            file_path = result["metadata"].get("file_path", "")
            result["file_tags"] = self.tag_manager.get_tags_for_file(file_path)
            content = result.get("content", "") or ""
            matched_terms = self._compute_matched_terms(clean_query, phrase, content)
            result["matched_terms"] = matched_terms
            result["snippet"] = self._build_snippet(content, matched_terms)
            result["search_mode"] = mode
        return results

    # ------------------------------------------------------------------ #
    # 空查询 + 标签 / 路径过滤的"按 metadata 浏览"模式
    # ------------------------------------------------------------------ #
    def _browse_by_metadata(
        self,
        tag_filters: List[str],
        path_filters: List[str],
        top_k: int,
    ) -> List[dict]:
        """空查询时，仅依据 tag / path 过滤浏览全部命中文件对应的 chunk。

        空 query 无法走 BM25 / 向量检索（需要 query 向量），故直接按 file_path
        从向量库列举这些文件的所有 chunk 返回，使"按标签浏览"可用。
        """
        allowed_files = self._resolve_allowed_files(tag_filters, path_filters)
        if allowed_files is None or not allowed_files:
            return []

        chunks = self.vector_store.get_chunks_by_files(allowed_files)
        if not chunks:
            return []

        results = []
        for c in chunks:
            content = c.get("content", "") or ""
            meta = c.get("metadata", {}) or {}
            file_path = meta.get("file_path", "")
            results.append({
                "id": c.get("id"),
                "content": content,
                "metadata": meta,
                "distance": None,
                "similarity": None,
                "lexical_score": 0.0,
                "rrf_score": None,
                "file_tags": self.tag_manager.get_tags_for_file(file_path),
                "matched_terms": [],
                "snippet": self._build_snippet(content, []),
                "search_mode": "browse",
            })
        # 按文件路径稳定排序，便于 UI 稳定展示
        results.sort(key=lambda r: r["metadata"].get("file_path", ""))
        return results[:top_k]

    # ------------------------------------------------------------------ #
    # 过滤条件解析辅助
    # ------------------------------------------------------------------ #
    def _resolve_allowed_files(
        self,
        tag_filters: List[str],
        path_filters: List[str],
    ) -> Optional[set]:
        """求 tag 过滤与 path 过滤的文件交集；某类无过滤则忽略该类。

        返回 None 表示无文件级约束；返回空集表示约束后无命中文件。
        """
        allowed = None
        if tag_filters:
            sets = [set(self.tag_manager.get_files_by_tag(t)) for t in tag_filters]
            # 任一标签完全无文件 → 交集为空
            if any(len(s) == 0 for s in sets):
                return set()
            allowed = set.intersection(*sets) if sets else set()
            if not allowed:
                return set()

        if path_filters:
            pf = self._files_matching_paths(path_filters)
            allowed = pf if allowed is None else (allowed & pf)
            if not allowed:
                return set()

        return allowed

    def _files_matching_paths(self, path_filters: List[str]) -> set:
        """返回满足任一 path 过滤模式的文件集合（相对 current_folder 解释）。"""
        indexed = self.vector_store.get_indexed_files()
        result = set()
        for fp in indexed.keys():
            for pat in path_filters:
                if self._path_match(fp, pat):
                    result.add(fp)
                    break
        return result

    @staticmethod
    def _path_match(file_path: str, pattern: str) -> bool:
        """path 匹配：先按 fnmatch 通配（basename + 全路径），再退化为大小写不敏感子串。"""
        if not pattern:
            return False
        base = os.path.basename(file_path)
        if fnmatch.fnmatch(file_path, pattern) or fnmatch.fnmatch(base, pattern):
            return True
        return pattern.lower() in file_path.lower()

    @staticmethod
    def _content_has_any(content: str, terms: List[str]) -> bool:
        """内容级排除：content 含任一排除词（大小写不敏感）则 True。"""
        if not content or not terms:
            return False
        cl = content.lower()
        return any((t and t.lower() in cl) for t in terms)

    @staticmethod
    def _compute_matched_terms(query_text: str, phrase: str, content: str) -> List[str]:
        """计算 content 中实际命中的查询词（用于 <mark> 高亮）。

        候选词来源：短语 phrase、空格切分 token、以及整句（覆盖无空格的中文短语）。
        只返回确实作为子串出现在 content 中的词，避免空高亮。
        """
        if not content:
            return []
        cl = content.lower()
        cands = set()
        if phrase:
            cands.add(phrase)
        qt = (query_text or "").strip()
        if qt:
            cands.add(qt)
            for tok in re.split(r"\s+", qt):
                if tok:
                    cands.add(tok)
        matched = [c for c in cands if c and c.lower() in cl]
        return matched

    @staticmethod
    def _build_snippet(content: str, matched_terms: List[str], max_len: int = 240) -> str:
        """围绕首个命中词截取 ~max_len 字窗口；无命中则取开头。"""
        if not content:
            return ""
        if not matched_terms:
            snip = content[:max_len]
            return snip + ("…" if len(content) > max_len else "")
        cl = content.lower()
        idx = len(content)
        for t in matched_terms:
            pos = cl.find(t.lower())
            if pos != -1 and pos < idx:
                idx = pos
        start = max(0, idx - 40)
        end = min(len(content), start + max_len)
        start = max(0, end - max_len)
        snip = content[start:end]
        if start > 0:
            snip = "…" + snip
        if end < len(content):
            snip = snip + "…"
        return snip

    # ------------------------------------------------------------------ #
    # 相似文章推荐（文件级）
    # ------------------------------------------------------------------ #
    def query_similar(self, file_path: str, top_k: int = 5) -> List[dict]:
        """相似文章推荐：包装 vectorstore.query_similar，补充 file_tags / snippet /
        matched_terms，使返回结构与 search 结果同构（设计 §3.1），便于 UI 复用渲染。

        返回元素：
            {id(=file_path), content, metadata, distance, similarity,
             lexical_score, rrf_score, file_tags, snippet, matched_terms, search_mode}
        """
        raw = self.vector_store.query_similar(file_path, top_k=top_k)
        results = []
        for item in raw:
            meta = item.get("metadata", {}) or {}
            fp = meta.get("file_path", "")
            content = item.get("content", "") or ""
            results.append({
                "id": fp,  # 文件级聚合后以 file_path 作为唯一 id
                "content": content,
                "metadata": meta,
                "distance": item.get("distance"),
                "similarity": item.get("similarity", 0.0),
                "lexical_score": 0.0,
                "rrf_score": None,
                "file_tags": self.tag_manager.get_tags_for_file(fp),
                "matched_terms": [],
                "snippet": self._build_snippet(content, []),
                "search_mode": "similar",
            })
        return results

    # ------------------------------------------------------------------ #
    # P2-6 跨文件链接图谱（外观，委托 core.link_graph）
    # ------------------------------------------------------------------ #
    def build_link_graph(self) -> "LinkGraph":
        """构建跨文件链接图谱（节点=文章，边=引用关系）。

        薄委托 core.link_graph.LinkGraphBuilder.build，与原 query_similar 风格一致。
        仅读磁盘内容、不调用 encode、不写 tags.json（三权分立零冲突）。
        """
        from .link_graph import LinkGraphBuilder
        return LinkGraphBuilder().build(self)

    # ------------------------------------------------------------------ #
    # P2-12 近似/重复文章检测（外观，委托 core.dedup）
    # ------------------------------------------------------------------ #
    def find_duplicate_pairs(self, threshold: float = 0.85) -> "List[DuplicatePair]":
        """全库近似/重复文章检测（与 query_similar 不同：query_similar 是单文件 top-k）。

        薄委托 core.dedup.find_duplicate_pairs：复用 get_file_vector（不 encode），
        分块余弦相似度收集 sim≥threshold 的无重复 (i<j) 对。结果为只读分析，
        不写 tags.json。
        """
        from .dedup import find_duplicate_pairs as _find
        return _find(self, threshold=threshold)

    @staticmethod
    def _normalize_display_scores(results: List[dict], mode: str):
        """为关键词/混合模式补齐展示用 similarity（0~1），避免结果卡片显示 0.0%"""
        if mode == "keyword":
            mx = max((r.get("lexical_score", 0.0) or 0.0) for r in results) or 1.0
            for r in results:
                r["similarity"] = (r.get("lexical_score", 0.0) or 0.0) / mx
        elif mode == "hybrid":
            for r in results:
                if "similarity" not in r or r.get("similarity") is None:
                    mx = max((rr.get("lexical_score", 0.0) or 0.0)
                             for rr in results) or 1.0
                    r["similarity"] = (r.get("lexical_score", 0.0) or 0.0) / mx

    # ------------------------------------------------------------------ #
    # 文件管理
    # ------------------------------------------------------------------ #
    def list_indexed_files(self) -> Dict[str, dict]:
        return self.vector_store.get_indexed_files()

    def remove_file_from_index(self, file_path: str):
        self.vector_store.delete_by_file(file_path)
        self.lexical.remove_file(file_path)
        self.tag_manager.remove_file(file_path)
        self._content_cache.pop(file_path, None)

    def reindex_file(self, file_path: str):
        if not os.path.isfile(file_path):
            raise FileNotFoundError(file_path)
        meta = self.file_scanner.extract_metadata(file_path)
        self._process_file(meta)
        self.vector_store.set_file_md5(file_path, meta.md5_hash)
        self.tag_manager.save()
        self.lexical.save()

    # ------------------------------------------------------------------ #
    # 星标（功能7）：与 file_tags / 簇 三权分立
    # ------------------------------------------------------------------ #
    def star_file(self, file_path: str):
        self.tag_manager.star(file_path)
        self.tag_manager.save()

    def unstar_file(self, file_path: str):
        self.tag_manager.unstar(file_path)
        self.tag_manager.save()

    def is_starred_file(self, file_path: str) -> bool:
        return self.tag_manager.is_starred(file_path)

    def get_starred_files(self) -> List[str]:
        return self.tag_manager.get_starred()

    # ------------------------------------------------------------------ #
    # 批量操作（功能8）
    # ------------------------------------------------------------------ #
    def batch_add_tags(self, file_paths: List[str], tags: List[str]):
        tags = list(tags or [])
        for p in file_paths:
            existing = self.tag_manager.get_tags_for_file(p)
            merged = list(dict.fromkeys(list(existing) + tags))
            self.tag_manager.set_tags_for_file(p, merged)
        self.tag_manager.save()

    def batch_reindex(self, file_paths: List[str], progress_callback=None):
        total = len(file_paths)
        for i, p in enumerate(file_paths):
            try:
                self.reindex_file(p)
            except Exception as e:
                logger.error("批量重建失败 %s: %s", p, e)
            if progress_callback:
                progress_callback(i + 1, total, os.path.basename(p))

    def batch_remove(self, file_paths: List[str]):
        for p in file_paths:
            self.remove_file_from_index(p)
        self.tag_manager.save()
        self.lexical.save()

    @staticmethod
    def open_file_in_explorer(file_path: str):
        try:
            if sys.platform == "win32":
                os.startfile(file_path)  # noqa: S404 - 用户主动触发的资源管理器打开
            elif sys.platform == "darwin":
                os.system(f'open "{file_path}"')
            else:
                os.system(f'xdg-open "{file_path}"')
        except Exception as e:
            logger.warning("打开文件失败: %s", e)

    # ------------------------------------------------------------------ #
    # 内容读取
    # ------------------------------------------------------------------ #
    def get_file_content(self, file_path: str) -> str:
        cached = self._content_cache.get(file_path)
        if cached is not None:
            self._content_cache.move_to_end(file_path)
            return cached
        try:
            ext = Path(file_path).suffix
            if ext == ".pdf":
                paras = ContentParser.parse_pdf(file_path)
                content = "\n".join(p["content"] for p in paras)
            elif ext == ".docx":
                paras = ContentParser.parse_docx(file_path)
                content = "\n".join(p["content"] for p in paras)
            else:
                content = Path(file_path).read_text(encoding="utf-8", errors="ignore")
            self._content_cache[file_path] = content
            while len(self._content_cache) > self._content_cache_max:
                self._content_cache.popitem(last=False)
            return content
        except Exception as e:
            logger.error("读取文件失败 %s: %s", file_path, e)
            return ""

    # ------------------------------------------------------------------ #
    # 标签 / 状态
    # ------------------------------------------------------------------ #
    def get_all_tags(self) -> List[str]:
        return self.tag_manager.get_all_tags()

    def get_tag_counts(self) -> Dict[str, int]:
        return self.tag_manager.get_tag_counts()

    def get_indexed_files(self) -> Dict[str, dict]:
        return self.vector_store.get_indexed_files()

    def get_status(self) -> Dict[str, Any]:
        indexed = self.vector_store.get_indexed_files()
        # 每源文件数（按源根前缀归属；无法归属的计入"其他"）
        per_source = []
        for s in self._sources:
            if not s.enabled:
                continue
            ap = os.path.abspath(s.path)
            cnt = sum(
                1 for fp in indexed.keys() if os.path.abspath(fp).startswith(ap)
            )
            per_source.append({
                "path": s.path, "enabled": s.enabled,
                "files": cnt, "excluded_rules": s.exclude_rules,
            })
        total_chars = sum(len(d.get("text", "")) for d in self.lexical.docs.values())
        return {
            # —— 既有字段保留（向后兼容）——
            "current_folder": self.current_folder,
            "indexed_files": len(indexed),
            "total_chunks": self.vector_store.get_chunk_count(),
            "lexical_chunks": self.lexical.count(),
            "search_mode": self._search_mode,
            "embedding_model": self.embedding_engine.get_status_info(),
            "tags": self.get_tag_counts(),
            "index_stale": self._index_stale,
            # —— P1 多源 / 统计扩展 ——
            "sources": per_source,
            "total_files": len(indexed),
            "total_chars": total_chars,
            "tag_distribution": self.get_tag_counts(),
            "cluster_distribution": {
                c.get("label", ""): len(c.get("files", []))
                for c in self.tag_manager.get_clusters().values()
            },
            "recent_updates": self._recent_updates(),
            "starred_count": len(self.tag_manager.get_starred()),
        }

    def _recent_updates(self, n: int = 10) -> List[Dict[str, Any]]:
        files = self.vector_store.get_indexed_files()
        items = []
        for fp in files.keys():
            try:
                mt = os.path.getmtime(fp)
            except OSError:
                mt = 0
            items.append((fp, mt))
        items.sort(key=lambda x: x[1], reverse=True)
        return [{"file_path": fp, "modified_time": mt} for fp, mt in items[:n]]

    @staticmethod
    def _compute_file_md5(file_path: str) -> str:
        import hashlib
        hasher = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    # ------------------------------------------------------------------ #
    # 增量（P1-1）布尔 AST 求值路径
    # ------------------------------------------------------------------ #
    def _eval_bool_path(
        self, parsed: ParsedQuery, top_k: int, mode: str
    ) -> List[dict]:
        """布尔求值主路径（由 search() 在 parsed.has_boolean 时调用）。

        1) allowed = _eval_tag_path_expr(parsed.expr)  文件级 并/交/差/分组
        2) clean_query 为空且仅有 tag/path 过滤 → 浏览模式（_browse_by_files）
        3) 否则 clean_query 正项拼接检索，filter_metadata=allowed
        4) results = _eval_text_bool(results, parsed.expr)  内容级 AND/OR/NOT 后过滤
        5) 补充 file_tags / matched_terms / snippet
        """
        expr = parsed.expr
        allowed = self._eval_tag_path_expr(expr)  # None | set
        clean_query = parsed.clean_query or ""

        # 纯 tag/path 布尔浏览（无检索词）
        if not clean_query.strip():
            if allowed is None:
                return []
            return self._browse_by_files(allowed, top_k)

        mode = mode or self._search_mode
        # Bug #1 修复：allowed 为「空 set」表示文件级约束求交为空（应返回空集），
        # 必须区分「空 set」与「None（无文件约束）」。用 `is not None` 判定，
        # 否则空 set 被视为无约束而返回全部文件。
        filter_metadata = (
            {"file_path": list(allowed)} if allowed is not None else None
        )

        semantic = None
        lexical = None
        if mode == "semantic":
            query_embedding = self.embedding_engine.encode_single(clean_query)
            semantic = self.vector_store.search(
                query_embedding=query_embedding,
                top_k=max(top_k * 3, 30),
                filter_metadata=filter_metadata,
            )
            results = semantic[:top_k]
        elif mode == "keyword":
            lexical = self.lexical.search(clean_query, top_k=max(top_k * 3, 30))
            if allowed is not None:
                lexical = [r for r in lexical
                           if r["metadata"].get("file_path") in allowed]
            results = lexical[:top_k]
        else:  # hybrid
            query_embedding = self.embedding_engine.encode_single(clean_query)
            semantic = self.vector_store.search(
                query_embedding=query_embedding,
                top_k=max(top_k * 3, 30),
                filter_metadata=filter_metadata,
            )
            lexical = self.lexical.search(clean_query, top_k=max(top_k * 3, 30))
            results = search_fusion.rrf(semantic, lexical, top_n=top_k)
            if allowed is not None:
                results = [r for r in results
                           if r["metadata"].get("file_path") in allowed]

        # 内容级布尔后过滤（语义模式近似，与现有 exclude_terms 近似排除一致）
        results = self._eval_text_bool(results, expr)

        # 归一化展示用评分，避免关键词/混合模式显示 0%
        self._normalize_display_scores(results, mode)

        # 补充 file_tags / matched_terms / 增强 snippet
        for result in results:
            file_path = result["metadata"].get("file_path", "")
            result["file_tags"] = self.tag_manager.get_tags_for_file(file_path)
            content = result.get("content", "") or ""
            matched_terms = self._compute_matched_terms(
                clean_query, parsed.phrase or "", content)
            result["matched_terms"] = matched_terms
            result["snippet"] = self._build_snippet(content, matched_terms)
            result["search_mode"] = mode
        return results

    def _browse_by_files(self, file_set: set, top_k: int) -> List[dict]:
        """按文件集合浏览：返回这些文件对应的全部 chunk（布尔 OR 浏览用）。"""
        if not file_set:
            return []
        chunks = self.vector_store.get_chunks_by_files(file_set)
        if not chunks:
            return []
        results = []
        for c in chunks:
            content = c.get("content", "") or ""
            meta = c.get("metadata", {}) or {}
            file_path = meta.get("file_path", "")
            results.append({
                "id": c.get("id"),
                "content": content,
                "metadata": meta,
                "distance": None,
                "similarity": None,
                "lexical_score": 0.0,
                "rrf_score": None,
                "file_tags": self.tag_manager.get_tags_for_file(file_path),
                "matched_terms": [],
                "snippet": self._build_snippet(content, []),
                "search_mode": "browse",
            })
        results.sort(key=lambda r: r["metadata"].get("file_path", ""))
        return results[:top_k]

    def _eval_tag_path_expr(self, node: Optional[BoolExpr]) -> Optional[set]:
        """文件级布尔求值（精确）：返回 None 表示无文件级约束。

        AndNode → 子结果交集（任一约束无命中文件→空集）；
        OrNode  → 并集；
        NotNode → (全集索引文件 - child 文件集)；
        TagNode → set(tag_manager.get_files_by_tag(tag))（negated 时取差集）；
        PathNode→ _files_matching_paths([pattern])（negated 时取差集）；
        TermNode→ None（不参与文件级过滤，留给 _eval_text_bool）。
        """
        if node is None:
            return None
        full_set = set(self.vector_store.get_indexed_files().keys())

        if isinstance(node, TagNode):
            s = set(self.tag_manager.get_files_by_tag(node.tag))
            return (full_set - s) if node.negated else s
        if isinstance(node, PathNode):
            s = self._files_matching_paths([node.pattern])
            return (full_set - s) if node.negated else s
        if isinstance(node, TermNode):
            return None
        if isinstance(node, NotNode):
            child_set = self._eval_tag_path_expr(node.child)
            if child_set is None:
                return None
            return full_set - child_set
        if isinstance(node, AndNode):
            resolved = []
            for child in node.children:
                r = self._eval_tag_path_expr(child)
                if r is None:
                    continue
                if len(r) == 0:
                    return set()  # 任一约束无命中文件 → 空集
                resolved.append(r)
            if not resolved:
                return None
            acc = resolved[0]
            for s in resolved[1:]:
                acc = acc & s
                if not acc:
                    return set()
            return acc
        if isinstance(node, OrNode):
            resolved = []
            for child in node.children:
                r = self._eval_tag_path_expr(child)
                if r is None:
                    continue
                resolved.append(r)
            if not resolved:
                return None
            acc = set()
            for s in resolved:
                acc = acc | s
            return acc
        return None

    @staticmethod
    def _is_file_level(node: Optional[BoolExpr]) -> bool:
        """判断节点是否为「文件级」约束（TagNode/PathNode 及其 Not/And/Or 组合）。

        TagNode/PathNode 是文件级约束，已由 allowed 在 _eval_tag_path_expr 精确处理；
        其内容级否定（NotNode 包裹）同样属于文件级，不应在 _eval_text_bool 中再被
        内容否定（否则会把结果整体清空）。TermNode（检索词/短语）视为内容级，返回 False。
        """
        if node is None:
            return False
        if isinstance(node, (TagNode, PathNode)):
            return True
        if isinstance(node, NotNode):
            return ArticleSearchEngine._is_file_level(node.child)
        if isinstance(node, (AndNode, OrNode)):
            return any(ArticleSearchEngine._is_file_level(ch) for ch in node.children)
        return False

    def _eval_text_bool(self, results: List[dict], node: Optional[BoolExpr]) -> List[dict]:
        """内容级布尔后过滤（近似，与现有 exclude_terms 近似排除一致）。

        AndNode → 所有子项命中才保留；OrNode → 任一命中保留；NotNode → 不命中保留；
        TermNode → result.content 含 text（大小写不敏感）为命中；
        TagNode/PathNode → 视为 True（文件级已过滤）。
        """
        if node is None:
            return results
        if isinstance(node, TermNode):
            text = (node.text or "").lower()
            if node.negated:
                return [r for r in results
                        if text not in (r.get("content", "") or "").lower()]
            return [r for r in results
                    if text in (r.get("content", "") or "").lower()]
        if isinstance(node, NotNode):
            # Bug #2 修复：文件级否定（TagNode/PathNode 及其组合）已由 allowed 在
            # _eval_tag_path_expr 处理，内容级直接放行，避免整体清空结果。
            if self._is_file_level(node):
                return results
            child = self._eval_text_bool(results, node.child)
            keep = {id(r) for r in child}
            return [r for r in results if id(r) not in keep]
        if isinstance(node, AndNode):
            acc = results
            for child in node.children:
                acc = self._eval_text_bool(acc, child)
            return acc
        if isinstance(node, OrNode):
            keep = set()
            for child in node.children:
                keep |= {id(r) for r in self._eval_text_bool(results, child)}
            return [r for r in results if id(r) in keep]
        # TagNode / PathNode → 文件级已过滤，视为 True
        return results
