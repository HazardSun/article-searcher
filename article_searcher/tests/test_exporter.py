"""
导出功能单元测试（纯函数，无需 GUI / 模型 / 向量库）

运行: python tests/test_exporter.py
覆盖：MD/CSV 内容正确性、空结果不报错、CSV UTF-8 BOM、字段取值、流式写。
"""

import os
import sys
import csv
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.exporter import export_markdown, export_csv, DEFAULT_FIELDS


def _sample_results():
    return [
        {
            "metadata": {
                "file_path": "/docs/机器学习.md",
                "file_name": "机器学习.md",
                "title": "机器学习",
            },
            "content": "深度学习是机器学习的重要分支。",
            "snippet": "深度学习是机器学习的重要分支。",
            "similarity": 0.9123,
            "rrf_score": 0.0512,
            "lexical_score": 3.4,
        },
        {
            "metadata": {
                "file_path": "/docs/烹饪.md",
                "file_name": "烹饪.md",
                "title": "烹饪",
            },
            "content": "今天教大家做苹果派。",
            "snippet": "今天教大家做苹果派。",
            "similarity": 0.55,
            "rrf_score": None,
            "lexical_score": 1.1,
        },
    ]


class TestExporterMarkdown(unittest.TestCase):

    def test_markdown_content(self):
        results = _sample_results()
        text = export_markdown(results)
        self.assertIn("# 搜索结果导出", text)
        self.assertIn("机器学习.md", text)
        self.assertIn("烹饪.md", text)
        # score 取 rrf_score 优先，第二个无 rrf 取 similarity
        self.assertIn("0.0512", text)
        self.assertIn("0.55", text)

    def test_markdown_to_file(self):
        results = _sample_results()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "out.md")
            export_markdown(results, path=path)
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("机器学习.md", content)
            self.assertTrue(content.startswith("# 搜索结果导出"))

    def test_markdown_empty(self):
        text = export_markdown([])
        self.assertIn("共 0 条结果", text)
        # 空结果也应生成合法表头
        self.assertIn("| 文件路径 |", text)

    def test_markdown_custom_fields(self):
        results = _sample_results()
        text = export_markdown(results, fields=["filename", "snippet"])
        self.assertIn("文件名", text)
        self.assertIn("片段", text)
        self.assertNotIn("评分", text)


class TestExporterCsv(unittest.TestCase):

    def test_csv_content(self):
        results = _sample_results()
        text = export_csv(results)
        self.assertIsInstance(text, str)
        lines = text.strip().splitlines()
        # 表头 + 2 行
        self.assertEqual(len(lines), 3)
        self.assertIn("文件路径", lines[0])
        self.assertIn("文件名", lines[0])
        self.assertIn("片段", lines[0])
        self.assertIn("评分", lines[0])
        # 第二行含文件名与评分
        self.assertIn("机器学习.md", lines[1])
        self.assertIn("0.0512", lines[1])

    def test_csv_to_file_bom(self):
        results = _sample_results()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "out.csv")
            export_csv(results, path=path)
            # UTF-8 BOM 校验
            with open(path, "rb") as f:
                raw = f.read()
            self.assertEqual(raw[:3], b"\xef\xbb\xbf")  # UTF-8 BOM
            # 用 csv 读回
            with open(path, "r", encoding="utf-8-sig", newline="") as f:
                rows = list(csv.reader(f))
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[1][1], "机器学习.md")

    def test_csv_optional_fields(self):
        results = _sample_results()
        text = export_csv(results, fields=["file_path", "semantic_score", "lexical_score"])
        lines = text.strip().splitlines()
        self.assertIn("语义分", lines[0])
        self.assertIn("词法分", lines[0])
        # 语义分 = similarity 四舍五入；词法分 = lexical_score
        self.assertIn("0.9123", lines[1])
        self.assertIn("3.4", lines[1])
        self.assertIn("1.1", lines[2])

    def test_csv_empty(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "empty.csv")
            # 空结果导出不应抛错，仅写表头
            export_csv([], path=path)
            with open(path, "rb") as f:
                raw = f.read()
            self.assertEqual(raw[:3], b"\xef\xbb\xbf")
            with open(path, "r", encoding="utf-8-sig", newline="") as f:
                rows = list(csv.reader(f))
            self.assertEqual(len(rows), 1)  # 仅表头


class TestExporterFields(unittest.TestCase):

    def test_default_fields(self):
        self.assertEqual(DEFAULT_FIELDS, ["file_path", "filename", "snippet", "score"])

    def test_score_prefers_rrf(self):
        results = _sample_results()
        text = export_csv(results, fields=["score"])
        lines = text.strip().splitlines()
        # 第一个 rrf=0.0512，第二个 similarity=0.55
        self.assertIn("0.0512", lines[1])
        self.assertIn("0.55", lines[2])


if __name__ == "__main__":
    unittest.main()
