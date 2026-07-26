#!/usr/bin/env python3
"""
统一测试运行器：综合运行全部测试并输出汇总报告。

设计要点：
- 每个测试文件在【独立子进程】中运行，避免 test_core_logic_extra.py
  在导入时向 sys.modules 注入的“假 onnxruntime”污染其它测试进程
  （否则会破坏 test_comprehensive 中真实 ONNX 引擎的加载）。
- 覆盖：
    tests/test_core_logic.py        （检查式脚本，26 项 check）
    tests/test_core_logic_extra.py  （DeviceManager/Config/RRF/BM25 单元）
    tests/test_engine_behavior.py   （引擎高危修复行为验证）
    tests/test_comprehensive.py     （真实 DML 推理/ChromaDB/端到端/GUI 冒烟）

最终打印 PASS/FAIL 汇总并以退出码反映整体结果。
"""

import os
import re
import sys
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.join(ROOT, "tests")
PYTHON = sys.executable

# (显示名, 运行方式, 解析函数)
CASES = [
    ("检查式脚本 test_core_logic.py",
     [PYTHON, "tests/test_core_logic.py"], "script"),
    ("单元 test_core_logic_extra.py",
     [PYTHON, "-m", "unittest", "tests.test_core_logic_extra", "-v"], "unittest"),
    ("行为 test_engine_behavior.py",
     [PYTHON, "-m", "unittest", "tests.test_engine_behavior", "-v"], "unittest"),
    ("综合 test_comprehensive.py",
     [PYTHON, "-m", "unittest", "tests.test_comprehensive", "-v"], "unittest"),
    ("P0 解析器 test_query_parser.py",
     [PYTHON, "-m", "unittest", "tests.test_query_parser", "-v"], "unittest"),
    ("P0 导出 test_exporter.py",
     [PYTHON, "-m", "unittest", "tests.test_exporter", "-v"], "unittest"),
    ("P0 相似推荐 test_related.py",
     [PYTHON, "-m", "unittest", "tests.test_related", "-v"], "unittest"),
    ("P0 search 过滤 test_search_filters.py",
     [PYTHON, "-m", "unittest", "tests.test_search_filters", "-v"], "unittest"),
    # —— P1 七项功能测试套件（增量架构设计 P1）——
    ("P1 多源与排除 test_multisource",
     [PYTHON, "-m", "unittest", "tests.test_multisource", "-v"], "unittest"),
    ("P1 自动索引 test_watcher",
     [PYTHON, "-m", "unittest", "tests.test_watcher", "-v"], "unittest"),
    ("P1 语义聚簇 test_clustering",
     [PYTHON, "-m", "unittest", "tests.test_clustering", "-v"], "unittest"),
    ("P1 星标 test_star",
     [PYTHON, "-m", "unittest", "tests.test_star", "-v"], "unittest"),
    ("P1 备份恢复 test_backup",
     [PYTHON, "-m", "unittest", "tests.test_backup", "-v"], "unittest"),
    # —— P2 两项功能测试套件（跨文件链接图谱 + 近似/重复检测）——
    ("P2 链接图谱 test_link_graph",
     [PYTHON, "-m", "unittest", "tests.test_link_graph", "-v"], "unittest"),
    ("P2 重复检测 test_dedup",
     [PYTHON, "-m", "unittest", "tests.test_dedup", "-v"], "unittest"),
    ("P2 补充 test_link_graph_extra",
     [PYTHON, "-m", "unittest", "tests.test_link_graph_extra", "-v"], "unittest"),
    # —— UI 回归冒烟（功能11 管理索引源对话框崩溃修复）——
    ("UI 冒烟 test_sources_dialog_smoke",
     [PYTHON, "-m", "unittest", "tests.test_sources_dialog_smoke", "-v"], "unittest"),
    # —— UI 修复独立回归验证（QSize 导入 / _size_item 新签名 / closeEvent / .get 健壮）——
    ("UI 回归 test_duplicate_dialog_ui",
     [PYTHON, "-m", "unittest", "tests.test_duplicate_dialog_ui", "-v"], "unittest"),
    ("UI 回归 test_link_graph_panel_ui",
     [PYTHON, "-m", "unittest", "tests.test_link_graph_panel_ui", "-v"], "unittest"),
    ("UI 回归 test_mainwindow_close",
     [PYTHON, "-m", "unittest", "tests.test_mainwindow_close", "-v"], "unittest"),
    ("UI 回归 test_status_get",
     [PYTHON, "-m", "unittest", "tests.test_status_get", "-v"], "unittest"),
    # —— UI 渲染打磨回归（UI-P1 按钮文字裁切 / UI-P2 思维导图缩放 tooltip）——
    ("UI 打磨 test_ui_polish",
     [PYTHON, "-m", "unittest", "tests.test_ui_polish", "-v"], "unittest"),
    # —— 增量架构设计（P2-1 / P1-1 / P0-3 / P0-2 / P0-1）测试套件 ——
    ("T01 配置 test_config_priority",
     [PYTHON, "-m", "unittest", "tests.test_config_priority", "-v"], "unittest"),
    ("T02 布尔 test_boolean_query",
     [PYTHON, "-m", "unittest", "tests.test_boolean_query", "-v"], "unittest"),
    ("T03 多标签筛选 test_tag_filter_ui",
     [PYTHON, "-m", "unittest", "tests.test_tag_filter_ui", "-v"], "unittest"),
    ("T04 结果分组 test_result_grouping",
     [PYTHON, "-m", "unittest", "tests.test_result_grouping", "-v"], "unittest"),
    ("T05 帮助浮层 test_help_overlay",
     [PYTHON, "-m", "unittest", "tests.test_help_overlay", "-v"], "unittest"),
    # —— QA 严过关 独立边界强化（括号/优先级/NOT 组合/空 allowed/空标签/空分组）——
    ("QA 边界 test_qa_boundary",
     [PYTHON, "-m", "unittest", "tests.test_qa_boundary", "-v"], "unittest"),
]


