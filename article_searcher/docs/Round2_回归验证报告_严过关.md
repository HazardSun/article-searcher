# Round 2 回归验证报告 — QA 工程师 严过关（Yan）

- **项目根目录**：`C:\Users\sunxi\Documents\opencode\本地文章整理\article_searcher`
- **执行命令**：`venv\Scripts\python.exe tests/run_all.py`
- **验证时间**：Round 2（SOP 上限 2 轮，本轮为第 2 轮，也是最终轮）
- **数据来源**：全部为真实运行结果（非估算）

---

## 一、Round 2 测试结果（真实运行）

| 指标 | 数值 |
|------|------|
| 测试套件数 | **28** |
| 用例 / 检查数 | **302** |
| 通过 | **302** |
| 失败 (failures) | **0** |
| 错误 (errors) | **0** |
| 耗时 | 32.4 s |
| 退出码 | 0 |
| 最终结果 | ✅ **全部通过** |

> 说明：`run_all.py` 对每个套件仅打印尾部 18 行（`out.strip().splitlines()[-18:]`），因此个别用例名未出现在汇总日志中，但套件计数（`Ran N tests`）与用例总数（302）均以真实子进程输出为准。对落于尾部窗口之外的目标用例，已通过 `python -m unittest` 直接复跑确认（见第三节）。

---

## 二、3 处修复复核结论

### Bug #1 — `engine._eval_bool_path` 空 `allowed` 集判空
**位置**：`core/engine.py` L820、L833-835，并贯穿 keyword/hybrid 分支（L849-851、L862-864）。

- **修复内容**：`allowed = self._eval_tag_path_expr(expr)` 返回 `None`（无文件约束）或 `set`（文件级约束）。构造 `filter_metadata` 时用 `if allowed is not None` 区分：
  - `allowed is None` → `filter_metadata=None`（无约束，全量检索）；
  - `allowed == set()`（空集）→ `filter_metadata={"file_path": []}`，向量库按空列表过滤 → **返回空集**；
  - `allowed` 非空 → 正常约束。
- **与 Round 1 建议一致性**：✅ 一致。空集不再被误判为「无约束」，从根上消除 Bug#1（空交集仍返回命中文件）。
- **旧路径未破坏**：`None` 分支保持原「无 filter」行为；`_eval_tag_path_expr` 的 `AndNode` 在任一子约束无命中时显式 `return set()`（L945-946、L953-954），语义闭合。

### Bug #2 — `_eval_text_bool` 文件级 `NotNode` 放行
**位置**：`core/engine.py` `_is_file_level`（L971-987）、`_eval_text_bool`（L1005-1012）。

- **修复内容**：`_eval_text_bool` 遇到 `NotNode` 时，若 `_is_file_level(node)` 为真（即包裹的是 `TagNode`/`PathNode` 及其 `And/Or` 组合），**直接 `return results`**（放行），因为该否定已由 `allowed` 在 `_eval_tag_path_expr` 中算过补集；仅对内容级（`TermNode`）否定执行「不命中则保留」的后过滤。
- **与 Round 1 建议一致性**：✅ 一致。文件级否定不再在内容级二次清空结果。
- **旧路径未破坏**：纯文本否定（`-排除词` / `NOT 词`）走 `TermNode` 分支，仍按内容否定过滤；`TagNode/PathNode` 在 `_eval_text_bool` 中本就视为 `True`（文件级已过滤）。

### Bug #3 — `parse_query` 否定词重拼 `clean_query`
**位置**：`core/query_parser.py` L209-216（重拼逻辑）、`_collect_plain_terms`（L459-477）。

- **修复内容**：在 `expr, has_boolean = _build_bool_expr(raw)` 之后，仅当 `is_valid and has_boolean` 且 `_collect_plain_terms(expr)` 非空时，用「非否定 `TermNode` 文本」重拼 `clean_query`；否定子树（`NotNode`）一律不贡献正向检索词（L470）。
- **与 Round 1 建议一致性**：✅ 一致，落实设计 §8 契约（`clean_query` = 所有非否定 `TermNode` 文本拼接；否定词仅作后过滤）。
- **旧路径未破坏**：守卫 `if is_valid and has_boolean` 确保纯文本 / 文本级 `-排除词` 查询**不触发重拼**，`clean_query` 保持原扁平解析结果（见下验证）。
- **实测 `clean_query` 行为**（直接运行）：
  - `深度学习 NOT 广告` → `'深度学习'`（has_boolean=True）✅ 否定词已剔除
  - `深度学习 NOT "广告营销"` → `'深度学习'` ✅ 否定短语已剔除
  - `深度学习 -广告` → `'深度学习'`（has_boolean=False）✅ 旧文本级排除路径不变
  - `Python 教程` → `'Python 教程'`（has_boolean=False）✅ 纯文本不变
  - `深度学习 (tag:A AND tag:B)` → `'深度学习'` ✅ 正向词保留、标签不进检索文本
  - `苹果 OR (tag:A OR tag:B)` → `'苹果'` ✅

