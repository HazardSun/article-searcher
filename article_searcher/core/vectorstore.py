"""
向量数据库模块
使用 ChromaDB 实现本地向量存储，支持增量更新
"""

import os
import json
import logging
from typing import List, Optional, Dict, Any
from pathlib import Path

import numpy as np
import chromadb
from chromadb.config import Settings

from .parser import TextChunk
from .embedding import EmbeddingEngine

logger = logging.getLogger(__name__)


class VectorStore:
    """本地向量数据库 - 基于 ChromaDB"""

    def __init__(
        self,
        db_path: str = None,
        embedding_engine: EmbeddingEngine = None
    ):
        self.db_path = db_path or os.path.join(
            os.path.expanduser("~"), ".cache", "article_searcher", "chromadb"
        )
        os.makedirs(self.db_path, exist_ok=True)

        self.embedding_engine = embedding_engine
        self._client = None
        self._collection = None
        self._index_meta_path = os.path.join(self.db_path, "index_meta.json")
        self._index_meta = self._load_index_meta()

    @property
    def client(self):
        if self._client is None:
            self._client = chromadb.PersistentClient(
                path=self.db_path,
                settings=Settings(anonymized_telemetry=False)
            )
        return self._client

    @property
    def collection(self):
        if self._collection is None:
            self._collection = self.client.get_or_create_collection(
                name="articles",
                metadata={"hnsw:space": "cosine"}
            )
        return self._collection

    def add_chunks(
        self,
        chunks: List[TextChunk],
        embeddings: Any,
        batch_size: int = 100
    ):
        """
        批量添加切片到向量数据库

        Args:
            chunks: 文本切片列表
            embeddings: 对应的向量矩阵 (numpy array)
            batch_size: 批处理大小
        """
        if not chunks:
            return

        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            batch_embeddings = embeddings[i:i + batch_size]

            ids = [c.chunk_id for c in batch]
            documents = [c.content for c in batch]
            metadatas = [self._chunk_to_metadata(c) for c in batch]

            self.collection.upsert(
                ids=ids,
                documents=documents,
                embeddings=batch_embeddings.tolist(),
                metadatas=metadatas
            )

        self._update_index_meta(chunks)
        logger.info(f"Added {len(chunks)} chunks to vector store")

    def search(
        self,
        query_embedding: Any,
        top_k: int = 10,
        filter_metadata: dict = None
    ) -> List[dict]:
        """
        语义搜索

        Args:
            query_embedding: 查询向量
            top_k: 返回结果数量
            filter_metadata: 可选的元数据过滤条件

        Returns:
            List[dict]: 搜索结果，包含文档、元数据和距离
        """
        where_filter = None
        if filter_metadata:
            where_filter = self._build_where_clause(filter_metadata)

        results = self.collection.query(
            query_embeddings=[query_embedding.tolist()],
            n_results=top_k,
            where=where_filter,
            include=["documents", "metadatas", "distances"]
        )

        if not results['ids'] or not results['ids'][0]:
            return []

        search_results = []
        for idx, doc_id in enumerate(results['ids'][0]):
            search_results.append({
                'id': doc_id,
                'content': results['documents'][0][idx],
                'metadata': results['metadatas'][0][idx],
                'distance': results['distances'][0][idx],
                'similarity': 1 - results['distances'][0][idx]
            })

        search_results.sort(key=lambda x: x['similarity'], reverse=True)
        return search_results

    def get_file_vector(self, file_path: str) -> Optional[np.ndarray]:
        """取该文件所有 chunk 向量，均值池化为文件级向量。

        返回 (dim,) 的 numpy 数组；文件不存在或无向量时返回 None。
        均值池化对 bge 类归一化向量稳定（见设计 §8⑤）。
        """
        try:
            res = self.collection.get(
                where={"file_path": file_path},
                include=["embeddings"],
            )
        except Exception as e:
            logger.warning("get_file_vector 读取向量失败: %s", e)
            return None

        ids = res.get("ids") or []
        embs = res.get("embeddings") or []
        if not ids or embs is None or len(embs) == 0:
            return None
        try:
            arr = np.array(embs, dtype=float)
        except Exception:
            return None
        if arr.size == 0:
            return None
        # 均值池化（axis=0），保持与单向量同形
        return arr.mean(axis=0)

    def query_similar(self, file_path: str, top_k: int = 5) -> List[dict]:
        """文件级近邻：用文件向量 query → 按 file_path 聚合（取最相似 chunk 的相似度）
        → 排除自身 → 取 top_k。

        返回每文件一条记录（取该文件内相似度最高的 chunk 作为代表）：
            {"file_path", "metadata", "content", "chunk_id", "distance", "similarity"}
        """
        fv = self.get_file_vector(file_path)
        if fv is None:
            return []

        n_results = max(top_k * 5, 30)
        try:
            res = self.collection.query(
                query_embeddings=[fv.tolist()],
                n_results=n_results,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            logger.warning("query_similar 检索失败: %s", e)
            return []

        if not res.get("ids") or not res["ids"][0]:
            return []

        # 按 file_path 聚合：保留相似度最高（距离最小）的 chunk
        best: Dict[str, dict] = {}
        ids0 = res["ids"][0]
        docs0 = res["documents"][0]
        meta0 = res["metadatas"][0]
        dist0 = res["distances"][0]
        for i, cid in enumerate(ids0):
            meta = meta0[i] or {}
            fp = meta.get("file_path", "")
            if not fp or fp == file_path:
                continue  # 排除自身
            dist = dist0[i]
            sim = 1.0 - dist
            entry = best.get(fp)
            if entry is None or sim > entry["similarity"]:
                best[fp] = {
                    "file_path": fp,
                    "metadata": meta,
                    "content": docs0[i],
                    "chunk_id": cid,
                    "distance": dist,
                    "similarity": sim,
                }

        items = sorted(best.values(), key=lambda x: x["similarity"], reverse=True)[:top_k]
        return items

    def delete_by_file(self, file_path: str):
        """删除指定文件的所有切片"""
        self.collection.delete(
            where={"file_path": file_path}
        )
        self._remove_from_index_meta(file_path)

    def get_indexed_files(self) -> Dict[str, dict]:
        """获取已索引的文件列表"""
        return self._index_meta.get('files', {})

    def set_file_md5(self, file_path: str, md5: str):
        """记录已索引文件的 MD5，用于增量更新判断"""
        entry = self._index_meta['files'].setdefault(file_path, {})
        entry['md5'] = md5
        self._save_index_meta()

    def get_file_md5(self, file_path: str) -> Optional[str]:
        """读取已索引文件的 MD5（未记录则返回 None）"""
        return self._index_meta.get('files', {}).get(file_path, {}).get('md5')

    def get_all_md5s(self) -> Dict[str, str]:
        """返回全部已索引文件的 MD5 映射 {file_path: md5}（用于增量判断）"""
        return {
            fp: info.get('md5')
            for fp, info in self._index_meta.get('files', {}).items()
        }

    def get_embedding_dim(self) -> Optional[int]:
        """读取已索引向量维度（用于模型切换后的兼容性判断）"""
        return self._index_meta.get('embedding_dim')

    def set_embedding_dim(self, dim: int):
        """记录当前索引的向量维度"""
        if dim and self._index_meta.get('embedding_dim') != dim:
            self._index_meta['embedding_dim'] = dim
            self._save_index_meta()

    def get_chunk_count(self) -> int:
        """获取当前数据库中的切片总数"""
        return self.collection.count()

    def get_chunks_by_files(self, file_paths: set) -> List[dict]:
        """按 file_path 列举匹配文件的所有 chunk（不依赖 query 向量）。

        用于"空查询 + 标签/路径过滤"的浏览模式：返回这些文件对应的全部 chunk，
        每条形如 {"id", "content", "metadata"}。file_paths 为空时返回 []。
        """
        if not file_paths:
            return []
        try:
            res = self.collection.get(
                where={"file_path": {"$in": list(file_paths)}},
                include=["documents", "metadatas"],
            )
        except Exception as e:
            logger.warning("get_chunks_by_files 读取失败: %s", e)
            return []
        ids = res.get("ids") or []
        docs = res.get("documents") or []
        metas = res.get("metadatas") or []
        return [
            {"id": ids[i], "content": docs[i], "metadata": metas[i] or {}}
            for i in range(len(ids))
        ]

    def _chunk_to_metadata(self, chunk: TextChunk) -> dict:
        """将 TextChunk 转换为 ChromaDB 兼容的元数据格式"""
        return {
            'file_path': chunk.file_path,
            'file_name': chunk.file_name,
            'title': chunk.title or chunk.file_name,
            'start_line': chunk.start_line,
            'end_line': chunk.end_line,
            'chunk_index': chunk.chunk_index,
            'total_chunks': chunk.total_chunks,
            'section_index': chunk.metadata.get('section_index', 0),
            'has_heading': str(chunk.metadata.get('has_heading', False))
        }

    def _build_where_clause(self, filters: dict) -> dict:
        """构建 ChromaDB where 过滤条件"""
        conditions = []
        for key, value in filters.items():
            if isinstance(value, list):
                conditions.append({key: {"$in": value}})
            else:
                conditions.append({key: value})

        if len(conditions) == 1:
            return conditions[0]
        elif len(conditions) > 1:
            return {"$and": conditions}
        return None

    def _load_index_meta(self) -> dict:
        """加载索引元数据"""
        if os.path.exists(self._index_meta_path):
            try:
                with open(self._index_meta_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {'files': {}, 'last_updated': None}

    def _save_index_meta(self):
        """保存索引元数据"""
        import datetime
        self._index_meta['last_updated'] = datetime.datetime.now().isoformat()
        with open(self._index_meta_path, 'w', encoding='utf-8') as f:
            json.dump(self._index_meta, f, ensure_ascii=False, indent=2)

    def _update_index_meta(self, chunks: List[TextChunk]):
        """更新索引元数据"""
        files = set(c.file_path for c in chunks)
        for file_path in files:
            file_chunks = [c for c in chunks if c.file_path == file_path]
            if file_chunks:
                self._index_meta['files'][file_path] = {
                    'chunk_count': len(file_chunks),
                    'title': file_chunks[0].title,
                    'file_name': file_chunks[0].file_name
                }
        self._save_index_meta()

    def _remove_from_index_meta(self, file_path: str):
        """从索引元数据中移除文件"""
        self._index_meta['files'].pop(file_path, None)
        self._save_index_meta()
