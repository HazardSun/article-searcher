"""
应用配置管理
将用户偏好（主题、设备、模型、搜索模式、切片参数、上次打开的文件夹等）
持久化到 JSON 文件，实现设置的跨会话保留。
"""

import os
import json
import logging
from dataclasses import dataclass, asdict, field
from typing import Optional, List

logger = logging.getLogger(__name__)

# 搜索历史持久化上限（去重后保留最近 N 条）
MAX_RECENT_SEARCHES = 50


@dataclass
class AppConfig:
    last_folder: str = ""                       # 上次索引的文件夹
    theme: str = "dark"                         # 'dark' | 'light'
    device: str = "auto"                        # 'auto' 或具体设备 key
    model: str = "BAAI/bge-small-zh-v1.5"       # Embedding 模型
    top_k: int = 10                            # 默认返回结果数
    search_mode: str = "hybrid"                # 'semantic' | 'keyword' | 'hybrid'
    chunk_max: int = 800                        # 切片最大长度
    chunk_overlap: int = 100                    # 切片重叠长度
    batch_size: int = 32                        # 编码批大小
    priority: str = "gpu,cpu"                   # 设备优先级（与 device_manager.DEFAULT_PRIORITY 一致）
    window_geometry: Optional[dict] = None      # 窗口尺寸/位置
    recent_searches: List[str] = field(default_factory=list)  # 搜索历史（上限50，去重，最近在前）
    # —— P1 多源 / 自动索引 / 聚簇 字段（旧配置缺省时自动兼容）——
    index_sources: List[dict] = field(default_factory=list)   # 多索引源：[{path, exclude_rules, enabled}]
    auto_index_enabled: bool = False             # 文件监听自动索引（默认关闭）
    auto_index_debounce_ms: int = 1500           # 防抖时长
    cluster_enabled: bool = True                 # 左栏"主题簇"分组是否展示
    cluster_auto: bool = False                   # 索引完成后是否自动聚类（默认手动"重新聚类"）

    def to_dict(self) -> dict:
        return asdict(self)


class ConfigStore:
    """配置读写（带默认值与字段过滤）"""

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "config.json")
        self.config = self._load()

    def _load(self) -> AppConfig:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            valid = {k: v for k, v in data.items()
                     if k in AppConfig.__dataclass_fields__}
            return AppConfig(**valid)
        except FileNotFoundError:
            return AppConfig()
        except Exception as e:
            logger.warning("配置加载失败，使用默认配置: %s", e)
            return AppConfig()

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.config.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("配置保存失败: %s", e)

    def update(self, **kwargs):
        changed = False
        for k, v in kwargs.items():
            if hasattr(self.config, k) and getattr(self.config, k) != v:
                setattr(self.config, k, v)
                changed = True
        if changed:
            self.save()
        return changed

    def get(self, key: str, default=None):
        return getattr(self.config, key, default)

    def push_recent_search(self, q: str):
        """将一条搜索词压入历史：去重（精确匹配）+ 截断到上限 50 + 落盘。

        新词放在最前面（最近优先）。空字符串不记录。
        """
        q = (q or "").strip()
        if not q:
            return
        lst = list(self.config.recent_searches)
        # 去重：移除已有相同项（保留即将插入的置顶位置）
        lst = [x for x in lst if x != q]
        lst.insert(0, q)
        if len(lst) > MAX_RECENT_SEARCHES:
            lst = lst[:MAX_RECENT_SEARCHES]
        self.config.recent_searches = lst
        self.save()
