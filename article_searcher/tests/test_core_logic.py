"""
核心逻辑的免 GUI / 免模型 单元测试。
运行: python tests/test_core_logic.py
（仅需 psutil / numpy / beautifulsoup4 / lxml / marko，无需 torch / chromadb / onnxruntime）
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.device_manager as dm
from core.device_manager import DeviceManager, DeviceInfo
from core.config import AppConfig, ConfigStore
from core import search as fusion
from core.lexical import LexicalIndex

PASS = []


def check(name, cond):
    PASS.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)


# --------------------------------------------------------------------------- #
# 1) DeviceManager：探测、推荐、ONNX provider 生成
# --------------------------------------------------------------------------- #
def test_device_manager():
    mgr = DeviceManager(priority="npu,gpu,cpu")
    keys = mgr.keys()
    check("DM: 至少包含 cpu", "cpu" in keys)
    check("DM: 推荐设备存在", mgr.recommended() is not None)
    check("DM: 推荐设备属于已探测设备之一", mgr.recommended().key in mgr.keys())

    cpu = mgr.get("cpu")
    # 模拟 onnxruntime 可用提供方
    mgr.available_onnx_providers = lambda: {
        "CPUExecutionProvider", "CUDAExecutionProvider",
        "DmlExecutionProvider", "OpenVINOExecutionProvider",
    }
    cpu_prov = mgr.onnx_providers(cpu)
    check("DM: CPU provider 含 CPUExecutionProvider",
          "CPUExecutionProvider" in cpu_prov)
    check("DM: CPU provider 含 OpenVINO(Intel)",
          any(p[0] == "OpenVINOExecutionProvider" for p in cpu_prov if isinstance(p, tuple)))

    cuda = DeviceInfo("cuda:0", "GeForce RTX", "cuda", 0)
    cuda_prov = mgr.onnx_providers(cuda)
    check("DM: CUDA provider 首项为 CUDA",
          cuda_prov and cuda_prov[0][0] == "CUDAExecutionProvider")
    check("DM: CUDA provider 含 CPU 回退", "CPUExecutionProvider" in cuda_prov)

    dml = DeviceInfo("dml:1", "NPU via DML", "npu", 1)
    dml_prov = mgr.onnx_providers(dml)
    check("DM: NPU/DML provider 首项为 DmlExecutionProvider",
          dml_prov and dml_prov[0][0] == "DmlExecutionProvider")
    check("DM: NPU device_id 透传", dml_prov[0][1]["device_id"] == 1)

    so = mgr.build_session_options()
    check("DM: SessionOptions 生成", so is not None)


# --------------------------------------------------------------------------- #
# 2) ConfigStore：JSON 持久化往返
# --------------------------------------------------------------------------- #
def test_config():
    with tempfile.TemporaryDirectory() as d:
        cs = ConfigStore(d)
        check("CFG: 默认主题 dark", cs.config.theme == "dark")
        cs.update(theme="light", top_k=20, device="cuda:0", search_mode="keyword")
        cs2 = ConfigStore(d)
        check("CFG: 主题已持久化", cs2.config.theme == "light")
        check("CFG: top_k 已持久化", cs2.config.top_k == 20)
        check("CFG: device 已持久化", cs2.config.device == "cuda:0")
        check("CFG: search_mode 已持久化", cs2.config.search_mode == "keyword")


# --------------------------------------------------------------------------- #
# 3) LexicalIndex：BM25 分词 / 检索 / 移除
# --------------------------------------------------------------------------- #
class FakeChunk:
    def __init__(self, cid, fp, fn, title, content):
        self.chunk_id = cid
        self.file_path = fp
        self.file_name = fn
        self.title = title
        self.content = content


def test_lexical():
    with tempfile.TemporaryDirectory() as d:
        idx = LexicalIndex(os.path.join(d, "lex.json"))
        chunks = [
            FakeChunk("a:0", "/x/a.md", "a.md", "Alpha",
                      "深度学习 模型 训练 神经网络 优化"),
            FakeChunk("a:1", "/x/a.md", "a.md", "Alpha",
                      "向量检索 与 语义搜索 的相关技术"),
            FakeChunk("b:0", "/x/b.md", "b.md", "Beta",
                      "烹饪 食谱 美食 与 神经网络 无关的内容"),
        ]
        idx.add_chunks(chunks)
        check("LEX: 索引数量正确", idx.count() == 3)

        res = idx.search("神经网络", top_k=5)
        check("LEX: 检索返回结果", len(res) > 0)
        check("LEX: 命中含'神经网络'的文档",
              any("神经网络" in r["content"] for r in res))
        # '神经网络' 同时出现在 a:0 与 b:0，但 a:0 还有更多相关词
        top = res[0]
        check("LEX: 结果含 metadata.file_path", "file_path" in top["metadata"])

        idx.remove_file("/x/b.md")
        check("LEX: 移除后数量减少", idx.count() == 2)
        res2 = idx.search("烹饪", top_k=5)
        check("LEX: 移除后不再命中被删文档",
              not any("烹饪" in r["content"] for r in res2))

        # 持久化
        idx2 = LexicalIndex(os.path.join(d, "lex.json"))
        check("LEX: 重新加载后数量一致", idx2.count() == 2)


# --------------------------------------------------------------------------- #
# 4) search.rrf：融合排序
# --------------------------------------------------------------------------- #
def test_rrf():
    semantic = [
        {"id": "s1", "similarity": 0.9},
        {"id": "s2", "similarity": 0.8},
        {"id": "s3", "similarity": 0.5},
    ]
    lexical = [
        {"id": "l3", "lexical_score": 5.0},
        {"id": "l1", "lexical_score": 3.0},
        {"id": "l2", "lexical_score": 1.0},
    ]
    fused = fusion.rrf(semantic, lexical, top_n=10)
    check("RRF: 结果数量 = 并集", len(fused) == 6)
    check("RRF: 每条附带 rrf_score", all("rrf_score" in r for r in fused))
    # s1 在 semantic 排第 1，应获得最高 RRF 分
    s1 = next(r for r in fused if r["id"] == "s1")
    check("RRF: semantic 第一名得分最高",
          s1["rrf_score"] >= max(r["rrf_score"] for r in fused))

    top3 = fusion.rrf(semantic, lexical, top_n=3)
    check("RRF: top_n 截断生效", len(top3) == 3)


if __name__ == "__main__":
    test_device_manager()
    test_config()
    test_lexical()
    test_rrf()
    failed = [n for n, ok in PASS if not ok]
    print(f"\n=== {len(PASS)-len(failed)}/{len(PASS)} 通过 ===")
    sys.exit(1 if failed else 0)
