"""
搜索融合工具（纯函数，便于单元测试）
实现 Reciprocal Rank Fusion (RRF)，将语义检索与词法(BM25)检索的结果融合，
兼顾「意思相近」与「关键词命中」。
"""

import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


def rrf(semantic: List[Dict[str, Any]],
        lexical: List[Dict[str, Any]],
        k: int = 60,
        top_n: int = None) -> List[Dict[str, Any]]:
    """
    Reciprocal Rank Fusion。

    Args:
        semantic: 语义检索结果（含 'id'）
        lexical:  词法检索结果（含 'id'）
        k:        RRF 常数，越大则排名差距影响越小
        top_n:    返回前 N 条

    Returns:
        融合并排序后的结果列表，每条附加 'rrf_score'
    """
    fused: Dict[str, Dict[str, Any]] = {}

    for rank, item in enumerate(semantic):
        rid = item.get("id")
        if rid is None:
            continue
        entry = fused.setdefault(rid, {"item": item, "score": 0.0})
        entry["score"] += 1.0 / (k + rank + 1)

    for rank, item in enumerate(lexical):
        rid = item.get("id")
        if rid is None:
            continue
        entry = fused.setdefault(rid, {"item": item, "score": 0.0})
        entry["score"] += 1.0 / (k + rank + 1)

    merged = sorted(fused.values(), key=lambda e: e["score"], reverse=True)
    if top_n is not None:
        merged = merged[:top_n]

    for entry in merged:
        entry["item"]["rrf_score"] = round(entry["score"], 6)

    return [entry["item"] for entry in merged]


def normalize_scores(results: List[Dict[str, Any]], key: str = "similarity"):
    """将某个分数键线性归一化到 [0,1]，便于 UI 展示"""
    if not results:
        return results
    vals = [r.get(key, 0) or 0 for r in results]
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        for r in results:
            r[f"{key}_norm"] = 1.0 if hi > 0 else 0.0
        return results
    for r in results:
        r[f"{key}_norm"] = (r.get(key, 0) - lo) / (hi - lo)
    return results
