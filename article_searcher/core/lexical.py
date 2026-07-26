"""
词法检索引擎（BM25）
在本地维护一个轻量倒排索引，对切片文本做中英文分词与 BM25 打分，
弥补纯向量语义检索在「精确关键词 / 专有名词 / 文件名」上的不足。
索引随向量库一起持久化到磁盘。
"""

import os
import re
import json
import math
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_ASCII_RE = re.compile(r"[a-z0-9]+")


class LexicalIndex:
    """基于 BM25 的本地词法索引"""

    def __init__(self, path: str):
        self.path = path
        self.docs: Dict[str, Dict[str, Any]] = {}   # chunk_id -> {tokens, file_path, text, file_name, title}
        self.df: Dict[str, int] = {}                 # term -> 文档频率
        self.N = 0
        self.avgdl = 0.0
        self._load()

    # ------------------------------------------------------------------ #
    # 分词：英文小写词 + 中文 2/3-gram
    # ------------------------------------------------------------------ #
    @staticmethod
    def _tokenize(text: str) -> List[str]:
        text = (text or "").lower()
        tokens = _ASCII_RE.findall(text)
        for seg in _CJK_RE.findall(text):
            for i in range(len(seg)):
                for n in (2, 3):
                    if i + n <= len(seg):
                        tokens.append(seg[i:i + n])
        return tokens

    # ------------------------------------------------------------------ #
    # 增删
    # ------------------------------------------------------------------ #
    def add_chunks(self, chunks, defer_save: bool = False):
        changed = False
        for c in chunks:
            toks = self._tokenize(c.content)
            if not toks:
                continue
            old = self.docs.get(c.chunk_id)
            if old is not None and old["tokens"] == toks:
                continue
            if old is not None:
                for t in set(old["tokens"]):
                    self.df[t] = max(0, self.df.get(t, 1) - 1)
            self.docs[c.chunk_id] = {
                "tokens": toks,
                "file_path": c.file_path,
                "file_name": c.file_name,
                "title": c.title,
                "text": c.content[:400],
            }
            for t in set(toks):
                self.df[t] = self.df.get(t, 0) + 1
            changed = True

        if changed:
            self._recompute_stats()
            if not defer_save:
                self._save()

    def save(self):
        """显式落盘（索引循环结束后统一调用一次）"""
        self._save()

    def remove_file(self, file_path: str):
        to_delete = [cid for cid, d in self.docs.items() if d["file_path"] == file_path]
        if not to_delete:
            return
        for cid in to_delete:
            for t in set(self.docs[cid]["tokens"]):
                self.df[t] = max(0, self.df.get(t, 1) - 1)
            del self.docs[cid]
        self._recompute_stats()
        self._save()

    def _recompute_stats(self):
        self.N = len(self.docs)
        total = sum(len(d["tokens"]) for d in self.docs.values())
        self.avgdl = (total / self.N) if self.N else 0.0

    # ------------------------------------------------------------------ #
    # 检索
    # ------------------------------------------------------------------ #
    def search(self, query: str, top_k: int = 30) -> List[Dict[str, Any]]:
        q = self._tokenize(query)
        if not q or self.N == 0:
            return []
        scores: Dict[str, float] = {}
        for term in set(q):
            df = self.df.get(term, 0)
            if df == 0:
                continue
            idf = math.log(1 + (self.N - df + 0.5) / (df + 0.5))
            for cid, doc in self.docs.items():
                f = doc["tokens"].count(term)
                if f == 0:
                    continue
                dl = len(doc["tokens"])
                denom = f + 1.2 * (1 - 0.75 + 0.75 * (dl / self.avgdl if self.avgdl else dl))
                scores[cid] = scores.get(cid, 0.0) + idf * (f * (1.2 + 1) / denom)

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
        return [
            {
                "id": cid,
                "content": self.docs[cid]["text"],
                "metadata": {
                    "file_path": self.docs[cid]["file_path"],
                    "file_name": self.docs[cid].get("file_name", ""),
                    "title": self.docs[cid].get("title", ""),
                },
                "lexical_score": s,
            }
            for cid, s in ranked
        ]

    def count(self) -> int:
        return self.N

    # ------------------------------------------------------------------ #
    # 持久化
    # ------------------------------------------------------------------ #
    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.docs = data.get("docs", {})
            self.df = data.get("df", {})
            self.N = data.get("N", 0)
            self.avgdl = data.get("avgdl", 0.0)
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning("词法索引加载失败: %s", e)

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(
                    {"docs": self.docs, "df": self.df, "N": self.N, "avgdl": self.avgdl},
                    f, ensure_ascii=False,
                )
        except Exception as e:
            logger.warning("词法索引保存失败: %s", e)
