"""
Extra bounded QA tests for core logic (no torch / chromadb / PyQt6 required).

Covers:
  Group 1: DeviceManager.onnx_providers (provider ordering & exclusion) + recommended()
  Group 2: ConfigStore round-trip / missing-file / corrupt-JSON
  Group 3: rrf() fusion edge cases
  Group 4: LexicalIndex (BM25) add / ranking / remove_file / save-load

API names below follow the ACTUAL module signatures (not the task sketch):
  - DeviceManager.onnx_providers  (not "build_provider_list")
  - DeviceManager.recommended     (not "recommend_device")
  - LexicalIndex.remove_file      (not "remove_document")
  - LexicalIndex._save / _load    (auto-invoked; not public save()/load())
"""

import os
import sys
import json
import types
import tempfile
import unittest

# Make `import core.*` work regardless of cwd.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import device_manager as dm_mod
from core.device_manager import DeviceManager, DeviceInfo
from core.config import ConfigStore, AppConfig
from core.lexical import LexicalIndex
from core.search import rrf


# --------------------------------------------------------------------------- #
# Group 1 helper: inject a fake onnxruntime so we can control get_available_providers
# --------------------------------------------------------------------------- #
_fake_ort = types.ModuleType("onnxruntime")
_fake_ort.get_available_providers = lambda: ["CPUExecutionProvider"]
sys.modules["onnxruntime"] = _fake_ort


def set_available_providers(subsets):
    """Control the (faked) onnxruntime available provider list."""
    _fake_ort.get_available_providers = lambda: list(subsets)


def make_manager():
    """DeviceManager with detection skipped; manual device list."""
    dm = DeviceManager(priority="npu,gpu,cpu")
    dm.devices = [
        DeviceInfo("cpu", "CPU", "cpu"),
        DeviceInfo("cuda:0", "RTX 3060", "cuda", 0),
        DeviceInfo("dml:0", "AMD Radeon", "dml", 0),
        DeviceInfo("npu:0", "Intel NPU", "npu", 0),
    ]
    return dm


class _FakeChunk:
    def __init__(self, chunk_id, content, file_path="", file_name="", title=""):
        self.chunk_id = chunk_id
        self.content = content
        self.file_path = file_path
        self.file_name = file_name
        self.title = title


class TestDeviceManager(unittest.TestCase):
    def test_cpu_only_ordering(self):
        dm = make_manager()
        set_available_providers(["CPUExecutionProvider"])
        provs = dm.onnx_providers(dm.get("cpu"))
        self.assertEqual(provs, ["CPUExecutionProvider"])

    def test_cuda_ordering_and_exclusion(self):
        dm = make_manager()
        set_available_providers(["CPUExecutionProvider", "CUDAExecutionProvider"])
        provs = dm.onnx_providers(dm.get("cuda:0"))
        # CUDA first, CPU fallback appended; Dml/OpenVINO excluded
        self.assertEqual(provs[0], ("CUDAExecutionProvider", {"device_id": 0}))
        self.assertEqual(provs[-1], "CPUExecutionProvider")
        self.assertNotIn("DmlExecutionProvider", provs)
        self.assertNotIn("OpenVINOExecutionProvider", provs)

    def test_dml_ordering(self):
        dm = make_manager()
        set_available_providers(["CPUExecutionProvider", "DmlExecutionProvider"])
        provs = dm.onnx_providers(dm.get("dml:0"))
        # DML provider 携带 device_id 与显存上限（device_manager 有意追加 gpu_mem_limit）
        self.assertEqual(provs[0][0], "DmlExecutionProvider")
        self.assertEqual(provs[0][1].get("device_id"), 0)
        self.assertIn("gpu_mem_limit", provs[0][1])
        self.assertEqual(provs[-1], "CPUExecutionProvider")

    def test_unavailable_provider_excluded(self):
        dm = make_manager()
        # Dml requested but NOT available -> must fall back to CPU only
        set_available_providers(["CPUExecutionProvider"])
        provs = dm.onnx_providers(dm.get("dml:0"))
        self.assertNotIn("DmlExecutionProvider", provs)
        self.assertEqual(provs, ["CPUExecutionProvider"])

    def test_openvino_cpu_pref(self):
        dm = make_manager()
        set_available_providers(["CPUExecutionProvider", "OpenVINOExecutionProvider"])
        provs = dm.onnx_providers(dm.get("cpu"))
        self.assertEqual(provs[0], ("OpenVINOExecutionProvider", {}))
        self.assertEqual(provs[-1], "CPUExecutionProvider")

    def test_no_cpu_fallback(self):
        dm = make_manager()
        set_available_providers(["CPUExecutionProvider", "CUDAExecutionProvider"])
        provs = dm.onnx_providers(dm.get("cuda:0"), allow_cpu_fallback=False)
        self.assertEqual(provs, [("CUDAExecutionProvider", {"device_id": 0})])

    def test_recommended_valid_device(self):
        dm = make_manager()
        for prio, expected in [
            ("npu,gpu,cpu", "npu:0"),
            ("gpu,cpu", "cuda:0"),    # gpu matches cuda+dml; cuda is first in self.devices
            ("cuda:0,cpu", "cuda:0"), # exact key form works
            ("cpu", "cpu"),
        ]:
            dev = dm.recommended(priority=prio)
            self.assertIn(dev.key, dm.keys(), "recommended() must return a real device")
            self.assertEqual(dev.key, expected)

    def test_recommended_bare_cuda_gap(self):
        # FIXED: recommended() now treats bare "cuda"/"dml" tokens as kind
        # keywords, so a priority of "cuda,cpu" selects the CUDA device
        # (cuda:0) instead of silently falling through to CPU.
        dm = make_manager()
        dev = dm.recommended(priority="cuda,cpu")
        self.assertEqual(dev.key, "cuda:0")  # fixed behaviour
        dev_dml = dm.recommended(priority="dml,cpu")
        self.assertEqual(dev_dml.key, "dml:0")  # bare dml token also matched


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_round_trip_all_fields(self):
        cs = ConfigStore(self.tmp)
        cs.config.theme = "light"
        cs.config.device = "cuda:0"
        cs.config.model = "my/model"
        cs.config.top_k = 5
        cs.config.search_mode = "keyword"
        cs.config.chunk_max = 500
        cs.config.chunk_overlap = 80
        cs.config.last_folder = "/some/path"
        cs.config.batch_size = 16
        cs.config.priority = "gpu,cpu"
        cs.config.window_geometry = {"x": 1, "y": 2}
        cs.save()

        cs2 = ConfigStore(self.tmp)
        self.assertEqual(cs2.config, cs.config)
        # also verify individual fields
        self.assertEqual(cs2.get("theme"), "light")
        self.assertEqual(cs2.get("window_geometry"), {"x": 1, "y": 2})

    def test_missing_file_uses_defaults(self):
        cs = ConfigStore(self.tmp)  # no config.json written yet
        self.assertEqual(cs.config, AppConfig())

    def test_corrupt_json_graceful(self):
        path = os.path.join(self.tmp, "config.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{ this is : not valid json ]")
        # must not raise
        cs = ConfigStore(self.tmp)
        self.assertEqual(cs.config, AppConfig())


