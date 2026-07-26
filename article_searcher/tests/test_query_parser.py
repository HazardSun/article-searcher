"""
高级搜索语法解析器单元测试（纯函数，无需 GUI / 模型 / 向量库）

运行: python tests/test_query_parser.py
覆盖：tag: / "短语" / path: / -排除词 / 未闭合引号降级 / 组合。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.query_parser import parse_query, ParsedQuery


class TestQueryParser(unittest.TestCase):

    def test_plain(self):
        p = parse_query("深度学习 神经网络")
        self.assertTrue(p.is_valid)
        self.assertEqual(p.clean_query, "深度学习 神经网络")
        self.assertEqual(p.tag_filters, [])
        self.assertEqual(p.path_filters, [])
        self.assertEqual(p.exclude_terms, [])
        self.assertEqual(p.phrase, "")

    def test_tag(self):
        p = parse_query("tag:技术 深度学习")
        self.assertTrue(p.is_valid)
        self.assertIn("技术", p.tag_filters)
        self.assertEqual(p.clean_query, "深度学习")

    def test_multiple_tags(self):
        p = parse_query("tag:A tag:B 内容")
        self.assertEqual(p.tag_filters, ["A", "B"])
        self.assertEqual(p.clean_query, "内容")

    def test_phrase(self):
        p = parse_query('"深度学习" 简介')
        self.assertTrue(p.is_valid)
        self.assertEqual(p.phrase, "深度学习")
        self.assertEqual(p.clean_query, "深度学习 简介")

    def test_path(self):
        p = parse_query("path:笔记 内容")
        self.assertTrue(p.is_valid)
        self.assertEqual(p.path_filters, ["笔记"])
        self.assertEqual(p.clean_query, "内容")

    def test_path_glob(self):
        p = parse_query("path:*.md 内容")
        self.assertTrue(p.is_valid)
        self.assertEqual(p.path_filters, ["*.md"])

    def test_exclude(self):
        p = parse_query("深度学习 -广告")
        self.assertTrue(p.is_valid)
        self.assertEqual(p.exclude_terms, ["广告"])
        self.assertEqual(p.clean_query, "深度学习")

    def test_exclude_phrase(self):
        p = parse_query('深度学习 -"垃圾内容"')
        self.assertTrue(p.is_valid)
        self.assertEqual(p.exclude_terms, ["垃圾内容"])
        self.assertEqual(p.clean_query, "深度学习")

    def test_combined(self):
        p = parse_query('tag:技术 "深度学习" path:笔记 -广告 模型')
        self.assertTrue(p.is_valid)
        self.assertEqual(p.tag_filters, ["技术"])
        self.assertEqual(p.phrase, "深度学习")
        self.assertEqual(p.path_filters, ["笔记"])
        self.assertEqual(p.exclude_terms, ["广告"])
        self.assertEqual(p.clean_query, "深度学习 模型")

    def test_unclosed_quote_degrades(self):
        p = parse_query('tag:技术 "未闭合 深度学习')
        self.assertFalse(p.is_valid)
        self.assertTrue(p.warn)
        # 降级：clean_query 回退为原文（普通搜索），不丢词
        self.assertEqual(p.clean_query, 'tag:技术 "未闭合 深度学习')

    def test_unclosed_exclude_quote_degrades(self):
        p = parse_query('深度学习 -"未闭合')
        self.assertFalse(p.is_valid)
        self.assertTrue(p.warn)
        self.assertEqual(p.clean_query, '深度学习 -"未闭合')

    def test_empty(self):
        p = parse_query("")
        self.assertTrue(p.is_valid)
        self.assertEqual(p.clean_query, "")

    def test_excludes_removed_from_clean(self):
        # 排除词 / tag / path / 短语 都不应残留在 clean_query
        p = parse_query('tag:A path:b -x "短语" 保留词')
        self.assertEqual(p.clean_query, "短语 保留词")

    def test_dataclass_defaults(self):
        p = ParsedQuery(clean_query="x")
        self.assertEqual(p.tag_filters, [])
        self.assertEqual(p.path_filters, [])
        self.assertEqual(p.exclude_terms, [])
        self.assertTrue(p.is_valid)
        self.assertEqual(p.warn, "")

    def test_multiple_consecutive_excludes(self):
        # 连续多个 -排除 都应被识别
        p = parse_query("深度学习 -广告 -垃圾 -推广")
        self.assertEqual(p.exclude_terms, ["广告", "垃圾", "推广"])
        self.assertEqual(p.clean_query, "深度学习")

    def test_empty_tag_value_ignored(self):
        # tag: 后无值（紧跟空格）不应产生空 tag
        p = parse_query("tag: 内容 path: 文件")
        self.assertEqual(p.tag_filters, [])
        self.assertEqual(p.path_filters, [])
        self.assertEqual(p.clean_query, "内容 文件")

    def test_path_glob_question_mark(self):
        # path 通配 ? 应被 fnmatch 识别
        p = parse_query("path:a?c 内容")
        self.assertEqual(p.path_filters, ["a?c"])

    def test_exclude_phrase_and_word_together(self):
        p = parse_query('深度 -"垃圾内容" -广告')
        self.assertEqual(p.exclude_terms, ["垃圾内容", "广告"])
        self.assertEqual(p.clean_query, "深度")

    def test_phrase_highlight_not_excluded(self):
        # 短语进入 clean_query 参与检索，但不进入 exclude
        p = parse_query('"深度学习" -广告')
        self.assertEqual(p.phrase, "深度学习")
        self.assertEqual(p.exclude_terms, ["广告"])
        self.assertEqual(p.clean_query, "深度学习")


if __name__ == "__main__":
    unittest.main()
