"""
engine.search() 过滤与片段增强（P0-3）集成测试 + parsed-only 契约回归。

不依赖真实嵌入 / BM25 质量，仅 monkeypatch 掉 vector_store.search /
lexical.search / embedding_engine.encode_single，聚焦验证 P0-3 新增的
编排逻辑是否正确：
  - 多 tag 交集（tag: A tag: B）
  - path 过滤（fnmatch + 子串）
  - exclude 词在语义 / 混合模式下均生效（内容级后过滤）
  - matched_terms 计算与 snippet 命中窗口增强
  - 向后兼容：仅传 parsed（无 query）也能调用（回归保护，见 test_parse_only_api）

说明：本文件为 QA 验证版（构造真实 ArticleSearchEngine，仅替换检索后端返回，
避免重复加载模型；chromadb/onnxruntime 在本项目 venv 中均可用，构造不触发推理）。
运行: python tests/test_search_filters.py
（也由 tests/run_all.py 统一运行）
"""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np
from core.engine import ArticleSearchEngine
from core.query_parser import ParsedQuery


def _mk_chunk(cid, content, file_path, file_name, sim=0.9, lex=1.0):
    return {
        "id": cid,
        "content": content,
        "metadata": {
            "file_path": file_path, "file_name": file_name, "title": file_name,
            "start_line": 0, "end_line": 1, "chunk_index": 0, "total_chunks": 1,
        },
        "distance": 1 - sim,
        "similarity": sim,
        "lexical_score": lex,
    }


