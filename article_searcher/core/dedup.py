"""
近似 / 重复文章检测（功能12 / P2-12）

核心逻辑（纯函数 + find_duplicate_pairs），复用现有文件级向量
`engine.vector_store.get_file_vector`（均值池化向量，已在 ChromaDB），
**不调用 encode**；用 numpy 做分块余弦相似度，收集 `sim ≥ threshold`
的无重复 (i<j) 对，按相似度降序返回。

与 `engine.query_similar` 的区别：query_similar 是「给定一篇 → top-k 邻居」
（单文件推荐）；本函数是「全库两两」全局去重，二者仅共享底层
`get_file_vector` 原语，此处在其之上自建相似度矩阵。

零新增依赖：仅用 numpy。分块计算**不物化 n×n 矩阵**（内存 ~O(k)，k=命中对数）。
结果为只读分析，不写 tags.json（与三权分立零冲突）。
"""

from dataclasses import dataclass
from typing import List, Optional

import numpy as np


@dataclass
class DuplicatePair:
    """一对近似/重复文章。"""
    file_a: str
    file_b: str
    similarity: float   # 余弦相似度 [0, 1]


# 余弦相似度矩阵的分块大小（控制单次切片内存，不物化整张 n×n）
_CHUNK = 256
# 软上限：超过该文件数的库仅取前 max_files 篇检测（由调用方决定是否告警）
_DEFAULT_MAX_FILES = 5000


def find_duplicate_pairs(
    engine,
    threshold: float = 0.85,
    max_files: int = _DEFAULT_MAX_FILES,
) -> List[DuplicatePair]:
    """全库近似/重复文章检测。

    Args:
        engine: 提供 `vector_store.get_file_vector(fp)` 与 `get_indexed_files()` 的引擎
        threshold: 相似度阈值（默认 0.85，近重复）
        max_files: 软上限，超过则仅取前 max_files 篇（降采样，避免超大库爆内存）

    Returns:
        按 similarity 降序的 DuplicatePair 列表（每对仅出现一次，i<j）。
        无向量或不足 2 篇时返回空列表。

    注意：本函数不调用 encode；向量直接来自 `get_file_vector`（已存在的 ChromaDB 向量）。
    """
    indexed = engine.get_indexed_files()
    if not indexed:
        return []

    file_paths = sorted(indexed.keys())
    if len(file_paths) > max_files:
        file_paths = file_paths[:max_files]

    # 1) 取全量文件级向量（复用 get_file_vector，不 encode）
    vectors: List[np.ndarray] = []
    valid_paths: List[str] = []
    vs = engine.vector_store
    for fp in file_paths:
        try:
            vec = vs.get_file_vector(fp)
        except Exception:  # noqa: BLE001 - 单文件取向量失败不应中断整体
            vec = None
        if vec is not None and np.any(vec):
            vectors.append(vec)
            valid_paths.append(fp)

    if len(vectors) < 2:
        return []

    # 2) 堆叠并 L2 归一化（余弦相似度 = 归一化后向量内积）
    matrix = np.vstack(vectors)                       # (k, dim)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    norm_matrix = matrix / norms

    # 3) 分块计算余弦相似度，只收集 sim >= threshold 的 (i<j) 对
    #    —— 不物化 n×n 矩阵，内存 ~O(命中对数)。
    k = norm_matrix.shape[0]
    pairs: List[DuplicatePair] = []
    for i in range(k):
        vi = norm_matrix[i]
        for j_start in range(i + 1, k, _CHUNK):
            j_end = min(j_start + _CHUNK, k)
            sims = norm_matrix[j_start:j_end] @ vi     # (chunk,)
            for offset, sim in enumerate(sims):
                if sim >= threshold:
                    j = j_start + offset
                    pairs.append(DuplicatePair(
                        valid_paths[i], valid_paths[j], float(sim)))

    # 4) 按相似度降序
    pairs.sort(key=lambda p: p.similarity, reverse=True)
    return pairs
