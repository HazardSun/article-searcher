"""
近似 / 重复文章检测单元测试（功能12 / P2-12）

覆盖（注入已知相似向量，验证契约）：
- 返回 sim ≥ threshold 的无重复 (i<j) 对，按相似度降序；
- 阈值过滤（0.80 含 0.82 近重复，0.85 排除之）；
- 全库两两 vs query_similar（单文件 top-k）：本函数为全局去重；
- 不调 encode（向量来自 get_file_vector，零新依赖）；
- 不污染 tags.json（不访问 tag_manager）；
- 空向量 / 不足两篇 → 返回 []。

运行：python -m unittest tests.test_dedup -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from core.dedup import find_duplicate_pairs, DuplicatePair


def _norm(v):
    a = np.array(v, dtype=float)
    n = np.linalg.norm(a)
    return a / n if n else a


class _FakeVectorStore:
    def __init__(self, vectors):
        self._v = vectors

    def get_file_vector(self, fp):
        return self._v.get(fp)


class _FakeEmbedding:
    """若去重误调 encode，立即暴露（应永不触发）。"""
    def encode(self, *a, **k):
        raise AssertionError("dedup 不应调用 encode（必须复用 get_file_vector）")


class _FakeEngine:
    def __init__(self, vectors):
        self.vector_store = _FakeVectorStore(vectors)
        self.embedding_engine = _FakeEmbedding()

    def get_indexed_files(self):
        return {fp: {"title": fp} for fp in self.vector_store._v}


def _build_vectors():
    """构造已知相似度的向量集合（4 维，正交分离，避免交叉相似）。

    关系：
      f1 ≡ f2                → sim 1.0（完全重复）
      f3 ⊥ f1/f2/f4/f5；f5 仅在 y 轴近 f3 → (f3,f5)≈0.95
      f4 仅在 x 轴近 f1 → (f1,f4)≈0.82（近重复，跨过 0.80/0.85 边界）
      其余两两正交 → sim 0
    """
    f1 = _norm([1.0, 0.0, 0.0, 0.0])           # 基准
    f2 = _norm([1.0, 0.0, 0.0, 0.0])           # 与 f1 完全相同 → sim 1.0
    f3 = _norm([0.0, 1.0, 0.0, 0.0])           # y 轴
    f4 = np.array([0.82, 0.0, 0.5724, 0.0])      # x-z 平面，与 f1 sim ≈0.82
    f5 = np.array([0.0, 0.95, 0.0, 0.312])       # y-z 平面，与 f3 sim ≈0.95
    return {
        "A/f1.md": f1, "A/f2.md": f2, "B/f3.md": f3,
        "A/f4.md": f4, "B/f5.md": f5,
    }


class TestDuplicatePairs(unittest.TestCase):

    def setUp(self):
        self.engine = _FakeEngine(_build_vectors())

    def _pairs(self, threshold=0.85):
        return find_duplicate_pairs(self.engine, threshold=threshold)

    def test_returns_similar_pairs(self):
        pairs = self._pairs(0.85)
        # (f1,f2)=1.0、(f3,f5)=0.95 → 2 对；f4(0.82) 被阈值排除
        self.assertEqual(len(pairs), 2)
        # f4 相似度仅 0.82，不应出现在 0.85 阈值的任何对中
        for p in pairs:
            self.assertFalse(("f4.md" in p.file_a) or ("f4.md" in p.file_b))
        # 每对相似度均 ≥ 阈值
        for p in pairs:
            self.assertGreaterEqual(p.similarity, 0.85)

    def test_sorted_descending(self):
        pairs = self._pairs(0.85)
        sims = [p.similarity for p in pairs]
        self.assertEqual(sims, sorted(sims, reverse=True))

    def test_unique_i_lt_j_pairs(self):
        pairs = self._pairs(0.80)
        seen = set()
        for p in pairs:
            key = tuple(sorted((p.file_a, p.file_b)))
            self.assertNotIn(key, seen, "出现重复对")
            seen.add(key)

    def test_threshold_filtering(self):
        # 0.85 排除 f4 相关对（f1/f4≈0.82、f2/f4≈0.82）
        high = self._pairs(0.85)
        self.assertFalse(
            any(("f4.md" in p.file_a) or ("f4.md" in p.file_b) for p in high))
        # 0.80 纳入 f4 相关对（≈0.82）
        low = self._pairs(0.80)
        self.assertTrue(
            any(("f4.md" in p.file_a) or ("f4.md" in p.file_b) for p in low))

    def test_highest_pair_is_full_match(self):
        pairs = self._pairs(0.85)
        top = pairs[0]
        self.assertAlmostEqual(top.similarity, 1.0, places=4)
        self.assertEqual(set((top.file_a, top.file_b)),
                         {"A/f1.md", "A/f2.md"})

    def test_no_encode_called(self):
        # 构造中若误调 encode 会直接抛错；此处仅为显式契约说明
        self.assertTrue(hasattr(self.engine.embedding_engine, "encode"))
        pairs = self._pairs(0.85)
        self.assertIsInstance(pairs, list)

    def test_no_tag_manager_access(self):
        self.assertFalse(
            hasattr(self.engine, "tag_manager"),
            "dedup 不应访问 tag_manager / 写 tags.json（三权分立零冲突）")
        pairs = self._pairs(0.85)
        self.assertIsInstance(pairs, list)

    def test_empty_when_no_vectors(self):
        engine = _FakeEngine({})
        self.assertEqual(find_duplicate_pairs(engine), [])

    def test_empty_when_single_file(self):
        engine = _FakeEngine({"A/only.md": _norm([1.0, 0.0, 0.0])})
        self.assertEqual(find_duplicate_pairs(engine), [])

    def test_returns_duplicate_pair_dataclasses(self):
        pairs = self._pairs(0.85)
        for p in pairs:
            self.assertIsInstance(p, DuplicatePair)
            self.assertIsInstance(p.file_a, str)
            self.assertIsInstance(p.file_b, str)
            self.assertIsInstance(p.similarity, float)


if __name__ == "__main__":
    unittest.main()
