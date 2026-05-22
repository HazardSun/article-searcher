"""
思维导图生成模块
从文章内容中提取结构并生成思维导图数据
"""

import re
from typing import List, Dict, Optional
from dataclasses import dataclass, field
from collections import Counter

from .parser import ContentParser
from .tagger import KeywordExtractor


@dataclass
class MindMapNode:
    """思维导图节点"""
    label: str
    children: List['MindMapNode'] = field(default_factory=list)
    level: int = 0
    weight: float = 1.0
    metadata: dict = field(default_factory=dict)

    def add_child(self, child: 'MindMapNode'):
        self.children.append(child)

    def to_dict(self) -> dict:
        return {
            'label': self.label,
            'level': self.level,
            'weight': self.weight,
            'children': [c.to_dict() for c in self.children]
        }


class MindMapGenerator:
    """思维导图生成器"""

    def __init__(self):
        self.parser = ContentParser()
        self.tagger = KeywordExtractor()

    def generate_from_file(
        self,
        file_path: str,
        content: str,
        is_html: bool = False
    ) -> MindMapNode:
        """
        从单个文件生成思维导图

        Args:
            file_path: 文件路径
            content: 文件内容
            is_html: 是否为 HTML 文件

        Returns:
            MindMapNode: 思维导图根节点
        """
        from pathlib import Path
        file_name = Path(file_path).stem

        if is_html:
            paragraphs = self.parser.parse_html(content)
        else:
            paragraphs = self.parser.parse_markdown(content)

        title = self._extract_title_from_paragraphs(paragraphs, file_name)
        root = MindMapNode(label=title, level=0)

        sections = self._group_into_hierarchy(paragraphs)
        for section in sections:
            root.add_child(section)

        keywords = self.tagger.extract_tags(content, title)
        if keywords:
            kw_node = MindMapNode(label="关键词", level=1, weight=0.8)
            for kw in keywords[:8]:
                kw_node.add_child(MindMapNode(label=kw, level=2, weight=0.5))
            root.add_child(kw_node)

        return root

    def generate_from_files(
        self,
        files: List[Dict[str, str]]
    ) -> MindMapNode:
        """
        从多个文件生成综合思维导图

        Args:
            files: [{"path": str, "content": str, "is_html": bool}]

        Returns:
            MindMapNode: 综合思维导图根节点
        """
        if not files:
            return MindMapNode(label="空", level=0)

        if len(files) == 1:
            return self.generate_from_file(
                files[0]['path'],
                files[0]['content'],
                files[0].get('is_html', False)
            )

        from pathlib import Path
        root = MindMapNode(label="文章集合", level=0)

        common_keywords = Counter()
        for file_info in files:
            file_path = file_info['path']
            content = file_info['content']
            is_html = file_info.get('is_html', False)

            file_name = Path(file_path).stem
            file_node = self.generate_from_file(file_path, content, is_html)
            file_node.label = file_name
            file_node.level = 1
            root.add_child(file_node)

            kws = self.tagger.extract_tags(content, file_node.label)
            for kw in kws:
                common_keywords[kw] += 1

        if common_keywords:
            shared_node = MindMapNode(label="共同主题", level=1, weight=0.9)
            for kw, count in common_keywords.most_common(10):
                if count >= 2:
                    shared_node.add_child(
                        MindMapNode(
                            label=f"{kw} ({count})",
                            level=2,
                            weight=count * 0.3
                        )
                    )
            if shared_node.children:
                root.add_child(shared_node)

        return root

    def generate_from_search_results(
        self,
        results: List[dict],
        engine
    ) -> MindMapNode:
        """
        从搜索结果生成思维导图

        Args:
            results: 搜索结果列表
            engine: ArticleSearchEngine 实例

        Returns:
            MindMapNode: 思维导图根节点
        """
        if not results:
            return MindMapNode(label="无结果", level=0)

        query = results[0].get('metadata', {}).get('title', '搜索结果')
        root = MindMapNode(label=f"搜索: {query}", level=0)

        file_results = {}
        for result in results:
            meta = result['metadata']
            file_path = meta.get('file_path', '')
            if file_path not in file_results:
                file_results[file_path] = {
                    'path': file_path,
                    'content': engine.get_file_content(file_path),
                    'is_html': file_path.lower().endswith(('.html', '.htm')),
                    'matches': []
                }
            file_results[file_path]['matches'].append(result)

        for file_path, file_info in file_results.items():
            from pathlib import Path
            file_name = Path(file_path).stem
            file_node = MindMapNode(label=file_name, level=1, weight=0.8)

            for match in file_info['matches'][:5]:
                match_content = match.get('content', '')[:100]
                if match_content:
                    file_node.add_child(
                        MindMapNode(
                            label=match_content + "...",
                            level=2,
                            weight=match.get('similarity', 0),
                            metadata={'start_line': match['metadata'].get('start_line', 0)}
                        )
                    )

            root.add_child(file_node)

        return root

    def _extract_title_from_paragraphs(
        self,
        paragraphs: List[dict],
        default: str
    ) -> str:
        """从段落列表中提取标题"""
        for para in paragraphs:
            if para['type'] == 'heading' and para.get('level', 0) <= 2:
                return para['content']
        return default

    def _group_into_hierarchy(
        self,
        paragraphs: List[dict]
    ) -> List[MindMapNode]:
        """将段落组织为层级结构"""
        nodes = []
        current_heading_node = None
        current_sub_nodes = []

        for para in paragraphs:
            if para['type'] == 'heading':
                if current_heading_node:
                    for sub in current_sub_nodes:
                        current_heading_node.add_child(sub)
                    nodes.append(current_heading_node)

                level = para.get('level', 1)
                current_heading_node = MindMapNode(
                    label=para['content'],
                    level=level,
                    weight=1.0 if level <= 2 else 0.7
                )
                current_sub_nodes = []
            elif para['type'] in ('paragraph', 'list'):
                if len(para['content']) > 20:
                    summary = self._summarize_text(para['content'], 40)
                    if current_heading_node:
                        current_sub_nodes.append(
                            MindMapNode(
                                label=summary,
                                level=para.get('level', 3),
                                weight=0.5
                            )
                        )

        if current_heading_node:
            for sub in current_sub_nodes:
                current_heading_node.add_child(sub)
            nodes.append(current_heading_node)

        if not nodes:
            for para in paragraphs[:10]:
                if para['type'] in ('paragraph', 'list') and len(para['content']) > 30:
                    nodes.append(
                        MindMapNode(
                            label=self._summarize_text(para['content'], 50),
                            level=1,
                            weight=0.5
                        )
                    )

        return nodes[:20]

    def _summarize_text(self, text: str, max_len: int) -> str:
        """截取文本摘要"""
        text = re.sub(r'\s+', ' ', text).strip()
        if len(text) <= max_len:
            return text
        return text[:max_len] + "..."
