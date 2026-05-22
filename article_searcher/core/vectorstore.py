"""
向量数据库模块
使用 ChromaDB 实现本地向量存储，支持增量更新
"""

import os
import json
import logging
from typing import List, Optional, Dict, Any
from pathlib import Path

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

    def delete_by_file(self, file_path: str):
        """删除指定文件的所有切片"""
        self.collection.delete(
            where={"file_path": file_path}
        )
        self._remove_from_index_meta(file_path)

    def get_indexed_files(self) -> Dict[str, dict]:
        """获取已索引的文件列表"""
        return self._index_meta.get('files', {})

    def get_chunk_count(self) -> int:
        """获取当前数据库中的切片总数"""
        return self.collection.count()

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
