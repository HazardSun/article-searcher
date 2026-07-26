"""
UI 回归测试共用的「最小引擎」桩。

设计目标（遵循主理人约束：fake engine 只实现被调用方法，
无需真实加载 chromadb / onnx）：

- 仅实现 MainWindow 构造期与受测方法（_update_status / _on_indexing_finished /
  closeEvent）实际访问到的 engine 接口；
- 其余未显式声明的方法通过 __getattr__ 兜底为 no-op，避免构造期因
  某个冷门调用而抛 AttributeError，让测试聚焦于「UI 健壮性」本身；
- 不导入、不实例化 ArticleSearchEngine，因此不会触发 chromadb / onnx 加载。
"""

from types import SimpleNamespace


class _TagManager:
    def get_clusters(self):
        return {}

    def set_clusters(self, clusters):
        return None

    def save(self):
        return None

    def load(self):
        return None


class _Lexical:
    def _load(self):
        return None


class _EmbeddingEngine:
    def __init__(self):
        # _populate_device_combo 会遍历 devices 并访问 .label / .key
        self.devices = [SimpleNamespace(label="CPU", key="cpu")]
        self.model_name = "fake-model"
        self.device = "cpu"
        self.dimension = 0

    def get_status_info(self):
        return {}


class _VectorStore:
    def __init__(self):
        self.db_path = ":memory:"

    def get_embedding_dim(self):
        return 0


class FlexEngine:
    """最小但足够完整的 engine 桩，供 MainWindow UI 回归测试使用。"""

    def __init__(self, status=None):
        self.current_folder = ""
        self.search_mode = "hybrid"
        self.sources = []
        self.embedding_engine = _EmbeddingEngine()
        self.vector_store = _VectorStore()
        self.tag_manager = _TagManager()
        self.lexical = _Lexical()
        self.chunker = SimpleNamespace(config=None)
        # 受测方法 _update_status 读取的 status；测试可注入「缺键」字典
        self._status = status if status is not None else {}

    # —— 显式实现的、对受测路径有意义的接口 —— #
    def get_status(self):
        return self._status

    def get_tag_counts(self):
        return {}

    def list_indexed_files(self):
        # ui/indexed_files_panel.refresh() 对返回值调用 .keys()，故返回 dict
        return {}

    def get_indexed_files(self):
        return {}

    def is_starred_file(self, path):
        return False

    def get_file_content(self, path):
        return ""

    def set_sources(self, sources):
        self.sources = list(sources)

    def set_search_mode(self, mode):
        self.search_mode = mode

    def set_model(self, model, device=None):
        return None

    def set_device(self, device):
        return None

    def __getattr__(self, name):
        # 兜底：任何未显式声明的方法都返回安全的 no-op（返回 None）
        def _noop(*args, **kwargs):
            return None
        return _noop
