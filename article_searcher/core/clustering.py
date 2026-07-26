"""
语义自动聚簇（功能5）—— numpy-only KMeans（不引入 sklearn）

- 取全部已索引文件的「文件级向量」（engine.vector_store.get_file_vector，均值池化）；
- 用 k-means++ 初始化 + 少量迭代的 KMeans 聚类；
- 簇数由 estimate_k 启发式估计（封顶 12）；
- 文件数 < 3 直接返回空（降级，不聚类）；
- 结果存 engine.tag_manager 的 clusters 独立字段（与 file_tags 物理隔离）。

簇标签：取簇内出现频率最高的手动标签作为簇名；无标签则回退为「主题簇 N」。
"""

import math
import logging
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class Cluster:
    """单个簇"""
    id: str
    label: str
    files: List[str] = field(default_factory=list)
    sample_titles: List[str] = field(default_factory=list)
    centroid: List[float] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def estimate_k(n_files: int, cap: int = 12) -> int:
    """启发式簇数：min(max(2, int(sqrt(n/2))), cap)，封顶 12。"""
    if n_files < 3:
        return 0
    k = max(2, int(math.sqrt(n_files / 2)))
    return min(k, cap)


def _kmeans(X: np.ndarray, k: int, n_iter: int = 50, seed: int = 0):
    """极简 KMeans（k-means++ 初始化）。返回 (labels, centers)。"""
    n = X.shape[0]
    rng = np.random.default_rng(seed)
    # k-means++ 初始化
    centers = [X[rng.integers(n)].copy()]
    for _ in range(1, k):
        d2 = np.min(
            np.linalg.norm(X[:, None, :] - np.array(centers)[None, :, :], axis=2) ** 2,
            axis=1,
        )
        total = d2.sum()
        if total <= 0:
            idx = rng.integers(n)
        else:
            probs = d2 / total
            idx = rng.choice(n, p=probs)
        centers.append(X[idx].copy())
    centers = np.array(centers, dtype=float)

    labels = np.zeros(n, dtype=int)
    for _ in range(n_iter):
        dists = np.linalg.norm(X[:, None, :] - centers[None, :, :], axis=2)
        new_labels = np.argmin(dists, axis=1)
        new_centers = []
        for j in range(k):
            mask = new_labels == j
            if np.any(mask):
                new_centers.append(X[mask].mean(axis=0))
            else:
                # 空簇：保留原中心
                new_centers.append(centers[j])
        new_centers = np.array(new_centers, dtype=float)
        if np.allclose(new_centers, centers):
            labels = new_labels
            centers = new_centers
            break
        labels = new_labels
        centers = new_centers
    return labels, centers


def _file_title(engine, file_path: str) -> str:
    """尽量取到可读标题：优先 indexed_files 元数据里的 title/file_name。"""
    try:
        meta = engine.vector_store.get_indexed_files().get(file_path, {})
        return meta.get("title") or meta.get("file_name") or file_path
    except Exception:
        return file_path


def cluster_files(
    engine,
    n_clusters: Optional[int] = None,
    cap: int = 12,
    seed: int = 0,
) -> List[Cluster]:
    """对全部已索引文件做语义聚簇，返回 Cluster 列表（design §3.4）。

    需 engine 提供：vector_store.get_file_vector(file_path) 与
    tag_manager.get_tags_for_file(file_path)。文件数 < 3 返回空（不聚类）。
    """
    try:
        indexed = engine.vector_store.get_indexed_files()
    except Exception as e:
        logger.warning("聚簇读取索引失败: %s", e)
        return []

    vectors = []
    paths = []
    for fp in indexed.keys():
        v = engine.vector_store.get_file_vector(fp)
        if v is None:
            continue
        vectors.append(np.asarray(v, dtype=float))
        paths.append(fp)

    n = len(vectors)
    if n < 3:
        logger.info("文件数 %d < 3，跳过聚簇", n)
        return []

    X = np.vstack(vectors)
    # L2 归一化，避免长向量主导距离
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    X = X / norms

    k = n_clusters or estimate_k(n, cap=cap)
    k = max(2, min(k, n - 1))

    labels, centers = _kmeans(X, k, seed=seed)

    clusters: List[Cluster] = []
    for j in range(k):
        members = [paths[i] for i in range(n) if labels[i] == j]
        if not members:
            continue
        # 标签：取该簇文件手动标签的众数
        tag_counter: Dict[str, int] = {}
        for fp in members:
            for t in engine.tag_manager.get_tags_for_file(fp):
                tag_counter[t] = tag_counter.get(t, 0) + 1
        if tag_counter:
            label = max(tag_counter.items(), key=lambda kv: kv[1])[0]
        else:
            label = f"主题簇 {j + 1}"
        # 样本标题：取余弦距离中心最近的 3 个文件
        centroid = centers[j]
        try:
            sims = X[labels == j] @ centroid
            order = np.argsort(-sims)
            member_arr = [members[i] for i in order]
            sample = [_file_title(engine, fp) for fp in member_arr[:3]]
        except Exception:
            sample = [_file_title(engine, fp) for fp in members[:3]]
        clusters.append(Cluster(
            id=f"cluster_{j}",
            label=label,
            files=members,
            sample_titles=sample,
            centroid=centroid.tolist(),
        ))
    return clusters