def parse_script(stdout, returncode):
    m = re.search(r"=== (\d+)/(\d+) 通过 ===", stdout)
    passed = failed = 0
    if m:
        passed = int(m.group(1))
        failed = int(m.group(2)) - passed
    return passed, failed, 0 if returncode == 0 else (failed or 1)


def parse_unittest(stdout, stderr):
    text = (stdout or "") + "\n" + (stderr or "")
    ran_m = re.search(r"Ran (\d+) tests?", text)
    ran = int(ran_m.group(1)) if ran_m else 0
    failed = errored = 0
    sm = re.search(r"FAILED \(([^)]*)\)", text)
    if sm:
        inner = sm.group(1)
        fm = re.search(r"failures=(\d+)", inner)
        em = re.search(r"errors=(\d+)", inner)
        failed = int(fm.group(1)) if fm else 0
        errored = int(em.group(1)) if em else 0
    ok = ("OK" in text) and ("FAILED" not in text) and failed == 0 and errored == 0
    return ran, failed, errored


def run_case(name, cmd, kind):
    print("=" * 72)
    print(f"[RUN] {name}")
    print("=" * 72)
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    proc = subprocess.run(
        cmd, cwd=ROOT, env=env,
        capture_output=True, text=True, errors="replace",
    )
    out = proc.stdout
    if proc.stderr:
        out += "\n[stderr]\n" + proc.stderr
    # 仅打印摘要（避免刷屏），保留尾部关键信息
    tail = "\n".join(out.strip().splitlines()[-18:])
    print(tail)

    if kind == "script":
        p, f, e = parse_script(proc.stdout, proc.returncode)
        ok = proc.returncode == 0
        print(f"-> {'通过' if ok else '失败'} (通过 {p}/{p+f})\n")
        return ok, p + f, f, e
    else:
        ran, f, e = parse_unittest(proc.stdout, proc.stderr)
        ok = (f == 0 and e == 0)
        print(f"-> {'通过' if ok else '失败'} (用例 {ran}, 失败 {f}, 错误 {e})\n")
        return ok, ran, f, e


def main():
    import time
    t0 = time.time()
    total_cases = 0
    total_tests = 0
    total_failed = 0
    total_errored = 0
    all_ok = True

    for name, cmd, kind in CASES:
        ok, n, f, e = run_case(name, cmd, kind)
        total_cases += 1
        total_tests += n
        total_failed += f
        total_errored += e
        all_ok = all_ok and ok

    elapsed = time.time() - t0
    print("=" * 72)
    print("汇总报告")
    print("=" * 72)
    print(f"  测试套件数 : {total_cases}")
    print(f"  用例/检查数: {total_tests}")
    print(f"  失败        : {total_failed}")
    print(f"  错误        : {total_errored}")
    print(f"  耗时        : {elapsed:.1f}s")
    print("=" * 72)
    print("最终结果:", "✅ 全部通过" if all_ok else "❌ 存在失败")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