class TestSearchFilters(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.eng = ArticleSearchEngine(db_path=os.path.join(self.tmp, "chromadb"))
        # 让 path 过滤解析能看到这两个已索引文件
        self.eng.vector_store._index_meta["files"] = {
            "/docs/notes/alpha.md": {"chunk_count": 1, "title": "A", "file_name": "alpha.md"},
            "/docs/other/beta.md": {"chunk_count": 1, "title": "B", "file_name": "beta.md"},
        }
        self.eng.tag_manager._tag_index = {
            "ML": {"/docs/notes/alpha.md"},
            "DL": {"/docs/notes/alpha.md", "/docs/other/beta.md"},
        }

        self.alpha = _mk_chunk("c1", "深度学习是神经网络的核心技术",
                               "/docs/notes/alpha.md", "alpha.md", 0.9, 1.0)
        self.beta = _mk_chunk("c2", "这是一条广告垃圾内容不相关",
                              "/docs/other/beta.md", "beta.md", 0.5, 0.5)

        def fake_vs(query_embedding=None, top_k=10, filter_metadata=None):
            res = [dict(self.alpha), dict(self.beta)]
            if filter_metadata:
                fps = set(filter_metadata.get("file_path", []))
                res = [r for r in res if r["metadata"]["file_path"] in fps]
            return res[:top_k]

        def fake_lex(q, top_k=10):
            return [dict(self.alpha), dict(self.beta)]

        self.eng.vector_store.search = fake_vs
        self.eng.lexical.search = fake_lex
        self.eng.embedding_engine.encode_single = lambda q: np.zeros(8)

    def _paths(self, results):
        return [r["metadata"]["file_path"] for r in results]

    # ---- exclude 词后过滤 ----
    def test_exclude_semantic(self):
        p = ParsedQuery(clean_query="深度学习", exclude_terms=["广告"])
        out = self.eng.search(query=p.clean_query, parsed=p, mode="semantic")
        paths = self._paths(out)
        self.assertNotIn("/docs/other/beta.md", paths)   # 含"广告"被剔除
        self.assertIn("/docs/notes/alpha.md", paths)

    def test_exclude_hybrid(self):
        p = ParsedQuery(clean_query="深度学习", exclude_terms=["广告"])
        out = self.eng.search(query=p.clean_query, parsed=p, mode="hybrid")
        self.assertNotIn("/docs/other/beta.md", self._paths(out))

    def test_exclude_keeps_nonmatching(self):
        # 没有排除词时两条都应保留
        p = ParsedQuery(clean_query="深度学习")
        out = self.eng.search(query=p.clean_query, parsed=p, mode="semantic")
        self.assertEqual(set(self._paths(out)),
                         {"/docs/notes/alpha.md", "/docs/other/beta.md"})

    # ---- path 过滤 ----
    def test_path_filter(self):
        p = ParsedQuery(clean_query="深度学习", path_filters=["notes"])
        out = self.eng.search(query=p.clean_query, parsed=p, mode="semantic")
        self.assertEqual(self._paths(out), ["/docs/notes/alpha.md"])

    def test_path_filter_glob(self):
        p = ParsedQuery(clean_query="深度学习", path_filters=["*.md"])
        out = self.eng.search(query=p.clean_query, parsed=p, mode="semantic")
        self.assertEqual(set(self._paths(out)),
                         {"/docs/notes/alpha.md", "/docs/other/beta.md"})

    # ---- 多 tag 交集 ----
    def test_tag_intersection(self):
        p = ParsedQuery(clean_query="深度学习", tag_filters=["ML", "DL"])
        out = self.eng.search(query=p.clean_query, parsed=p, mode="semantic")
        # 仅 alpha 同时拥有 ML 与 DL
        self.assertEqual(self._paths(out), ["/docs/notes/alpha.md"])

    def test_tag_no_intersection_returns_empty(self):
        p = ParsedQuery(clean_query="深度学习", tag_filters=["ML", "不存在的标签"])
        out = self.eng.search(query=p.clean_query, parsed=p, mode="semantic")
        self.assertEqual(out, [])

    # ---- matched_terms / snippet ----
    def test_matched_terms_populated(self):
        p = ParsedQuery(clean_query="深度学习", exclude_terms=["广告"])
        out = self.eng.search(query=p.clean_query, parsed=p, mode="semantic")
        self.assertEqual(out[0]["matched_terms"], ["深度学习"])

    def test_snippet_centers_on_match(self):
        p = ParsedQuery(clean_query="神经网络")
        out = self.eng.search(query=p.clean_query, parsed=p, mode="semantic")
        snip = out[0]["snippet"]
        self.assertIn("神经网络", snip)

    # ---- 向后兼容：仅传 parsed（无 query）也应可调用 ----
    # 锁定"设计意图"：UI 统一 parse 后把 parsed 传入，engine 不再重复解析。
    # engine.search 曾把 query 设为必填位置参数而 UI 不传，导致 TypeError；
    # 修复：query 默认值改为 ""。本用例即该回归保护。
    def test_parse_only_api(self):
        p = ParsedQuery(clean_query="深度学习", exclude_terms=["广告"])
        try:
            out = self.eng.search(parsed=p, mode="semantic")
        except TypeError as e:
            self.fail(
                "engine.search(parsed=...) 调用缺少 query 参数而报错，"
                "请为 engine.search 的 query 参数加默认值 '' 以兼容 parsed-only 调用: %s" % e
            )
        self.assertNotIn("/docs/other/beta.md", self._paths(out))

    # ---- 空查询 + 标签浏览（修复 D：按标签浏览失效）----
    def test_tag_browse_empty_query(self):
        """空查询 + 标签筛选应返回该标签下全部文件对应的 chunk（按标签浏览）。

        回归保护：search() 早期 `if not clean_query.strip(): return []` 会跳过
        tag/path 过滤，导致左栏点标签、搜索框为空时永远 0 条。
        """
        p = ParsedQuery(clean_query="", tag_filters=["DL"])

        def fake_get_chunks_by_files(file_paths):
            fps = set(file_paths)
            return [dict(ch) for ch in (self.alpha, self.beta)
                    if ch["metadata"]["file_path"] in fps]

        self.eng.vector_store.get_chunks_by_files = fake_get_chunks_by_files
        out = self.eng.search(query=p.clean_query, parsed=p, mode="semantic")
        paths = self._paths(out)
        self.assertTrue(paths, "空查询 + 标签过滤必须返回非空结果（按标签浏览）")
        # 返回结果应恰好等于引擎解析出的该标签覆盖的文件集合
        expected = set(self.eng.tag_manager.get_files_by_tag("DL"))
        self.assertTrue(expected, "测试夹具需存在带 DL 标签的文件")
        self.assertEqual(set(paths), expected)

    def test_empty_query_no_filters_returns_empty(self):
        """空查询且无任何过滤时仍返回 []（向后兼容行为，未破坏）"""
        p = ParsedQuery(clean_query="")
        out = self.eng.search(query=p.clean_query, parsed=p, mode="semantic")
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
