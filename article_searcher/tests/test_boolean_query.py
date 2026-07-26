"""
布尔表达式解析 + 引擎并/差/分组过滤测试（T02 / P1-1）

不依赖真实嵌入 / BM25 质量，仅 monkeypatch 掉 vector_store.search /
lexical.search / embedding_engine.encode_single，聚焦验证：
  - parse_query 布尔 AST 构建（OR / NOT / 括号 / -tag: / -path: 置 has_boolean）
  - 文本级 -排除词 不置 has_boolean（走旧 exclude_terms 路径，向后兼容）
  - engine.search 走 _eval_bool_path：tag/path 文件级 并/交/差/分组 + 内容级 AND/OR/NOT 后过滤
  - build_tag_filter_parsed / combine_parsed 辅助

运行: python tests/test_boolean_query.py
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
from core.query_parser import (
    parse_query, ParsedQuery, build_tag_filter_parsed, combine_parsed,
    TagNode, PathNode, TermNode, NotNode, AndNode, OrNode,
)


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


class TestBooleanParser(unittest.TestCase):

    def test_or_sets_has_boolean(self):
        p = parse_query("tag:ML OR tag:DL")
        self.assertTrue(p.has_boolean)
        self.assertIsInstance(p.expr, OrNode)
        self.assertEqual(len(p.expr.children), 2)
        self.assertEqual(p.expr.children[0].tag, "ML")
        self.assertEqual(p.expr.children[1].tag, "DL")

    def test_plain_no_boolean(self):
        p = parse_query("深度学习 神经网络")
        self.assertFalse(p.has_boolean)
        self.assertIsInstance(p.expr, AndNode)

    def test_not_tag(self):
        p = parse_query("-tag:ML")
        self.assertTrue(p.has_boolean)
        self.assertIsInstance(p.expr, NotNode)
        self.assertIsInstance(p.expr.child, TagNode)
        self.assertEqual(p.expr.child.tag, "ML")

    def test_not_keyword(self):
        p = parse_query("tag:ML NOT tag:DL")
        self.assertTrue(p.has_boolean)
        self.assertIsInstance(p.expr, AndNode)
        self.assertIsInstance(p.expr.children[0], TagNode)
        self.assertIsInstance(p.expr.children[1], NotNode)

    def test_paren_grouping(self):
        p = parse_query("(tag:ML OR tag:DL) NOT tag:X")
        self.assertTrue(p.has_boolean)
        self.assertIsInstance(p.expr, AndNode)
        self.assertIsInstance(p.expr.children[0], OrNode)
        self.assertIsInstance(p.expr.children[1], NotNode)

    def test_exclude_text_not_boolean(self):
        p = parse_query("深度学习 -广告")
        self.assertFalse(p.has_boolean)
        self.assertEqual(p.exclude_terms, ["广告"])
        self.assertEqual(p.clean_query, "深度学习")

    def test_or_text_terms(self):
        p = parse_query('"深度学习" OR "神经网络"')
        self.assertTrue(p.has_boolean)
        self.assertIsInstance(p.expr, OrNode)

    def test_negated_path_sets_boolean(self):
        p = parse_query("-path:草稿")
        self.assertTrue(p.has_boolean)
        self.assertIsInstance(p.expr, NotNode)
        self.assertIsInstance(p.expr.child, PathNode)


class TestBooleanEngine(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.eng = ArticleSearchEngine(db_path=os.path.join(self.tmp, "chromadb"))
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

        def fake_get_chunks_by_files(file_paths):
            fps = set(file_paths)
            return [dict(ch) for ch in (self.alpha, self.beta)
                    if ch["metadata"]["file_path"] in fps]

        self.eng.vector_store.search = fake_vs
        self.eng.lexical.search = fake_lex
        self.eng.embedding_engine.encode_single = lambda q: np.zeros(8)
        self.eng.vector_store.get_chunks_by_files = fake_get_chunks_by_files

    def _paths(self, results):
        return [r["metadata"]["file_path"] for r in results]

    def test_or_tag_union(self):
        p = parse_query("tag:ML OR tag:DL")
        out = self.eng.search(parsed=p, mode="semantic")
        paths = self._paths(out)
        self.assertIn("/docs/notes/alpha.md", paths)
        self.assertIn("/docs/other/beta.md", paths)

    def test_and_not_tag(self):
        p = parse_query("tag:DL NOT tag:ML")
        out = self.eng.search(parsed=p, mode="semantic")
        paths = self._paths(out)
        self.assertEqual(paths, ["/docs/other/beta.md"])

    def test_text_or_filtering(self):
        p = parse_query("深度学习 OR 广告")
        out = self.eng.search(parsed=p, mode="semantic")
        paths = self._paths(out)
        self.assertIn("/docs/notes/alpha.md", paths)
        self.assertIn("/docs/other/beta.md", paths)

    def test_text_not_filtering(self):
        p = parse_query("深度学习 NOT 广告")
        out = self.eng.search(parsed=p, mode="semantic")
        paths = self._paths(out)
        self.assertIn("/docs/notes/alpha.md", paths)
        self.assertNotIn("/docs/other/beta.md", paths)

    def test_boolean_or_browse_empty_query(self):
        p = parse_query("tag:ML OR tag:DL")
        out = self.eng.search(parsed=p, mode="semantic")
        # 空检索词 + OR 标签 → 浏览两标签覆盖的全部文件
        self.assertEqual(set(self._paths(out)),
                         {"/docs/notes/alpha.md", "/docs/other/beta.md"})


class TestBuildCombine(unittest.TestCase):

    def test_build_and(self):
        p = build_tag_filter_parsed(["ML", "DL"], "AND")
        self.assertFalse(p.has_boolean)
        self.assertEqual(p.tag_filters, ["ML", "DL"])
        self.assertIsInstance(p.expr, AndNode)

    def test_build_or(self):
        p = build_tag_filter_parsed(["ML", "DL"], "OR")
        self.assertTrue(p.has_boolean)
        self.assertIsInstance(p.expr, OrNode)

    def test_build_empty(self):
        p = build_tag_filter_parsed([], "AND")
        self.assertEqual(p.clean_query, "")
        self.assertFalse(p.has_boolean)

    def test_combine_none_returns_text(self):
        tp = parse_query("深度学习")
        c = combine_parsed(tp, None)
        self.assertIs(c, tp)

    def test_combine_both_simple(self):
        tp = parse_query("深度学习")
        tagp = build_tag_filter_parsed(["ML"], "AND")
        c = combine_parsed(tp, tagp)
        self.assertFalse(c.has_boolean)
        self.assertEqual(c.tag_filters, ["ML"])

    def test_combine_with_boolean(self):
        tp = parse_query("深度学习")
        tagp = build_tag_filter_parsed(["ML", "DL"], "OR")
        c = combine_parsed(tp, tagp)
        self.assertTrue(c.has_boolean)
        self.assertIsInstance(c.expr, AndNode)


if __name__ == "__main__":
    unittest.main()
