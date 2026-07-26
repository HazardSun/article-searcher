"""
P1 功能 5 语义自动聚簇：KMeans 行为 + 与 file_tags 物理隔离 测试。

- estimate_k 启发式（<3 返回 0，封顶 12）；
- _kmeans 返回合法标签（数量 = n，簇数 = k）；
- cluster_files：<3 文件返回空；>=3 返回簇，且不污染 file_tags；
- TagManager.set_clusters / dismiss_cluster 仅操作 clusters 独立字段。
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
from core.clustering import cluster_files, estimate_k, _kmeans, Cluster
from core.tagger import TagManager


DIM = 8


class FakeVectorStore:
    def __init__(self, files):
        # files: dict path -> meta
        self._files = files
        self._vecs = {
            fp: np.random.RandomState(i + 1).rand(DIM).astype("float32")
            for i, fp in enumerate(files)
        }

    def get_indexed_files(self):
        return self._files

    def get_file_vector(self, fp):
        return self._vecs.get(fp)

    def get_embedding_dim(self):
        return DIM


def make_engine_like(files, tag_manager):
    class _E:
        pass
    e = _E()
    e.vector_store = FakeVectorStore(files)
    e.tag_manager = tag_manager
    return e


class TestEstimateK(unittest.TestCase):
    def test_less_than_three(self):
        self.assertEqual(estimate_k(0), 0)
        self.assertEqual(estimate_k(1), 0)
        self.assertEqual(estimate_k(2), 0)

    def test_floor_and_cap(self):
        self.assertEqual(estimate_k(3), 2)
        self.assertEqual(estimate_k(5), 2)
        self.assertEqual(estimate_k(50), 5)
        # 封顶 12
        big = estimate_k(1000)
        self.assertLessEqual(big, 12)
        self.assertGreaterEqual(big, 2)


class TestKMeans(unittest.TestCase):
    def test_labels_shape(self):
        rng = np.random.RandomState(1)
        X = rng.rand(20, DIM)
        labels, centers = _kmeans(X, 3, seed=0)
        self.assertEqual(len(labels), 20)
        self.assertEqual(centers.shape, (3, DIM))
        self.assertLessEqual(len(set(labels.tolist())), 3)

    def test_deterministic_with_seed(self):
        rng = np.random.RandomState(2)
        X = rng.rand(15, DIM)
        l1, _ = _kmeans(X, 4, seed=7)
        l2, _ = _kmeans(X, 4, seed=7)
        self.assertEqual(l1.tolist(), l2.tolist())


class TestClusterFiles(unittest.TestCase):
    def _files(self, n):
        return {
            f"/docs/f{i}.md": {"title": f"文件{i}", "file_name": f"f{i}.md"}
            for i in range(n)
        }

    def test_fewer_than_three_returns_empty(self):
        tm = TagManager(save_path=os.path.join(tempfile.mkdtemp(), "tags.json"))
        eng = make_engine_like(self._files(2), tm)
        self.assertEqual(cluster_files(eng), [])

    def test_clusters_returned_and_isolated(self):
        tm = TagManager(save_path=os.path.join(tempfile.mkdtemp(), "tags.json"))
        files = self._files(6)
        eng = make_engine_like(files, tm)
        # 预置手动标签（不应被聚簇污染）
        tm.set_tags_for_file("/docs/f0.md", ["机器学习"])
        tm.set_tags_for_file("/docs/f1.md", ["深度学习"])
        before = tm.get_tags_for_file("/docs/f0.md")

        clusters = cluster_files(eng, seed=0)
        self.assertTrue(len(clusters) >= 1)
        for c in clusters:
            self.assertIsInstance(c, Cluster)
            self.assertIsInstance(c.label, str)
            self.assertTrue(len(c.files) > 0)

        # 隔离验证：file_tags 不变
        self.assertEqual(tm.get_tags_for_file("/docs/f0.md"), before)
        # clusters 独立字段未被污染进 file_tags
        self.assertNotIn("主题簇", tm.get_all_tags())

    def test_empty_vectors_returns_empty(self):
        files = {"/docs/x.md": {"title": "x", "file_name": "x.md"}}
        tm = TagManager(save_path=os.path.join(tempfile.mkdtemp(), "tags.json"))
        # 向量缺失 → 全部跳过 → <3 → 空
        class EmptyVS(FakeVectorStore):
            def get_file_vector(self, fp):
                return None
        class _E:
            pass
        e = _E()
        e.vector_store = EmptyVS(files)
        e.tag_manager = tm
        self.assertEqual(cluster_files(e), [])


class TestClusterIntegration(unittest.TestCase):
    def test_cluster_files_feed_set_clusters(self):
        """集成回归：cluster_files 返回的 List[Cluster] 应能直接交给
        TagManager.set_clusters 持久化（设计 §3.4：cluster_files -> List[Cluster]，
        再由 TagManager 写入 tags.json['clusters']）。

        历史缺陷：set_clusters 以 dict 风格 c.get('id') 访问 Cluster 对象，
        触发 AttributeError('Cluster' object has no attribute 'get')，导致「重新聚簇」
        在 main_window._on_cluster_done 中崩溃、簇无法落盘。本断言应不再抛异常。
        """
        tm = TagManager(save_path=os.path.join(tempfile.mkdtemp(), "tags.json"))
        files = {f"/docs/f{i}.md": {"title": f"文件{i}", "file_name": f"f{i}.md"}
                 for i in range(6)}
        eng = make_engine_like(files, tm)
        clusters = cluster_files(eng, seed=0)
        self.assertTrue(len(clusters) >= 1)
        # 不应抛 AttributeError
        tm.set_clusters(clusters)
        stored = tm.get_clusters()
        self.assertGreaterEqual(len(stored), 1)
        # 物理隔离仍成立：簇不污染手动标签体系
        self.assertNotIn("主题簇", tm.get_all_tags())


class TestClusterPersistence(unittest.TestCase):
    def test_set_clusters_independent(self):
        tm = TagManager(save_path=os.path.join(tempfile.mkdtemp(), "tags.json"))
        tm.set_tags_for_file("/a.md", ["t1"])
        tm.set_clusters([
            {"id": "cluster_0", "label": "主题簇 1",
             "files": ["/a.md", "/b.md"], "sample_titles": ["A"], "centroid": [0.1]},
        ])
        # 簇不进入 file_tags / tag_index
        self.assertNotIn("主题簇 1", tm.get_all_tags())
        self.assertEqual(tm.get_clusters()["cluster_0"]["files"], ["/a.md", "/b.md"])
        # file_tags 独立保留
        self.assertEqual(tm.get_tags_for_file("/a.md"), ["t1"])

    def test_dismiss_cluster_only(self):
        tm = TagManager(save_path=os.path.join(tempfile.mkdtemp(), "tags.json"))
        tm.set_tags_for_file("/a.md", ["t1"])
        tm.set_clusters([
            {"id": "c0", "label": "L0", "files": ["/a.md"], "sample_titles": [], "centroid": []},
            {"id": "c1", "label": "L1", "files": ["/b.md"], "sample_titles": [], "centroid": []},
        ])
        tm.dismiss_cluster("c0")
        self.assertNotIn("c0", tm.get_clusters())
        self.assertIn("c1", tm.get_clusters())
        # file_tags 不受影响
        self.assertEqual(tm.get_tags_for_file("/a.md"), ["t1"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
