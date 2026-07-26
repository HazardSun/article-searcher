"""
智能文章整理与语义检索系统
主入口
"""

import sys
import os
import logging
from pathlib import Path

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from core.engine import ArticleSearchEngine
from core.config import ConfigStore
from ui.main_window import MainWindow

logger = logging.getLogger(__name__)


def get_data_dir() -> str:
    """获取数据目录路径"""
    data_dir = os.path.join(
        os.path.expanduser("~"),
        ".cache",
        "article_searcher"
    )
    os.makedirs(data_dir, exist_ok=True)
    return data_dir


def setup_logging(data_dir: str):
    """配置日志系统"""
    log_file = os.path.join(data_dir, "app.log")
    handlers = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    except Exception:
        pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=handlers,
    )


def _run_selftest(data_dir: str) -> int:
    """
    无头自检（由环境变量 AS_SELFTEST=1 触发）：
    在「真实冻结环境」中走完 建引擎 -> 索引示例文档 -> 混合检索 全流程，
    验证 chromadb 与 onnxruntime 在同一进程内共存不崩溃。
    返回 0 表示通过，非 0 表示失败。仅用于打包后的冒烟验证，不进入正常 UI 流程。
    """
    import tempfile
    import json
    import time
    from pathlib import Path

    tmp = tempfile.mkdtemp(prefix="as_selftest_")
    db = os.path.join(tmp, "chromadb")
    model_cache = os.path.join(data_dir, "models")
    folder = os.path.join(tmp, "docs")
    os.makedirs(folder, exist_ok=True)
    samples = {
        "ml.md": "# 机器学习\n深度学习是机器学习的重要分支，神经网络是其核心模型。\n",
        "cook.md": "# 烹饪\n今天教大家做苹果派，简单又美味的一道家常菜。\n",
        "hist.md": "# 历史\n唐朝是中国历史上最强盛的朝代之一，长安城繁华无比。\n",
    }
    for name, text in samples.items():
        Path(os.path.join(folder, name)).write_text(text, encoding="utf-8")

    t0 = time.time()
    engine = ArticleSearchEngine(
        db_path=db, model_cache_dir=model_cache,
        embedding_model="BAAI/bge-small-zh-v1.5", device="cpu",
        search_mode="hybrid",
    )
    logger.info("[selftest] 引擎构造 %.1fs", time.time() - t0)

    t0 = time.time()
    engine.load_folder(folder, incremental=True)
    logger.info("[selftest] 索引完成 %.1fs", time.time() - t0)

    t0 = time.time()
    res = engine.search("神经网络 深度学习", mode="hybrid", top_k=3)
    logger.info("[selftest] 检索耗时 %.1fs, 命中 %d", time.time() - t0, len(res))

    ok = len(res) > 0 and any(
        "机器学习" in (r.get("metadata", {}).get("file_name", "")) or
        "ml" in (r.get("metadata", {}).get("file_path", ""))
        for r in res
    )
    # 关键词模式（不调用语义编码）也应能命中
    kw = engine.search("苹果派", mode="keyword", top_k=3)
    ok = ok and len(kw) > 0

    # DML（核显加速）路径验证：若设备支持 DML，确认冻结环境下可用
    dml_ok = None
    try:
        from core.embedding import EmbeddingEngine
        dm = EmbeddingEngine(cache_dir=model_cache,
                             device="dml:0")
        dv = dm.encode_single("DML 路径自检")
        dml_ok = (len(dv) > 0, dm.actual_device)
        logger.info("[selftest] DML 自检: dim=%d provider=%s", len(dv), dm.actual_device)
    except Exception as e:
        dml_ok = (False, str(e))
        logger.warning("[selftest] DML 自检失败: %s", e)

    print(json.dumps({"selftest": "pass" if ok else "fail",
                      "hybrid_hits": len(res), "keyword_hits": len(kw),
                      "dml": (dml_ok[0] if isinstance(dml_ok, tuple) else None),
                      "dml_provider": (dml_ok[1] if isinstance(dml_ok, tuple) else None)},
                     ensure_ascii=False))
    return 0 if ok else 2


def main():
    """应用入口"""
    data_dir = get_data_dir()
    setup_logging(data_dir)

    # 打包后冒烟自检：AS_SELFTEST=1 时只跑无头流程并退出，不启动 GUI
    if os.environ.get("AS_SELFTEST") == "1":
        sys.exit(_run_selftest(data_dir))

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("智能文章整理与语义检索")
    app.setOrganizationName("ArticleSearcher")

    default_font = QFont("Segoe UI", 10)
    default_font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    app.setFont(default_font)

    config_store = ConfigStore(data_dir)
    cfg = config_store.config

    db_path = os.path.join(data_dir, "chromadb")
    model_cache = os.path.join(data_dir, "models")

    logger.info(f"Data directory: {data_dir}")
    logger.info(f"Database path: {db_path}")

    engine = ArticleSearchEngine(
        db_path=db_path,
        model_cache_dir=model_cache,
        embedding_model=cfg.model,
        device=cfg.device,
        chunk_max=cfg.chunk_max,
        chunk_overlap=cfg.chunk_overlap,
        search_mode=cfg.search_mode,
        batch_size=cfg.batch_size,
        priority=cfg.priority,
    )

    hw_info = engine.embedding_engine.hardware_info
    logger.info(
        "Hardware: CPU cores=%s, Devices=%s, Recommended=%s",
        hw_info.get("cpu_cores"),
        [d["key"] for d in hw_info.get("devices", [])],
        hw_info.get("recommended"),
    )

    window = MainWindow(engine, config_store)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