**复核结论**：三处修复均与 Round 1 建议一致，且未破坏旧有非布尔 / 文本级排除路径。

---

## 三、5 个原失败用例现状（Round 1 → Round 2）

> 以下 5 个用例均已在 Round 2 实际运行并 **`... ok`**（部分因 `run_all.py` 尾部截断未显示于汇总日志，已用 `python -m unittest` 单独复跑取证）。

| # | 用例（所属类） | Round 1 | Round 2 | 证据 |
|---|---------------|---------|---------|------|
| 1 | `test_not_paren_excludes_union`（`TestBooleanParenAndPrecedence`） | 失败 | ✅ 通过 | 直接复跑 `... ok` |
| 2 | `test_negated_tag_partial_exclusion_still_works`（`TestBooleanEmptyAllowed`） | 失败 | ✅ 通过 | 直接复跑 `... ok` |
| 3 | `test_empty_intersection_with_query_returns_empty`（`TestBooleanEmptyAllowed`） | 失败 | ✅ 通过 | 直接复跑 `... ok` |
| 4 | `test_negated_term_excluded_from_clean_query`（`TestCleanQueryContract`） | 失败 | ✅ 通过 | 汇总日志尾部 `... ok` |
| 5 | `test_negated_phrase_excluded_from_clean_query`（`TestCleanQueryContract`） | 失败 | ✅ 通过 | 汇总日志尾部 `... ok` |

单独复跑输出（权威）：
```
test_not_paren_excludes_union ... ok
test_negated_tag_partial_exclusion_still_works ... ok
test_empty_intersection_with_query_returns_empty ... ok
test_negated_term_excluded_from_clean_query ... ok
test_negated_phrase_excluded_from_clean_query ... ok
Ran 5 tests in 1.233s — OK
```

**结论**：Round 1 全部 5 个失败用例在 Round 2 均已修复并通过。

---

## 四、智能路由判定

**判定：NoOne（无需再派发）** ✅

- 全量回归 **302/302 通过，0 失败 0 错误**；
- 工程师寇豆码自报的「302 用例全绿」与真实运行结果**一致**；
- 3 处源码修复经逐行复核，与 Round 1 建议一致，且旧路径未被破坏；
- 5 个原失败用例全部转正。

> 本轮为 SOP 规定的第 2 轮（上限 2 轮）。由于全绿，无需触发「第 3 轮」或 Engineer 二次反馈。

---

## 五、遗留问题 / 观察项（非阻塞，不计入失败）

1. **【轻微 · 非回归】** 纯布尔浏览查询（如 `NOT (tag:A OR tag:B)`，无任何正向检索词）经解析后 `clean_query` 仍残留括号噪声（实测 `'(tag:A'`）。
   - 成因：Bug#3 重拼逻辑仅在「存在非否定 `TermNode`」时生效；此场景下 `plain_terms=[]`，故沿用旧扁平解析的 `clean_query`，而 `(` `)` 未被排除出 `clean_parts`（此为修复前即存在的分词行为，非本次修复引入）。
   - 影响：因 `filter_metadata=allowed` 已对结果集做文件级约束，且此类查询在 `_eval_bool_path` 中实际走受约束的检索/浏览，最终结果仍正确（该场景对应用例 `test_not_paren_excludes_union` 已通过）。属「语义洁净度」层面的轻微瑕疵，**不构成功能缺陷、不导致任何测试失败**。
   - 建议（可选后续优化）：在 `_collect_plain_terms` 为空且 `has_boolean` 时，将 `clean_query` 显式置空以走纯浏览模式，减少无关 token 进入向量检索。

2. **【已覆盖 · 无遗漏】** 第 1 轮未提出、但需留意：文本级 `-排除词` 路径（无 `has_boolean`）仍依赖旧 `exclude_terms` 后过滤，未在本次三处修复范围内，但相关回归用例（`test_query_parser` 等）在 302 全量中一并通过，未见退化。

---

## 六、最终结论

**Round 2 验证通过，可放行。** 工程师三处布尔路径修复有效，全量 302 用例绿灯，5 个历史失败用例全部转正，旧路径无回归。路由至 **NoOne**，无需进一步返工。上述第 1 项观察项作为可选优化建议记录，不影响本次交付质量门禁。
