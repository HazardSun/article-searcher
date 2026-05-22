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
        handlers.append(
            logging.FileHandler(log_file, encoding='utf-8')
        )
    except Exception:
        pass

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=handlers
    )


def main():
    """应用入口"""
    data_dir = get_data_dir()
    setup_logging(data_dir)

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("智能文章整理与语义检索")
    app.setOrganizationName("ArticleSearcher")

    default_font = QFont("Segoe UI", 10)
    default_font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    app.setFont(default_font)

    data_dir = get_data_dir()
    db_path = os.path.join(data_dir, "chromadb")
    model_cache = os.path.join(data_dir, "models")

    logger.info(f"Data directory: {data_dir}")
    logger.info(f"Database path: {db_path}")

    engine = ArticleSearchEngine(
        db_path=db_path,
        model_cache_dir=model_cache
    )

    hw_info = engine.embedding_engine.hardware_info
    logger.info(f"Hardware: CPU cores={hw_info.get('cpu_cores')}, "
                f"Device={hw_info.get('device')}, "
                f"Acceleration={hw_info.get('acceleration')}")

    window = MainWindow(engine)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