class TestRRF(unittest.TestCase):
    def _mk(self, ids):
        return [{"id": i, "similarity": 1.0 - 0.1 * idx} for idx, i in enumerate(ids)]

    def test_empty_semantic(self):
        out = rrf([], self._mk(["a", "b"]))
        self.assertEqual([r["id"] for r in out], ["a", "b"])
        for r in out:
            self.assertTrue(0 < r["rrf_score"] <= 1)

    def test_empty_lexical(self):
        out = rrf(self._mk(["x", "y"]), [])
        self.assertEqual([r["id"] for r in out], ["x", "y"])

    def test_empty_both(self):
        self.assertEqual(rrf([], []), [])

    def test_duplicate_ids_across_lists(self):
        # 'a' appears rank0 in both -> summed score, should still be present once
        out = rrf(self._mk(["a", "b"]), self._mk(["a", "c"]))
        ids = [r["id"] for r in out]
        self.assertEqual(ids.count("a"), 1)
        a_score = next(r["rrf_score"] for r in out if r["id"] == "a")
        # appears in both lists at rank 0 => 2/(k+1)
        self.assertAlmostEqual(a_score, round(2.0 / 61.0, 6), places=5)

    def test_tied_scores_ordering(self):
        # both 'a' and 'b' only in lexical at rank0/1: distinct scores, ordering kept
        out = rrf([], self._mk(["a", "b", "c"]))
        scores = [r["rrf_score"] for r in out]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_single_result(self):
        out = rrf(self._mk(["only"]), [])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["id"], "only")
        self.assertTrue(0 < out[0]["rrf_score"] <= 1)

    def test_scores_in_range_and_order(self):
        out = rrf(self._mk(["a", "b", "c"]), self._mk(["c", "a", "d"]))
        scores = [r["rrf_score"] for r in out]
        for s in scores:
            self.assertTrue(0 < s <= 1)
        self.assertEqual(scores, sorted(scores, reverse=True))


