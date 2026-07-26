"""
跨文件链接图谱单元测试（功能6 / P2-6）

覆盖：
- extract_links：[[wikilink]] / [[Name|alias]] / [[Name#sec]] / [[dir/Name]]、
  [t](./x.md) relpath、[t](/abs/x.md) abspath、跳过代码围栏与外部/图片链接；
- resolve_link 多源确定性优先级：同源精确 > 同源 basename > 跨源 basename；
  wikilink 按 title / stem 跨源匹配；无法解析记 None（dangling）；
- build：产出含 incoming 的 LinkGraph，悬挂节点 kind='missing'；
- 读写契约：build 不访问 tag_manager（不污染 tags.json，三权分立零冲突）。

运行：python -m unittest tests.test_link_graph -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.link_graph import (
    LinkGraphBuilder, ExtractedLink, LinkNode, LinkEdge, LinkGraph,
)
from core.multisource import Source


def _make_engine():
    """构造一个多源场景的假引擎。"""
    src_a = Source("C:/docsA")
    src_b = Source("C:/docsB")
    sources = [src_a, src_b]

    indexed = {
        "C:/docsA/notes.md": {"title": "Notes A", "file_name": "notes.md"},
        "C:/docsA/ref.md": {"title": "Ref", "file_name": "ref.md"},
        "C:/docsA/helper.md": {"title": "Helper A", "file_name": "helper.md"},
        "C:/docsA/sub/page.md": {"title": "Page", "file_name": "page.md"},
        "C:/docsB/shared.md": {"title": "Shared B", "file_name": "shared.md"},
        "C:/docsB/helper.md": {"title": "Helper B", "file_name": "helper.md"},
        "C:/docsB/other.md": {"title": "Other B", "file_name": "other.md"},
    }

    notes = (
        "# Notes A\n"
        "Title match: [[Ref]].\n"
        "Same-source title: [[Page]].\n"
        "Dangling: [[Missing]].\n"
        "Cross-source stem: [[Other]].\n"
        "Cross-source basename: [s](./shared.md).\n"
        "Same-source basename > cross: [h](../pkg/helper.md).\n"
        "Abs path: [a](/ref.md).\n"
    )
    contents = {
        "C:/docsA/notes.md": notes,
    }

    class FakeEngine:
        def __init__(self):
            self.sources = sources
            self._indexed = indexed
            self._contents = contents

        def get_indexed_files(self):
            return dict(self._indexed)

        def get_file_content(self, fp):
            return self._contents.get(fp, "")

    return FakeEngine(), src_a, src_b, indexed


class TestExtractLinks(unittest.TestCase):

    def _extract(self, text, fp="C:/x.md"):
        return LinkGraphBuilder().extract_links(text, fp)

    def test_wikilinks_variants(self):
        text = "See [[Note]] and [[Note|alias]] and [[Note#sec]]."
        links = self._extract(text)
        self.assertEqual(len(links), 3)
        for lnk in links:
            self.assertEqual(lnk.link_type, "wikilink")
            self.assertEqual(lnk.target_raw, "Note")
            self.assertIsNone(lnk.target_resolved)

    def test_wikilink_dir_name(self):
        links = self._extract("[[dir/Name]] here")
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].target_raw, "dir/Name")

    def test_relpath_and_abspath(self):
        text = "[rel](./a.md) and [abs](/b.md)"
        links = self._extract(text)
        types = {l.target_raw: l.link_type for l in links}
        self.assertEqual(types.get("./a.md"), "relpath")
        self.assertEqual(types.get("/b.md"), "abspath")

    def test_external_and_image_skipped(self):
        text = ("[ext](https://x.com) "
                "![img](./i.png) "
                "[ok](./real.md)")
        links = self._extract(text)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].target_raw, "./real.md")

    def test_code_fence_skipped(self):
        text = "```\n[[X]]\n```\n[[Y]]"
        links = self._extract(text)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].target_raw, "Y")

    def test_line_and_context_recorded(self):
        text = "line0\nline1 [[Target]] end\nline2"
        links = self._extract(text)
        self.assertEqual(links[0].line, 1)
        self.assertIn("[[Target]]", links[0].context)


class TestResolveLink(unittest.TestCase):

    def setUp(self):
        self.engine, _, _, _ = _make_engine()
        self.builder = LinkGraphBuilder()
        self.notes = "C:/docsA/notes.md"

    def _r(self, raw, ltype):
        return self.builder.resolve_link(self.notes, raw, self.engine, link_type=ltype)

    def test_wikilink_same_source_title(self):
        self.assertEqual(self._r("Ref", "wikilink"), "C:/docsA/ref.md")

    def test_wikilink_same_source_stem(self):
        self.assertEqual(self._r("Page", "wikilink"), "C:/docsA/sub/page.md")

    def test_wikilink_cross_source_stem(self):
        self.assertEqual(self._r("Other", "wikilink"), "C:/docsB/other.md")

    def test_relpath_cross_source_basename(self):
        # candidate A/shared.md 不存在 → 跨源 basename → B/shared.md
        self.assertEqual(self._r("./shared.md", "relpath"), "C:/docsB/shared.md")

    def test_relpath_same_source_basename_beats_cross(self):
        # candidate A/pkg/helper.md 不存在；A/helper.md（同源）优先于 B/helper.md（跨源）
        self.assertEqual(self._r("../pkg/helper.md", "relpath"), "C:/docsA/helper.md")

    def test_abspath_resolves_to_source_root_join(self):
        self.assertEqual(self._r("/ref.md", "abspath"), "C:/docsA/ref.md")

    def test_dangling_returns_none(self):
        self.assertIsNone(self._r("Missing", "wikilink"))


class TestBuildGraph(unittest.TestCase):

    def setUp(self):
        self.engine, _, _, self.indexed = _make_engine()

    def test_build_returns_graph(self):
        g = LinkGraphBuilder().build(self.engine)
        self.assertIsInstance(g, LinkGraph)
        # 7 个已索引 md 文件均成为文章节点
        for fp in self.indexed:
            self.assertIn(fp, g.nodes)
            self.assertEqual(g.nodes[fp].kind, "article")

    def test_edges_and_incoming(self):
        g = LinkGraphBuilder().build(self.engine)
        # 7 条引用边（[[Ref]]x2 + 其余各 1）
        self.assertEqual(len(g.edges), 7)
        # dangling 节点
        self.assertIn("Missing", g.nodes)
        self.assertEqual(g.nodes["Missing"].kind, "missing")
        self.assertIn("Missing", g.incoming)
        self.assertEqual(g.incoming["Missing"], ["C:/docsA/notes.md"])

    def test_no_tag_manager_access(self):
        # 只读分析：build 不应触碰 tag_manager / tags.json（三权分立）
        self.assertFalse(hasattr(self.engine, "tag_manager"),
                         "FakeEngine 不应有 tag_manager；"
                         "若此处断言失败说明 build 误访问了标签系统")
        g = LinkGraphBuilder().build(self.engine)
        self.assertIsInstance(g, LinkGraph)


if __name__ == "__main__":
    unittest.main()
