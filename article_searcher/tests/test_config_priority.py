"""
配置设备优先级回归测试（T01 / P2-1）

验证 AppConfig.priority 默认值已与 device_manager.DEFAULT_PRIORITY 对齐为 "gpu,cpu"。
运行: python tests/test_config_priority.py
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.config import AppConfig


class TestConfigPriority(unittest.TestCase):

    def test_default_priority_is_gpu_cpu(self):
        self.assertEqual(AppConfig().priority, "gpu,cpu")

    def test_explicit_priority_overrides_default(self):
        self.assertEqual(
            AppConfig(priority="npu,gpu,cpu").priority, "npu,gpu,cpu")

    def test_other_defaults_unchanged(self):
        cfg = AppConfig()
        self.assertEqual(cfg.theme, "dark")
        self.assertEqual(cfg.search_mode, "hybrid")
        self.assertEqual(cfg.top_k, 10)


if __name__ == "__main__":
    unittest.main()
