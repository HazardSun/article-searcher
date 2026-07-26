"""
导出结果（纯函数，便于单元测试）

支持 Markdown 表格与 CSV 两种格式，不依赖任何第三方库（CSV 用标准库 csv）。
字段（顺序可配置）：
    file_path      文件路径
    filename       文件名
    snippet        命中片段
    score          评分（rrf_score 或 similarity，见设计 §8⑨）
    semantic_score 语义分（similarity，可选）
    lexical_score  词法分（BM25 原始分，可选）

约定：
- CSV 以 UTF-8 BOM（utf-8-sig）写入，Excel 直接可读中文。
- 空结果导出不报错（仅写表头，生成合法空表）。
- 逐行流式写入，避免大结果集一次性拼成大字符串占用过多内存。
"""

import csv
import io
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_FIELDS = ["file_path", "filename", "snippet", "score"]

FIELD_LABELS = {
    "file_path": "文件路径",
    "filename": "文件名",
    "snippet": "片段",
    "score": "评分",
    "semantic_score": "语义分",
    "lexical_score": "词法分",
}

# 片段/文件名中的换行会破坏表格与 CSV 行结构，统一折叠为空格
_WS_REPL = None


def _fold(value: str) -> str:
    return (value or "").replace("\r", " ").replace("\n", " ").replace("|", "\\|").strip()


def _extract_field(result: Dict[str, Any], field: str):
    meta = result.get("metadata", {}) or {}
    if field == "file_path":
        return meta.get("file_path", "")
    if field == "filename":
        return meta.get("file_name", "") or meta.get("title", "")
    if field == "snippet":
        return result.get("snippet", "") or ""
    if field == "score":
        s = result.get("rrf_score")
        if s is None:
            s = result.get("similarity", 0) or 0
        try:
            return round(float(s), 4)
        except (TypeError, ValueError):
            return 0.0
    if field == "semantic_score":
        try:
            return round(float(result.get("similarity", 0) or 0), 4)
        except (TypeError, ValueError):
            return 0.0
    if field == "lexical_score":
        try:
            return round(float(result.get("lexical_score", 0) or 0), 4)
        except (TypeError, ValueError):
            return 0.0
    return ""


def export_markdown(
    results: List[Dict[str, Any]],
    fields: Optional[List[str]] = None,
    path: Optional[str] = None,
) -> str:
    """导出为 Markdown 表格，返回生成的文本内容。

    results: search / query_similar 返回的结果列表。
    fields: 要导出的字段列表（默认 DEFAULT_FIELDS）。
    path: 若提供则写入该文件（UTF-8）；否则仅返回字符串。
    """
    fields = fields or DEFAULT_FIELDS
    lines: List[str] = []
    lines.append("# 搜索结果导出")
    lines.append("")
    lines.append(f"共 {len(results)} 条结果")
    lines.append("")
    header = "| " + " | ".join(FIELD_LABELS.get(f, f) for f in fields) + " |"
    sep = "|" + "|".join("---" for _ in fields) + "|"
    lines.append(header)
    lines.append(sep)
    for r in results:
        cells = [_fold(str(_extract_field(r, f))) for f in fields]
        lines.append("| " + " | ".join(cells) + " |")

    text = "\n".join(lines) + "\n"
    if path:
        try:
            with open(path, "w", encoding="utf-8", newline="") as fp:
                fp.write(text)
        except Exception as e:
            logger.warning("导出 Markdown 失败: %s", e)
            raise
    return text


def export_csv(
    results: List[Dict[str, Any]],
    fields: Optional[List[str]] = None,
    path: Optional[str] = None,
) -> Optional[str]:
    """导出为 CSV（UTF-8 BOM）。返回内容字符串（path 为 None 时）或 None（已写入文件）。

    results: 结果列表。fields: 字段列表。path: 文件路径。
    """
    fields = fields or DEFAULT_FIELDS
    header = [FIELD_LABELS.get(f, f) for f in fields]

    if path:
        try:
            # utf-8-sig 写入 BOM，保证 Excel 中文可读
            with open(path, "w", encoding="utf-8-sig", newline="") as fp:
                writer = csv.writer(fp)
                writer.writerow(header)
                for r in results:
                    writer.writerow([str(_extract_field(r, f)) for f in fields])
        except Exception as e:
            logger.warning("导出 CSV 失败: %s", e)
            raise
        return None

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    for r in results:
        writer.writerow([str(_extract_field(r, f)) for f in fields])
    return buf.getvalue()
