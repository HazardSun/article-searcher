"""
智能标签模块
基于文本内容和 Embedding 聚类自动为文章生成标签
"""

import re
import logging
from typing import List, Dict, Optional
from collections import Counter

logger = logging.getLogger(__name__)


class KeywordExtractor:
    """基于 TF-IDF 思想的关键词提取器"""

    STOP_WORDS = {
        '的', '了', '在', '是', '我', '有', '和', '就', '不', '人', '都', '一',
        '一个', '上', '也', '很', '到', '说', '要', '去', '你', '会', '着',
        '没有', '看', '好', '自己', '这', '他', '她', '它', '们', '那',
        'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
        'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
        'should', 'may', 'might', 'shall', 'can', 'need', 'dare', 'ought',
        'used', 'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by', 'from',
        'as', 'into', 'through', 'during', 'before', 'after', 'above',
        'below', 'between', 'under', 'again', 'further', 'then', 'once',
        'here', 'there', 'when', 'where', 'why', 'how', 'all', 'each',
        'few', 'more', 'most', 'other', 'some', 'such', 'no', 'nor', 'not',
        'only', 'own', 'same', 'so', 'than', 'too', 'very', 'just', 'because',
        'but', 'and', 'or', 'if', 'while', 'about', 'against', 'this', 'that',
        'these', 'those', 'i', 'me', 'my', 'myself', 'we', 'our', 'ours',
        'you', 'your', 'yours', 'he', 'him', 'his', 'she', 'her', 'hers',
        'it', 'its', 'they', 'them', 'their', 'what', 'which', 'who', 'whom'
    }

    def __init__(self, max_tags: int = 5):
        self.max_tags = max_tags

    def extract_tags(self, text: str, title: str = "") -> List[str]:
        """
        从文本中提取关键词作为标签

        Args:
            text: 文章正文
            title: 文章标题（权重更高）

        Returns:
            List[str]: 提取的标签列表
        """
        word_freq = Counter()

        title_words = self._tokenize(title)
        for word in title_words:
            if word not in self.STOP_WORDS and len(word) > 1:
                word_freq[word] += 3

        body_words = self._tokenize(text[:5000])
        for word in body_words:
            if word not in self.STOP_WORDS and len(word) > 1:
                word_freq[word] += 1

        tags = [word for word, _ in word_freq.most_common(self.max_tags * 2)]

        tags = self._filter_and_rank(tags, text)

        return tags[:self.max_tags]

    def _tokenize(self, text: str) -> List[str]:
        """简单的分词处理"""
        text = text.lower()
        text = re.sub(r'[^\w\s\u4e00-\u9fff]', ' ', text)

        words = []
        for word in text.split():
            if '\u4e00' <= word[0] <= '\u9fff':
                words.extend(self._split_chinese(word))
            else:
                words.append(word)

        return words

    def _split_chinese(self, text: str) -> List[str]:
        """中文分词 - 基于 n-gram 的简单实现"""
        words = []
        for i in range(len(text)):
            for n in [2, 3, 4]:
                if i + n <= len(text):
                    word = text[i:i + n]
                    if not word.isdigit() and not word.isascii():
                        words.append(word)
        return words

    def _filter_and_rank(self, candidates: List[str], text: str) -> List[str]:
        """过滤和排序候选标签"""
        text_lower = text.lower()
        scored = []

        for tag in candidates:
            score = 0
            if tag in text_lower:
                score = text_lower.count(tag)
            if score > 0:
                scored.append((tag, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [tag for tag, _ in scored]


class TagManager:
    """标签管理器 - 管理所有文章的标签"""

    def __init__(self):
        self._extractor = KeywordExtractor()
        self._file_tags: Dict[str, List[str]] = {}
        self._tag_index: Dict[str, List[str]] = {}

    def generate_tags(self, file_path: str, content: str, title: str = "") -> List[str]:
        """为文件生成标签"""
        tags = self._extractor.extract_tags(content, title)
        self._file_tags[file_path] = tags

        for tag in tags:
            if tag not in self._tag_index:
                self._tag_index[tag] = []
            if file_path not in self._tag_index[tag]:
                self._tag_index[tag].append(file_path)

        return tags

    def get_tags_for_file(self, file_path: str) -> List[str]:
        """获取文件的标签"""
        return self._file_tags.get(file_path, [])

    def get_files_by_tag(self, tag: str) -> List[str]:
        """获取包含指定标签的文件列表"""
        return self._tag_index.get(tag, [])

    def get_all_tags(self) -> List[str]:
        """获取所有标签"""
        return sorted(self._tag_index.keys())

    def get_tag_counts(self) -> Dict[str, int]:
        """获取每个标签的文件数量"""
        return {tag: len(files) for tag, files in self._tag_index.items()}

    def remove_file(self, file_path: str):
        """移除文件的标签记录"""
        tags = self._file_tags.pop(file_path, [])
        for tag in tags:
            if tag in self._tag_index:
                self._tag_index[tag] = [
                    f for f in self._tag_index[tag] if f != file_path
                ]
                if not self._tag_index[tag]:
                    del self._tag_index[tag]
