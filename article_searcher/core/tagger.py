"""
智能标签模块
基于文本内容和 Embedding 聚类自动为文章生成标签
"""

import os
import re
import json
import logging
from typing import List, Dict, Optional, Any
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
    """标签管理器 - 管理所有文章的标签（持久化到磁盘）"""

    def __init__(self, save_path: str = None):
        self._extractor = KeywordExtractor()
        self._file_tags: Dict[str, List[str]] = {}
        self._tag_index: Dict[str, List[str]] = {}
        # P1-7 星标：与 file_tags / 簇 三权分立的独立列表
        self._starred: List[str] = []
        # P1-5 语义聚簇：独立字段 {cid: {label, files, sample_titles, centroid}}
        self._clusters: Dict[str, Dict[str, Any]] = {}
        self._save_path = save_path

    # ------------------------------------------------------------------ #
    # 持久化
    # ------------------------------------------------------------------ #
    def save(self):
        """将标签写入磁盘（索引完成后调用，避免逐文件频繁写盘）"""
        if not self._save_path:
            return
        try:
            os.makedirs(os.path.dirname(self._save_path), exist_ok=True)
            with open(self._save_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "file_tags": self._file_tags,
                        "tag_index": self._tag_index,
                        "starred": self._starred,
                        "clusters": self._clusters,
                    },
                    f, ensure_ascii=False,
                )
        except Exception as e:
            logger.warning("标签保存失败: %s", e)

    def load(self):
        """从磁盘恢复标签（重启后标签筛选/展示仍然有效）"""
        if not self._save_path or not os.path.isfile(self._save_path):
            return
        try:
            with open(self._save_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._file_tags = data.get("file_tags", {})
            self._tag_index = data.get("tag_index", {})
            self._starred = data.get("starred", []) or []
            self._clusters = data.get("clusters", {}) or {}
        except Exception as e:
            logger.warning("标签加载失败: %s", e)

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

    # ------------------------------------------------------------------ #
    # 标签写入（供批量操作覆盖写回）
    # ------------------------------------------------------------------ #
    def set_tags_for_file(self, file_path: str, tags: List[str]):
        """直接设定某文件的标签（合并去重后写回 file_tags 与 tag_index）。"""
        tags = list(dict.fromkeys(tags or []))
        old = self._file_tags.get(file_path, [])
        for t in old:
            if t in self._tag_index:
                self._tag_index[t] = [f for f in self._tag_index[t] if f != file_path]
                if not self._tag_index[t]:
                    del self._tag_index[t]
        self._file_tags[file_path] = tags
        for t in tags:
            if t not in self._tag_index:
                self._tag_index[t] = []
            if file_path not in self._tag_index[t]:
                self._tag_index[t].append(file_path)

    # ------------------------------------------------------------------ #
    # 星标（功能7）：独立列表，不参与 tag 过滤
    # ------------------------------------------------------------------ #
    def star(self, file_path: str):
        if file_path not in self._starred:
            self._starred.append(file_path)

    def unstar(self, file_path: str):
        self._starred = [x for x in self._starred if x != file_path]

    def is_starred(self, file_path: str) -> bool:
        return file_path in self._starred

    def get_starred(self) -> List[str]:
        return list(self._starred)

    # ------------------------------------------------------------------ #
    # 语义聚簇（功能5）：独立字段，与 file_tags 物理隔离
    # ------------------------------------------------------------------ #
    def set_clusters(self, clusters: List[Dict[str, Any]]):
        """clusters: 列表或字典，每项含 {id?, label, files, sample_titles, centroid}。
        写入 self._clusters（以 cid 为键）。

        输入兼容：dict / Cluster 对象（含 to_dict()）/ 任意对象（vars()）。
        """
        self._clusters = {}
        if isinstance(clusters, dict):
            clusters = list(clusters.values())
        # 输入规范化：将每个元素统一为 dict，兼容 Cluster 对象（design §3.4
        # cluster_files 返回 List[Cluster]）与既有 dict 输入契约。
        normalized: List[Dict[str, Any]] = []
        for c in clusters:
            if hasattr(c, "to_dict"):
                normalized.append(c.to_dict())
            elif isinstance(c, dict):
                normalized.append(c)
            else:
                normalized.append(vars(c))
        clusters = normalized
        for i, c in enumerate(clusters):
            cid = c.get("id") or f"cluster_{i}"
            self._clusters[cid] = {
                "id": cid,
                "label": c.get("label", f"主题簇 {i + 1}"),
                "files": list(c.get("files", [])),
                "sample_titles": list(c.get("sample_titles", [])),
                "centroid": list(c.get("centroid", [])) if c.get("centroid") is not None else [],
            }

    def get_clusters(self) -> Dict[str, Dict[str, Any]]:
        return self._clusters

    def merge_clusters(self, ids: List[str]) -> Optional[str]:
        """将多个簇合并为一个（保留第一个 id，合并 files / sample_titles 去重）。"""
        targets = [self._clusters[i] for i in ids if i in self._clusters]
        if not targets:
            return None
        base = targets[0]
        files = list(base["files"])
        titles = list(base["sample_titles"])
        for t in targets[1:]:
            for f in t["files"]:
                if f not in files:
                    files.append(f)
            for s in t["sample_titles"]:
                if s not in titles:
                    titles.append(s)
        base["files"] = files
        base["sample_titles"] = titles
        base["label"] = base.get("label", "合并簇")
        keep_id = base["id"]
        for i in ids:
            if i in self._clusters and i != keep_id:
                del self._clusters[i]
        return keep_id

    def dismiss_cluster(self, cid: str):
        """解散单个簇（从 clusters 中移除，不影响 file_tags）。"""
        self._clusters.pop(cid, None)

    def remove_file(self, file_path: str):
        """移除文件的标签记录（同步清理星标 / 簇中的引用）"""
        tags = self._file_tags.pop(file_path, [])
        for tag in tags:
            if tag in self._tag_index:
                self._tag_index[tag] = [
                    f for f in self._tag_index[tag] if f != file_path
                ]
                if not self._tag_index[tag]:
                    del self._tag_index[tag]
        if file_path in self._starred:
            self._starred = [x for x in self._starred if x != file_path]
        for c in self._clusters.values():
            if file_path in c.get("files", []):
                c["files"] = [f for f in c["files"] if f != file_path]
