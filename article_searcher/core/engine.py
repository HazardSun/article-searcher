"""
文章搜索引擎
整合文件扫描、解析、切片、向量化和检索的核心引擎
"""

import os
import logging
from typing import List, Optional, Dict, Any
from pathlib import Path

from .parser import FileScanner, ContentParser, FileMetadata, TextChunk
from .chunker import SemanticChunker, ChunkConfig
from .embedding import EmbeddingEngine
from .vectorstore import VectorStore
from .tagger import TagManager

logger = logging.getLogger(__name__)


class ArticleSearchEngine:
    """文章搜索引擎 - 核心业务逻辑"""

    def __init__(
        self,
        db_path: str = None,
        model_cache_dir: str = None,
        embedding_model: str = None
    ):
        self.embedding_engine = EmbeddingEngine(
            model_name=embedding_model,
            cache_dir=model_cache_dir
        )
        self.vector_store = VectorStore(
            db_path=db_path,
            embedding_engine=self.embedding_engine
        )
        self.file_scanner = FileScanner()
        self.content_parser = ContentParser()
        self.chunker = SemanticChunker()
        self.tag_manager = TagManager()

        self._current_folder: Optional[str] = None
        self._file_contents: Dict[str, str] = {}

    @property
    def current_folder(self) -> Optional[str]:
        return self._current_folder

    def load_folder(self, folder_path: str, incremental: bool = True,
                    progress_callback=None) -> Dict[str, Any]:
        """
        加载文件夹并构建索引

        Args:
            folder_path: 文件夹路径
            incremental: 是否启用增量更新

        Returns:
            Dict: 处理结果统计
        """
        self._current_folder = folder_path
        stats = {
            'total_files': 0,
            'new_files': 0,
            'updated_files': 0,
            'unchanged_files': 0,
            'total_chunks': 0,
            'errors': []
        }

        logger.info(f"Scanning folder: {folder_path}")
        files = self.file_scanner.scan_directory(folder_path)
        stats['total_files'] = len(files)

        indexed_files = self.vector_store.get_indexed_files()

        for idx, file_meta in enumerate(files):
            try:
                if progress_callback:
                    progress_callback(idx + 1, len(files), file_meta.file_name)

                if incremental and file_meta.file_path in indexed_files:
                    existing = indexed_files[file_meta.file_path]
                    file_stat = Path(file_meta.file_path).stat()
                    if file_meta.md5_hash == self._compute_file_md5(file_meta.file_path):
                        stats['unchanged_files'] += 1
                        continue

                self._process_file(file_meta)
                if file_meta.file_path in indexed_files:
                    stats['updated_files'] += 1
                else:
                    stats['new_files'] += 1

            except Exception as e:
                error_msg = f"Error processing {file_meta.file_name}: {str(e)}"
                stats['errors'].append(error_msg)
                logger.error(error_msg)

        stats['total_chunks'] = self.vector_store.get_chunk_count()
        logger.info(f"Indexing complete: {stats}")
        return stats

    def _process_file(self, file_meta: FileMetadata):
        """处理单个文件：读取、解析、切片、向量化、存储"""
        file_path = file_meta.file_path
        ext = file_meta.file_extension

        self.vector_store.delete_by_file(file_path)
        self.tag_manager.remove_file(file_path)

        if ext == '.pdf':
            paragraphs = self.content_parser.parse_pdf(file_path)
            content = '\n'.join(p['content'] for p in paragraphs)
        elif ext == '.docx':
            paragraphs = self.content_parser.parse_docx(file_path)
            content = '\n'.join(p['content'] for p in paragraphs)
        elif ext == '.txt':
            content = Path(file_path).read_text(encoding='utf-8', errors='ignore')
            paragraphs = self.content_parser.parse_text(content)
        elif ext == '.md':
            content = Path(file_path).read_text(encoding='utf-8', errors='ignore')
            paragraphs = self.content_parser.parse_markdown(content)
        else:
            content = Path(file_path).read_text(encoding='utf-8', errors='ignore')
            paragraphs = self.content_parser.parse_html(content)

        self._file_contents[file_path] = content

        chunks = self.chunker.chunk_article(
            file_path=file_path,
            file_name=file_meta.file_name,
            title=file_meta.title,
            paragraphs=paragraphs
        )

        if not chunks:
            return

        texts = [c.content for c in chunks]
        embeddings = self.embedding_engine.encode(texts)

        self.vector_store.add_chunks(chunks, embeddings)

        title = file_meta.title or file_meta.file_name
        tags = self.tag_manager.generate_tags(file_path, content, title)
        file_meta.tags = tags

    def search(
        self,
        query: str,
        top_k: int = 10,
        tag_filter: Optional[str] = None
    ) -> List[dict]:
        """
        语义搜索

        Args:
            query: 搜索 query
            top_k: 返回结果数量
            tag_filter: 可选的标签过滤

        Returns:
            List[dict]: 搜索结果
        """
        if not query.strip():
            return []

        query_embedding = self.embedding_engine.encode_single(query)

        filter_metadata = None
        if tag_filter:
            files_by_tag = self.tag_manager.get_files_by_tag(tag_filter)
            if files_by_tag:
                filter_metadata = {'file_path': files_by_tag}

        results = self.vector_store.search(
            query_embedding=query_embedding,
            top_k=top_k,
            filter_metadata=filter_metadata
        )

        for result in results:
            file_path = result['metadata'].get('file_path', '')
            result['file_tags'] = self.tag_manager.get_tags_for_file(file_path)
            result['file_content'] = self._file_contents.get(file_path, '')

        return results

    def get_file_content(self, file_path: str) -> str:
        """获取文件完整内容"""
        if file_path in self._file_contents:
            return self._file_contents[file_path]

        try:
            ext = Path(file_path).suffix
            if ext == '.pdf':
                from .parser import ContentParser as CP
                paras = CP.parse_pdf(file_path)
                content = '\n'.join(p['content'] for p in paras)
            elif ext == '.docx':
                from .parser import ContentParser as CP
                paras = CP.parse_docx(file_path)
                content = '\n'.join(p['content'] for p in paras)
            else:
                content = Path(file_path).read_text(encoding='utf-8', errors='ignore')
            self._file_contents[file_path] = content
            return content
        except Exception as e:
            logger.error(f"Failed to read file {file_path}: {e}")
            return ""

    def get_all_tags(self) -> List[str]:
        """获取所有标签"""
        return self.tag_manager.get_all_tags()

    def get_tag_counts(self) -> Dict[str, int]:
        """获取标签统计"""
        return self.tag_manager.get_tag_counts()

    def get_indexed_files(self) -> Dict[str, dict]:
        """获取已索引文件信息"""
        return self.vector_store.get_indexed_files()

    def get_status(self) -> Dict[str, Any]:
        """获取引擎状态"""
        return {
            'current_folder': self._current_folder,
            'indexed_files': len(self.vector_store.get_indexed_files()),
            'total_chunks': self.vector_store.get_chunk_count(),
            'embedding_model': self.embedding_engine.get_status_info(),
            'tags': self.get_tag_counts()
        }

    @staticmethod
    def _compute_file_md5(file_path: str) -> str:
        """计算文件 MD5"""
        import hashlib
        hasher = hashlib.md5()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                hasher.update(chunk)
        return hasher.hexdigest()
