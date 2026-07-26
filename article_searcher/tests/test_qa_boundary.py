"""
独立 QA 边界用例（QA 严过关 / Yan 增量复核）

针对工程师 T01–T05 增量实现的边界强化测试，覆盖设计 docs/system_design.md
要求但工程师测试可能遗漏/薄弱的场景：

  - 嵌套括号 (A OR B) AND C 与 NOT (A OR B) 的 AST 与引擎求值
  - 优先级：OR 结合最松（A OR B AND C == A OR (B AND C)）、A AND B OR C == (A AND B) OR C
  - 布尔分支门控：`-tag:X` 排除全部文件 + 检索词时应为空集（空 allowed 集不可被当作“无约束”）
  - 设计 §8 契约：clean_query 仅拼接“非否定” TermNode，否定 term 不进入检索文本
  - 空标签筛选（build_tag_filter_parsed([]) / combine_parsed / UI clear 发射空列表）
  - 分组 BY_TAG / BY_SOURCE 空结果不崩溃；无 sources 时全部归“其他”
  - 帮助浮层 offscreen 可构造且含嵌套括号速查示例

运行: python tests/test_qa_boundary.py
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
from core.engine import ArticleSearchEngine  # noqa: E402
from core.query_parser import (  # noqa: E402
    parse_query, build_tag_filter_parsed, combine_parsed,
    TagNode, PathNode, TermNode, NotNode, AndNode, OrNode,
)


# --------------------------------------------------------------------------- #
# 引擎测试基件
# --------------------------------------------------------------------------- #
def _mk_chunk(fp, content, sim=0.9):
    return {
        "id": "id_" + os.path.basename(fp),
        "content": content,
        "metadata": {
            "file_path": fp,
            "file_name": os.path.basename(fp),
            "title": os.path.basename(fp),
            "start_line": 0,
            "end_line": 1,
            "chunk_index": 0,
            "total_chunks": 1,
        },
        "distance": 1 - sim,
        "similarity": sim,
        "lexical_score": 1.0,
    }


class _EngineHarness:
    """构造一个不依赖真实 embedding/BM25 的引擎，仅聚焦布尔逻辑。"""

    def __init__(self, tmp, files, tag_index, contents, sims=None):
        self.eng = ArticleSearchEngine(db_path=os.path.join(tmp, "chromadb"))
        self.files = list(files)
        meta = {
            fp: {
                "chunk_count": 1,
                "title": os.path.basename(fp),
                "file_name": os.path.basename(fp),
            }
            for fp in files
        }
        self.eng.vector_store._index_meta["files"] = meta
        self.eng.tag_manager._tag_index = {
            t: set(fs) for t, fs in tag_index.items()
        }
        self._chunks = {
            fp: _mk_chunk(fp, contents[fp], (sims or {}).get(fp, 0.9))
            for fp in files
        }

        def fake_vs(query_embedding=None, top_k=10, filter_metadata=None):
            res = [dict(self._chunks[fp]) for fp in self.files]
            if filter_metadata:
                fps = set(filter_metadata.get("file_path", []))
                res = [r for r in res if r["metadata"]["file_path"] in fps]
            return res[:top_k]

        def fake_lex(q, top_k=10):
            return [dict(self._chunks[fp]) for fp in self.files]

        def fake_get_chunks_by_files(file_paths):
            fps = set(file_paths)
            return [dict(self._chunks[fp]) for fp in self.files if fp in fps]

        self.eng.vector_store.search = fake_vs
        self.eng.lexical.search = fake_lex
        self.eng.embedding_engine.encode_single = lambda q: np.zeros(8)
        self.eng.vector_store.get_chunks_by_files = fake_get_chunks_by_files

    def paths(self, results):
        return [r["metadata"]["file_path"] for r in results]


# --------------------------------------------------------------------------- #
# 1) 括号 / 优先级 / NOT 组合
# --------------------------------------------------------------------------- #
class TestBooleanParenAndPrecedence(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_nested_paren_and(self):
        h = _EngineHarness(
            self.tmp,
            files=["/a.md", "/b.md", "/c.md"],
            tag_index={"A": {"/a.md"}, "B": {"/b.md"}, "C": {"/a.md", "/c.md"}},
            contents={"/a.md": "A", "/b.md": "B", "/c.md": "C"},
        )
        p = parse_query("(tag:A OR tag:B) AND tag:C")
        # AST 形态
        self.assertIsInstance(p.expr, AndNode)
        self.assertIsInstance(p.expr.children[0], OrNode)
        self.assertIsInstance(p.expr.children[1], TagNode)
        # 引擎：仅 /a.md 命中
        out = h.eng.search(parsed=p, mode="semantic")
        self.assertEqual(set(h.paths(out)), {"/a.md"})

    def test_not_paren_excludes_union(self):
        h = _EngineHarness(
            self.tmp,
            files=["/a.md", "/b.md", "/c.md"],
            tag_index={"A": {"/a.md"}, "B": {"/b.md"}},
            contents={"/a.md": "A", "/b.md": "B", "/c.md": "C"},
        )
        p = parse_query("NOT (tag:A OR tag:B)")
        self.assertIsInstance(p.expr, NotNode)
        self.assertIsInstance(p.expr.child, OrNode)
        out = h.eng.search(parsed=p, mode="semantic")
        # 空查询 + 文件级 NOT → 浏览补集 = /c.md
        self.assertEqual(set(h.paths(out)), {"/c.md"})

    def test_precedence_or_binds_loosest(self):
        # A OR B AND C 应等价于 A OR (B AND C)
        h = _EngineHarness(
            self.tmp,
            files=["/a.md", "/b.md", "/c.md"],
            tag_index={"A": {"/a.md"}, "B": {"/b.md", "/c.md"}, "C": {"/c.md"}},
            contents={"/a.md": "A", "/b.md": "B", "/c.md": "C"},
        )
        p = parse_query("tag:A OR tag:B AND tag:C")
        self.assertIsInstance(p.expr, OrNode)
        self.assertIsInstance(p.expr.children[1], AndNode)
        out = h.eng.search(parsed=p, mode="semantic")
        # (B AND C)={c}, A OR {c}={a,c}; b 不应命中
        self.assertEqual(set(h.paths(out)), {"/a.md", "/c.md"})
        self.assertNotIn("/b.md", h.paths(out))

    def test_precedence_and_or(self):
        # A AND B OR C 应等价于 (A AND B) OR C
        h = _EngineHarness(
            self.tmp,
            files=["/a.md", "/c.md"],
            tag_index={"A": {"/a.md"}, "B": {"/a.md"}, "C": {"/c.md"}},
            contents={"/a.md": "AB", "/c.md": "C"},
        )
        p = parse_query("tag:A AND tag:B OR tag:C")
        self.assertIsInstance(p.expr, OrNode)
        self.assertIsInstance(p.expr.children[0], AndNode)
        out = h.eng.search(parsed=p, mode="semantic")
        self.assertEqual(set(h.paths(out)), {"/a.md", "/c.md"})

    def test_text_or_with_paren_tag(self):
        h = _EngineHarness(
            self.tmp,
            files=["/a.md", "/b.md", "/c.md"],
            tag_index={"A": {"/a.md"}, "B": {"/b.md"}},
            contents={"/a.md": "苹果", "/b.md": "香蕉", "/c.md": "橙子"},
        )
        p = parse_query("苹果 OR (tag:A OR tag:B)")
        out = h.eng.search(parsed=p, mode="semantic")
        # 内容含“苹果”的 /a.md + tag A/B 覆盖的 /a.md,/b.md
        self.assertEqual(set(h.paths(out)), {"/a.md", "/b.md"})


# --------------------------------------------------------------------------- #
# 2) 布尔分支门控关键边界：空 allowed 集必须产出空结果
# --------------------------------------------------------------------------- #
class TestBooleanEmptyAllowed(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_negated_tag_excluding_all_with_query_returns_empty(self):
        # 所有文件都带 ALL；`-tag:ALL` 应排除全部 → 即使有检索词，结果也应为空。
        h = _EngineHarness(
            self.tmp,
            files=["/a.md", "/b.md"],
            tag_index={"ALL": {"/a.md", "/b.md"}},
            contents={"/a.md": "深度学习话题", "/b.md": "神经网络讨论"},
        )
        p = parse_query("深度学习 -tag:ALL")
        self.assertTrue(p.has_boolean)
        out = h.eng.search(parsed=p, mode="semantic")
        # 期望空（正确语义：-tag:ALL 排除全部文件）。
        # 若实现把空 allowed 集当“无文件约束”处理，会把 /a.md 也返回 —— 此为 Bug。
        self.assertEqual(out, [])

    def test_negated_tag_partial_exclusion_still_works(self):
        # 部分排除：KEEP 仅在 /a.md；`-tag:KEEP` 保留 /b.md。
        h = _EngineHarness(
            self.tmp,
            files=["/a.md", "/b.md"],
            tag_index={"KEEP": {"/a.md"}},
            contents={"/a.md": "深度学习", "/b.md": "深度学习"},
        )
        p = parse_query("深度学习 -tag:KEEP")
        out = h.eng.search(parsed=p, mode="semantic")
        self.assertEqual(set(h.paths(out)), {"/b.md"})
        self.assertNotIn("/a.md", h.paths(out))

    def test_negated_path_all_excluded_with_query(self):
        h = _EngineHarness(
            self.tmp,
            files=["/docs/a.md", "/docs/b.md"],
            tag_index={},
            contents={"/docs/a.md": "深度学习", "/docs/b.md": "深度学习"},
        )
        p = parse_query("深度学习 -path:/docs")
        self.assertTrue(p.has_boolean)
        out = h.eng.search(parsed=p, mode="semantic")
        # 全部 /docs 路径被 -path:/docs 排除 → 空集
        self.assertEqual(out, [])

    def test_empty_intersection_with_query_returns_empty(self):
        # 隔离验证“空 allowed 集”缺陷（不含 NOT/-tag 干扰）：
        # (tag:A AND tag:B) 交集为空，却带检索词 → 应为空集。
        # 当前实现把空 allowed 集当作“无文件约束”→ 返回含检索词的文件（Bug #1）。
        h = _EngineHarness(
            self.tmp,
            files=["/a.md", "/b.md"],
            tag_index={"A": {"/a.md"}, "B": {"/b.md"}},
            contents={"/a.md": "深度学习相关", "/b.md": "其他内容"},
        )
        p = parse_query("深度学习 (tag:A AND tag:B)")
        self.assertTrue(p.has_boolean)
        out = h.eng.search(parsed=p, mode="semantic")
        # A∩B=∅ → 没有任何文件同时满足 → 空集（即便 a.md 含“深度学习”）
        self.assertEqual(out, [])


# --------------------------------------------------------------------------- #
# 3) 设计 §8 契约：clean_query 仅含非否定 TermNode
# --------------------------------------------------------------------------- #
class TestCleanQueryContract(unittest.TestCase):

    def test_negated_term_excluded_from_clean_query(self):
        # 设计 §8：clean_query 取“所有非否定 TermNode”文本拼接；否定 term 仅作后过滤。
        p = parse_query("深度学习 NOT 广告")
        self.assertEqual(p.clean_query, "深度学习")

    def test_negated_phrase_excluded_from_clean_query(self):
        p = parse_query('机器学习 NOT "垃圾广告"')
        self.assertEqual(p.clean_query, "机器学习")

    def test_plain_and_or_keeps_terms(self):
        p = parse_query('"深度学习" OR "神经网络"')
        self.assertIn("深度学习", p.clean_query)
        self.assertIn("神经网络", p.clean_query)


# --------------------------------------------------------------------------- #
# 4) 空标签筛选（build / combine / UI）
# --------------------------------------------------------------------------- #
class TestEmptyTagFilter(unittest.TestCase):

    def test_build_empty_returns_no_boolean(self):
        p = build_tag_filter_parsed([], "AND")
        self.assertFalse(p.has_boolean)
        self.assertEqual(p.clean_query, "")
        self.assertEqual(p.tag_filters, [])

    def test_combine_with_empty_tag_keeps_text_simple(self):
        tp = parse_query("深度学习")
        empty = build_tag_filter_parsed([], "AND")
        c = combine_parsed(tp, empty)
        self.assertFalse(c.has_boolean)
        self.assertEqual(c.tag_filters, [])
        self.assertEqual(c.clean_query, "深度学习")

    def test_combine_none_and_empty_tag_equivalent(self):
        tp = parse_query("深度学习")
        c1 = combine_parsed(tp, None)
        c2 = combine_parsed(tp, build_tag_filter_parsed([], "OR"))
        self.assertEqual(c1.clean_query, c2.clean_query)
        self.assertEqual(c2.has_boolean, c1.has_boolean)


class TestEmptyTagFilterUi(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_clear_selection_emits_empty_list(self):
        from ui.tag_filter import TagFilterWidget
        w = TagFilterWidget()
        w.update_tags({"ML": 2, "DL": 3})
        w._on_tag_clicked("ML", True)
        received = []
        w.tags_selected.connect(lambda tags, op: received.append((tags, op)))
        w.clear_selection()
        self.assertEqual(w.selected_tags(), [])
        self.assertIn(([], "AND"), received)


# --------------------------------------------------------------------------- #
# 5) 分组空结果 / 无源
# --------------------------------------------------------------------------- #
class TestGroupingEmpty(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def _rl(self):
        from ui.search_result_list import SearchResultList
        return SearchResultList()

    def test_empty_by_tag_no_crash(self):
        from ui.search_result_list import GroupMode
        rl = self._rl()
        rl.display_results([], group_mode=GroupMode.BY_TAG)
        # 空结果统一显示“未找到匹配结果”占位项（1 条）
        self.assertEqual(rl.list_widget.count(), 1)

    def test_empty_by_source_no_crash(self):
        from ui.search_result_list import GroupMode
        rl = self._rl()
        rl.display_results([], group_mode=GroupMode.BY_SOURCE)
        self.assertEqual(rl.list_widget.count(), 1)

    def test_by_source_all_other_when_no_sources(self):
        from ui.search_result_list import GroupMode
        rl = self._rl()
        rl.set_sources([])
        results = [
            {"content": "x", "metadata": {"file_path": "/a/d.md",
             "file_name": "d.md", "title": "d.md"},
             "similarity": 0.9, "matched_terms": [], "snippet": "x",
             "file_tags": [], "search_mode": "semantic"},
        ]
        rl.display_results(results, group_mode=GroupMode.BY_SOURCE)
        # 无 sources → 全部归“其他”（1 组）
        self.assertEqual(rl.list_widget.count(), 1)


# --------------------------------------------------------------------------- #
# 6) 帮助浮层 offscreen 可构造 + 含嵌套括号速查
# --------------------------------------------------------------------------- #
class TestHelpOverlayBoundary(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_cheatsheet_has_nested_paren_example(self):
        from ui.help_overlay import HelpOverlay
        sheet = HelpOverlay.SYNTAX_CHEATSHEET
        self.assertIn("(tag:A OR tag:B) NOT tag:C", sheet)

    def test_dialog_has_positive_min_size(self):
        from ui.help_overlay import HelpOverlay
        dlg = HelpOverlay()
        self.assertGreaterEqual(dlg.minimumWidth(), 400)
        self.assertGreaterEqual(dlg.minimumHeight(), 300)


if __name__ == "__main__":
    unittest.main(verbosity=2)
