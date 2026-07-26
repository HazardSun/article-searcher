"""
相似文章推荐结构性单元测试（功能4）

由于 engine.query_similar 依赖 ChromaDB / Embedding（重依赖），本测试用 FakeEngine
验证「返回结构必须与 search 结果同构（架构设计 §3.1）」这一契约：
  - 返回 list[dict]，元素含 id / content / metadata / similarity / file_tags /
    snippet / matched_terms
  - similarity 降序
  - 排除自身（file_path 不等于查询文件）
  - file_tags / snippet / matched_terms 类型正确

运行: python tests/test_related.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 与 core/engine.py::query_similar 返回的字段保持一致（契约）
REQUIRED_KEYS = {
    "id", "content", "metadata", "distance", "similarity",
    "lexical_score", "rrf_score", "file_tags", "snippet", "matched_terms",
}


def _make_result(fp, sim, snippet="片段内容", tags=None, matched=None):
    return {
        "id": fp,
        "content": f"文档 {fp} 的内容",
        "metadata": {"file_path": fp, "file_name": os.path.basename(fp),
                     "title": os.path.basename(fp), "start_line": 0, "end_line": 1},
        "distance": 1 - sim,
        "similarity": sim,
        "lexical_score": 0.0,
        "rrf_score": None,
        "file_tags": tags if tags is not None else [],
        "snippet": snippet,
        "matched_terms": matched if matched is not None else [],
        "search_mode": "similar",
    }


class FakeEngine:
    """模拟 engine.query_similar，按契约（架构设计 §3.1 / §8⑤）返回结构：

    - 排除自身（file_path == 查询文件）
    - 按 similarity 降序
    - 截断到 top_k
    - 每条含 REQUIRED_KEYS 全部字段
    """

    def __init__(self, query_file, results):
        self._query_file = query_file
        self._results = results

    def query_similar(self, file_path, top_k=5):
        assert file_path == self._query_file
        out = [r for r in self._results if r["metadata"]["file_path"] != file_path]
        out.sort(key=lambda r: r["similarity"], reverse=True)
        return out[:top_k]


class TestRelatedStructure(unittest.TestCase):

    def _build(self, query_file):
        results = [
            _make_result("/docs/b.md", 0.91, tags=["AI"], matched=[]),
            _make_result("/docs/c.md", 0.80, tags=["AI", "ML"]),
            _make_result("/docs/d.md", 0.66),
            _make_result(query_file, 0.99),  # 自身，必须被排除
            _make_result("/docs/e.md", 0.50),
        ]
        engine = FakeEngine(query_file, results)
        return engine.query_similar(query_file, top_k=5)

    def test_returns_list_of_dicts(self):
        out = self._build("/docs/a.md")
        self.assertIsInstance(out, list)
        self.assertTrue(len(out) >= 3)  # 需求：≥3 条
        for r in out:
            self.assertIsInstance(r, dict)

    def test_required_keys_present(self):
        out = self._build("/docs/a.md")
        for r in out:
            missing = REQUIRED_KEYS - set(r.keys())
            self.assertEqual(missing, set(), f"缺失字段: {missing}")

    def test_excludes_self(self):
        out = self._build("/docs/a.md")
        self.assertTrue(all(r["metadata"]["file_path"] != "/docs/a.md" for r in out))

    def test_similarity_descending(self):
        out = self._build("/docs/a.md")
        sims = [r["similarity"] for r in out]
        self.assertEqual(sims, sorted(sims, reverse=True))

    def test_field_types(self):
        out = self._build("/docs/a.md")
        for r in out:
            self.assertIsInstance(r["file_tags"], list)
            self.assertIsInstance(r["snippet"], str)
            self.assertIsInstance(r["matched_terms"], list)
            self.assertIsInstance(r["similarity"], (int, float))

    def test_top_k_truncation(self):
        out = self._build("/docs/a.md")
        # 排除自身后共 4 个候选，top_k=3 应截断为 3
        engine = FakeEngine("/docs/a.md", [
            _make_result("/docs/b.md", 0.9),
            _make_result("/docs/c.md", 0.8),
            _make_result("/docs/d.md", 0.7),
            _make_result("/docs/e.md", 0.6),
        ])
        out3 = engine.query_similar("/docs/a.md", top_k=3)
        self.assertEqual(len(out3), 3)

    def test_empty_when_no_other_files(self):
        engine = FakeEngine("/docs/a.md", [
            _make_result("/docs/a.md", 0.99),  # 只有自身
        ])
        out = engine.query_similar("/docs/a.md", top_k=5)
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
