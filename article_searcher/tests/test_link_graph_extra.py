"""
P2-6 / P2-12 补充测试（增强覆盖率，非放宽断言）

弥补 test_link_graph.py 的盲区：
原 fixture 中同 basename 仅在 A、B 各出现一次，且 A 字典序先于 B，
导致「同源优先于跨源」的断言结果与「跨源回退」的结果相同，无法真正区分优先级。

本套件构造「同一 basename / 同一 title 同时存在于两源」的场景，
断言【必须落到同源文件】，从而真正验证设计 §3.1 的确定性优先级：
  同源 > 跨源（relpath：同源精确 > 同源 basename > 跨源 basename；
             wikilink：同源 title > 同源 stem > 跨源 title > 跨源 stem）。

另含：
- dedup 阈值边界（sim == threshold 应被包含，因实现用 >=）；
- dedup max_files 软上限截断（n > max_files 只取前 N 篇）；
- 悬挂节点在 build 中的 kind='missing' 与 incoming 收集。

运行：python -m unittest tests.test_link_graph_extra -v
（纯逻辑，无 GUI，offscreen 亦可运行）
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from core.link_graph import LinkGraphBuilder
from core.multisource import Source
from core.dedup import find_duplicate_pairs, DuplicatePair


def _absp(p):
    return os.path.abspath(p)


# ---------------------------------------------------------------------------
# P2-6 多源确定性优先级（真正可区分同/跨源）
# ---------------------------------------------------------------------------
def _make_conflict_engine():
    """helper.md / title=Helper 同时存在于 A 与 B；notes(A) 引用二者。

    若解析正确（同源优先），notes(A) 的引用应落到 A/helper.md，而非 B/helper.md。
    """
    src_a = Source("C:/docsA")
    src_b = Source("C:/docsB")
    sources = [src_a, src_b]

    indexed = {
        "C:/docsA/notes.md": {"title": "Notes A", "file_name": "notes.md"},
        "C:/docsA/helper.md": {"title": "Helper A", "file_name": "helper.md"},
        "C:/docsB/helper.md": {"title": "Helper B", "file_name": "helper.md"},
        "C:/docsB/other.md": {"title": "Other B", "file_name": "other.md"},
    }

    notes = (
        "# Notes A\n"
        "Same-source basename beats cross: [h](../pkg/helper.md).\n"
        "Same-source title beats cross: [[Helper]].\n"
        "Cross-only stem (only in B): [[Other]].\n"
        "Dangling: [[Ghost]].\n"
    )
    contents = {"C:/docsA/notes.md": notes}

    class FakeEngine:
        def __init__(self):
            self.sources = sources
            self._indexed = indexed
            self._contents = contents

        def get_indexed_files(self):
            return dict(self._indexed)

        def get_file_content(self, fp):
            return self._contents.get(fp, "")

    return FakeEngine(), "C:/docsA/notes.md"


class TestMultiSourcePriority(unittest.TestCase):

    def setUp(self):
        self.engine, self.notes = _make_conflict_engine()
        self.builder = LinkGraphBuilder()
        self.a_helper = _absp("C:/docsA/helper.md")
        self.b_helper = _absp("C:/docsB/helper.md")

    def _r(self, raw, ltype):
        return self.builder.resolve_link(self.notes, raw, self.engine, link_type=ltype)

    def test_relpath_same_source_basename_beats_cross(self):
        got = self._r("../pkg/helper.md", "relpath")
        self.assertEqual(_absp(got), self.a_helper,
                         "同源 basename 应优先于跨源 basename")
        self.assertNotEqual(_absp(got), self.b_helper,
                            "不应回退到跨源 helper.md")

    def test_wikilink_same_source_title_beats_cross(self):
        got = self._r("Helper", "wikilink")
        self.assertEqual(_absp(got), self.a_helper,
                         "同源 title 应优先于跨源 title")
        self.assertNotEqual(_absp(got), self.b_helper,
                            "不应回退到跨源 Helper B")

    def test_wikilink_cross_only_when_absent_in_source(self):
        # Other 仅存在于 B（跨源），应正确落到 B/other.md
        got = self._r("Other", "wikilink")
        self.assertEqual(_absp(got), _absp("C:/docsB/other.md"))

    def test_dangling_returns_none_and_not_cross_source(self):
        self.assertIsNone(self._r("Ghost", "wikilink"))


class TestBuildGraphSupplements(unittest.TestCase):

    def setUp(self):
        self.engine, self.notes = _make_conflict_engine()
        self.a_helper = _absp("C:/docsA/helper.md")
        self.b_helper = _absp("C:/docsB/helper.md")

    def test_build_resolves_same_source_helper(self):
        g = LinkGraphBuilder().build(self.engine)
        # notes 的出边应指向 A/helper.md（同源），而非 B/helper.md
        targets = [e.target for e in g.edges
                   if _absp(e.source) == _absp("C:/docsA/notes.md")]
        self.assertIn(self.a_helper, [_absp(t) for t in targets])
        self.assertNotIn(self.b_helper, [_absp(t) for t in targets])

    def test_build_dangling_node_kind_and_incoming(self):
        g = LinkGraphBuilder().build(self.engine)
        self.assertIn("Ghost", g.nodes)
        self.assertEqual(g.nodes["Ghost"].kind, "missing")
        self.assertIn("Ghost", g.incoming)
        self.assertEqual([_absp(x) for x in g.incoming["Ghost"]],
                         [_absp("C:/docsA/notes.md")])


# ---------------------------------------------------------------------------
# P2-12 阈值边界 + max_files 截断
# ---------------------------------------------------------------------------
class _FakeVS:
    def __init__(self, vectors):
        self._v = vectors

    def get_file_vector(self, fp):
        return self._v.get(fp)


class _FakeEng:
    def __init__(self, vectors):
        self.vector_store = _FakeVS(vectors)

    def get_indexed_files(self):
        return {fp: {"title": fp} for fp in self.vector_store._v}


def _unit(v):
    a = np.array(v, dtype=float)
    n = np.linalg.norm(a)
    return a / n if n else a


class TestDedupBoundary(unittest.TestCase):

    def test_threshold_boundary_inclusive(self):
        # 构造一对 sim 恰为 0.85 的向量（归一化后内积=0.85）
        v1 = _unit([1.0, 0.0, 0.0])
        v2 = _unit([0.85, 0.5267826876416365, 0.0])  # 0.85^2 + 0.52678^2 ≈ 1
        sim = float(np.dot(v1, v2))
        self.assertAlmostEqual(sim, 0.85, places=4)
        eng = _FakeEng({"A/a.md": v1, "A/b.md": v2})
        # 阈值=0.85，sim>=threshold -> 应包含
        pairs = find_duplicate_pairs(eng, threshold=0.85)
        self.assertEqual(len(pairs), 1)
        # 阈值=0.86，sim<threshold -> 应排除
        pairs2 = find_duplicate_pairs(eng, threshold=0.86)
        self.assertEqual(pairs2, [])

    def test_max_files_truncation(self):
        # 3 篇，其中 0/1 完全相同，2 也与 0 相同；max_files=2 只取前 2 篇
        v = _unit([1.0, 0.0, 0.0])
        eng = _FakeEng({
            "A/0.md": v, "A/1.md": v, "A/2.md": v,
        })
        pairs = find_duplicate_pairs(eng, threshold=0.99, max_files=2)
        # 仅前 2 篇 (0,1) 参与 -> 1 对；文件 2 被截断，不产生 (0,2)/(1,2)
        self.assertEqual(len(pairs), 1)
        involved = set()
        for p in pairs:
            involved.add(p.file_a)
            involved.add(p.file_b)
        self.assertNotIn("A/2.md", involved)

    def test_returns_descending_duplicate_pairs(self):
        v1 = _unit([1.0, 0.0, 0.0])
        v2 = _unit([1.0, 0.0, 0.0])            # sim 1.0
        v3 = _unit([0.0, 1.0, 0.0])           # 正交
        v4 = _unit([0.9, 0.4358898943540673, 0.0])  # 与 v1 sim ≈0.9
        eng = _FakeEng({
            "A/1.md": v1, "A/2.md": v2, "B/3.md": v3, "A/4.md": v4,
        })
        pairs = find_duplicate_pairs(eng, threshold=0.85)
        sims = [p.similarity for p in pairs]
        self.assertEqual(sims, sorted(sims, reverse=True))
        # (1,2)=1.0 必须排在 (1,4)≈0.9 之前
        self.assertAlmostEqual(pairs[0].similarity, 1.0, places=4)


if __name__ == "__main__":
    unittest.main()