class TestLexical(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "lex.json")

    def test_add_and_ranking(self):
        lx = LexicalIndex(self.path)
        lx.add_chunks([
            _FakeChunk("d1", "机器学习 神经网络 深度学习", file_path="a.txt", file_name="a", title="A"),
            _FakeChunk("d2", "烹饪 菜谱 美食", file_path="b.txt", file_name="b", title="B"),
            _FakeChunk("d3", "机器学习 模型 训练", file_path="c.txt", file_name="c", title="C"),
        ])
        # query strongly favouring d1
        res = lx.search("神经网络", top_k=10)
        self.assertTrue(res)
        self.assertEqual(res[0]["id"], "d1")
        # query matching d1/d3 but not d2 -> d2 excluded
        res2 = lx.search("机器学习", top_k=10)
        ids = [r["id"] for r in res2]
        self.assertIn("d1", ids)
        self.assertIn("d3", ids)
        self.assertNotIn("d2", ids)
        # d1 and d3 both present, ranked before any non-match (d2 absent)
        self.assertTrue(ids[0] in ("d1", "d3"))

    def test_remove_file_updates_index(self):
        lx = LexicalIndex(self.path)
        lx.add_chunks([
            _FakeChunk("d1", "机器学习 神经网络", file_path="a.txt"),
            _FakeChunk("d2", "深度学习 训练", file_path="a.txt"),
            _FakeChunk("d3", "烹饪 美食", file_path="b.txt"),
        ])
        before = lx.count()
        lx.remove_file("a.txt")
        after = lx.count()
        self.assertEqual(after, before - 2)
        res = lx.search("机器学习", top_k=10)
        self.assertNotIn("d1", [r["id"] for r in res])
        self.assertNotIn("d2", [r["id"] for r in res])
        res3 = lx.search("烹饪", top_k=10)
        self.assertEqual(res3[0]["id"], "d3")

    def test_save_load_reproducible(self):
        lx = LexicalIndex(self.path)
        lx.add_chunks([
            _FakeChunk("d1", "机器学习 神经网络 深度学习", file_path="a.txt"),
            _FakeChunk("d2", "烹饪 菜谱 美食", file_path="b.txt"),
            _FakeChunk("d3", "机器学习 模型 训练 推理", file_path="c.txt"),
        ])
        lx._save()
        # fresh instance loads from same path
        lx2 = LexicalIndex(self.path)
        self.assertEqual(lx2.count(), lx.count())
        r1 = lx.search("机器学习", top_k=10)
        r2 = lx2.search("机器学习", top_k=10)
        self.assertEqual([r["id"] for r in r1], [r["id"] for r in r2])
        self.assertEqual(lx2.df, lx.df)


@unittest.skip("integration test: chromadb / PyQt6 / torch not installed in this env")
class TestIntegration(unittest.TestCase):
    def test_vectorstore_ui_integration(self):
        import chromadb  # noqa
        import PyQt6     # noqa
        import torch     # noqa


class TestRecentSearches(unittest.TestCase):
    """P0-1：recent_searches 持久化、去重、上限 50。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_push_dedup_and_order(self):
        cs = ConfigStore(self.tmp)
        cs.push_recent_search("深度学习")
        cs.push_recent_search("神经网络")
        cs.push_recent_search("深度学习")  # 重复：应置顶去重
        self.assertEqual(cs.config.recent_searches, ["深度学习", "神经网络"])

    def test_empty_not_recorded(self):
        cs = ConfigStore(self.tmp)
        cs.push_recent_search("   ")
        cs.push_recent_search("")
        self.assertEqual(cs.config.recent_searches, [])

    def test_cap_50(self):
        cs = ConfigStore(self.tmp)
        for i in range(60):
            cs.push_recent_search("q%d" % i)
        self.assertEqual(len(cs.config.recent_searches), 50)
        # 最近插入的在最前
        self.assertEqual(cs.config.recent_searches[0], "q59")

    def test_persisted(self):
        cs = ConfigStore(self.tmp)
        cs.push_recent_search("持久化测试")
        cs2 = ConfigStore(self.tmp)
        self.assertIn("持久化测试", cs2.config.recent_searches)


if __name__ == "__main__":
    groups = {
        "Group1 DeviceManager": unittest.TestLoader().loadTestsFromTestCase(TestDeviceManager),
        "Group2 Config": unittest.TestLoader().loadTestsFromTestCase(TestConfig),
        "Group3 RRF": unittest.TestLoader().loadTestsFromTestCase(TestRRF),
        "Group4 Lexical(BM25)": unittest.TestLoader().loadTestsFromTestCase(TestLexical),
        "Integration (skipped)": unittest.TestLoader().loadTestsFromTestCase(TestIntegration),
    }
    total_pass = total_fail = total_skip = 0
    for name, suite in groups.items():
        import io
        stream = io.StringIO()
        res = unittest.TextTestRunner(stream=stream, verbosity=0).run(suite)
        passed = res.testsRun - len(res.failures) - len(res.errors)
        skipped = len(res.skipped)
        failed = len(res.failures) + len(res.errors)
        total_pass += passed
        total_fail += failed
        total_skip += skipped
        status = "OK" if failed == 0 else "FAIL"
        print(f"[{status}] {name}: {passed} passed, {failed} failed, {skipped} skipped")
        if res.failures:
            for t, msg in res.failures:
                print("   FAIL:", t, "\n", msg)
        if res.errors:
            for t, msg in res.errors:
                print("   ERROR:", t, "\n", msg)
    print(f"\nSUMMARY: {total_pass} passed, {total_fail} failed, {total_skip} skipped")
