"""
智能切片模块
按照语义完整性（段落、标题组）进行文本切片，保留元数据
"""

from typing import List
from dataclasses import dataclass

from .parser import TextChunk


@dataclass
class ChunkConfig:
    """切片配置"""
    max_chunk_size: int = 800
    min_chunk_size: int = 50
    overlap_size: int = 100
    respect_boundaries: bool = True


class SemanticChunker:
    """语义切片器 - 按标题组和段落边界进行智能切片"""

    def __init__(self, config: ChunkConfig = None):
        self.config = config or ChunkConfig()

    def chunk_article(
        self,
        file_path: str,
        file_name: str,
        title: str,
        paragraphs: List[dict]
    ) -> List[TextChunk]:
        """
        将文章段落列表切分为语义完整的文本块

        Args:
            file_path: 文件完整路径
            file_name: 文件名
            title: 文章标题
            paragraphs: 解析后的段落列表 [{"type": ..., "content": ..., "line": ...}]

        Returns:
            List[TextChunk]: 切片结果列表
        """
        groups = self._group_by_sections(paragraphs)
        chunks = []

        for group_idx, group in enumerate(groups):
            group_chunks = self._split_group(group)
            for sub_idx, (content, start_line, end_line) in enumerate(group_chunks):
                chunk_id = f"{file_path}:{start_line}:{group_idx}:{sub_idx}"
                chunk = TextChunk(
                    chunk_id=chunk_id,
                    file_path=file_path,
                    file_name=file_name,
                    title=title,
                    content=content,
                    start_line=start_line,
                    end_line=end_line,
                    chunk_index=len(chunks),
                    total_chunks=0,
                    metadata={
                        'section_index': group_idx,
                        'sub_index': sub_idx,
                        'has_heading': any(p['type'] == 'heading' for p in group)
                    }
                )
                chunks.append(chunk)

        total = len(chunks)
        for chunk in chunks:
            chunk.total_chunks = total

        return chunks

    def _group_by_sections(self, paragraphs: List[dict]) -> List[List[dict]]:
        """
        按标题将段落分组为语义区块
        每个区块以一个标题开头，包含其下的所有段落
        """
        groups = []
        current_group = []

        for para in paragraphs:
            if para['type'] == 'heading' and para.get('level', 0) <= 3:
                if current_group:
                    groups.append(current_group)
                current_group = [para]
            else:
                if not current_group:
                    current_group.append({
                        'type': 'heading',
                        'content': '',
                        'line': para.get('line', 0),
                        'level': 0
                    })
                current_group.append(para)

        if current_group:
            groups.append(current_group)

        if not groups and paragraphs:
            groups.append(paragraphs)

        return groups

    def _split_group(self, group: List[dict]) -> List[tuple]:
        """
        将一个语义区块切分为一个或多个文本块
        返回: [(content, start_line, end_line), ...]
        """
        result = []
        current_content = []
        current_start = None
        current_size = 0

        for para in group:
            text = para['content']
            text_len = len(text)

            if not text.strip():
                continue

            if current_start is None:
                current_start = para.get('line', 0)

            if current_size + text_len > self.config.max_chunk_size and current_content:
                combined = '\n\n'.join(current_content)
                result.append((combined, current_start, para.get('line', 0)))

                overlap_text = self._get_overlap_text(current_content)
                current_content = [overlap_text] if overlap_text else []
                current_start = para.get('line', 0)
                current_size = len(overlap_text) if overlap_text else 0

            current_content.append(text)
            current_size += text_len

        if current_content:
            combined = '\n\n'.join(current_content)
            last_line = group[-1].get('line', 0) if group else current_start
            result.append((combined, current_start, last_line))

        return result

    def _get_overlap_text(self, paragraphs: List[str]) -> str:
        """获取重叠文本用于上下文连贯"""
        if not paragraphs or self.config.overlap_size <= 0:
            return ""

        combined = '\n'.join(paragraphs)
        if len(combined) <= self.config.overlap_size:
            return combined

        start_idx = max(0, len(combined) - self.config.overlap_size)
        overlap = combined[start_idx:]

        newline_idx = overlap.find('\n')
        if newline_idx > 0:
            overlap = overlap[newline_idx + 1:]

        return overlap
