# ArticleSearcher · 本地文章智能检索

> **当前版本 v1.1** · 本地优先、隐私安全的个人文章 / 知识库**语义检索引擎**。所有数据留在本地，不上传云端；支持语义 + 关键词混合检索、高级布尔语法、多标签组合筛选、结果分组、主题聚类与可视化。

---

## 一、项目简介

ArticleSearcher 是一个 PyQt6 桌面应用，为大量本地 Markdown / 文档建立一个可语义检索的个人知识库：

- **嵌入模型**：`BAAI/bge-small-zh-v1.5`，通过 ONNX Runtime 运行（CPU 或 DirectML 核显加速）。
- **向量库**：ChromaDB（本地持久化）。
- **检索**：混合检索（语义向量 + 关键词 BM25 的 RRF 融合），可按标签、路径、布尔表达式精确过滤。
- **平台**：Windows 10/11 优先；支持 AMD / Intel / NVIDIA 核显或独显的 DirectML 加速。

整个应用**离线可用**，仅在首次使用时从 HuggingFace 下载一次嵌入模型权重（约 130MB）。

---

## 二、核心特性

| 特性 | 说明 |
|------|------|
| 混合检索 | 语义向量 + 关键词（BM25）RRF 融合，召回更准 |
| 高级搜索语法 | `tag:` / `path:` / `-排除` / `"短语"` + `AND` / `OR` / `NOT` / 括号分组 |
| 左栏多标签组合筛选 | 多选标签 + `且/或` 切换，等价于布尔组合 |
| 结果分组 | 扁平 / 按文件 / 按标签 / 按源 四种视图 |
| 应用内帮助 | 顶栏 `?` 或 `?` 键唤起语法速查浮层 |
| 多索引源 | 可挂多个文件夹，每个可设排除规则、启停 |
| 自动索引 | 监听文件变更，防抖后增量重建（默认关闭） |
| 主题簇 | 基于嵌入的自动聚类，可手动"重新聚类" |
| 批量操作 | 选中结果后批量加标签 / 重建索引 / 移除 |
| 可视化 | 思维导图、关系图谱 |
| 导出 | 支持 Markdown / HTML / JSON 等格式 |
| 设置 | 明暗主题、设备、模型、搜索模式、切片参数持久化 |
| GPU 加速 | 自动启用 DirectML（核显/独显），回退 CPU |

---

## 三、系统要求

- **操作系统**：Windows 10 / 11（其他支持 PyQt6 的平台亦可运行源码模式）。
- **运行方式 A（打包版）**：自带 Python 运行时，无需安装。
- **运行方式 B（源码模式）**：Python 3.12 + 虚拟环境。
- **磁盘**：约 2GB（含模型缓存与索引；索引随文档量增长）。
- **可选**：支持 DirectML 的显卡（AMD Radeon / Intel Arc / NVIDIA）以获得嵌入加速；无显卡时自动回退 CPU。

---

## 四、安装与运行

### 方式 A：打包版（推荐，开箱即用）

1. 进入 `dist/ArticleSearcher/` 文件夹。
2. 双击 `ArticleSearcher.exe` 启动。
3. 首次启动会自动下载并缓存嵌入模型（需联网一次），之后完全离线。

> ⚠️ **必须从 `dist/ArticleSearcher/` 文件夹内启动**——该文件夹内含 `_internal` 运行时目录，不要把 `ArticleSearcher.exe` 单独剪切出来，否则无法运行。

### 方式 B：源码模式（开发 / 调试）

```bash
cd <项目根目录>
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
venv\Scripts\python.exe main.py
```

首次启动后需在「管理索引源」（快捷键 `Ctrl+O`）中添加你的文档文件夹并建立索引，才能检索。

---

## 五、使用手册

### 5.1 主界面布局

```
┌──────────────────────────────────────────────────────────────┐
│  搜索框            [分组:扁平|按文件|按标签|按源]  设置  主题  ? │  ← 顶栏
├──────────────┬───────────────────────────────┬───────────────┤
│ 标签筛选      │  搜索结果（可分组渲染）        │ 文档预览 /    │
│ (多选+且/或)  │                              │ 相关文档 /    │
│ 主题簇        │                              │ 关系图        │
│ 索引源        │                              │               │
└──────────────┴───────────────────────────────┴───────────────┘
```

- **顶栏**：搜索框、结果分组切换、设置（`Ctrl+,`）、主题切换（`Ctrl+T`）、帮助（`?`）。
- **左栏**：标签筛选、主题簇、索引源。
- **中栏**：搜索结果列表（支持分组）。
- **右栏**：文档详情预览、相关文档、关系图谱等。

### 5.2 建立索引

1. 快捷键 `Ctrl+O` 打开「管理索引源」。
2. 添加文档文件夹（可填写排除规则，如 `node_modules`、`*.tmp`；可单独启停某个源）。
3. 点击「建立索引」，或随时按 `F5` 重新索引。
4. 如需实时同步：在设置或源管理中开启「自动索引」，文件变更后会在防抖（默认 1500ms）后增量重建。

> 索引状态、进度会显示在底部状态栏。

### 5.3 搜索语法（完整参考）

在搜索框输入查询，支持以下语法（也可按 `?` 在应用内随时查看速查）。

#### 基础过滤

| 语法 | 作用 | 示例 |
|------|------|------|
| `tag:标签` | 按标签过滤（可多个，默认取**交集**） | `tag:技术` |
| `path:路径` | 按路径 / 文件名过滤，支持通配 `*` 与大小写不敏感子串 | `path:笔记` / `path:2024/*.md` |
| `-词` 或 `-"短语"` | **排除**含该词 / 短语的文档（内容级后过滤） | `-广告` / `-"垃圾内容"` |
| `"短语"` | 精确短语，同时参与检索与语义强调 | `"深度学习"` |

#### 布尔组合（AND / OR / NOT / 括号）

优先级（由低到高）：**OR > 隐式 AND > NOT > 原子 / 括号**。

| 语法 | 含义 | 示例 |
|------|------|------|
| `词A 词B` | 隐式 AND（相邻词默认求交） | `机器学习 神经网络` |
| `A OR B` | 并集（任一命中） | `tag:技术 OR tag:教程` |
| `A NOT B` | 含 A 且不含 B | `tag:技术 NOT tag:广告` |
| `-tag:B` | 排除带 B 标签的文档 | `-tag:广告` |
| `A OR B`（短语） | 短语二选一 | `"深度学习" OR "神经网络"` |
| `A NOT B`（词） | 含 A 且不含 B | `深度学习 NOT 广告` |
| `(A OR B) NOT C` | 括号改变优先级 | `(tag:技术 OR tag:教程) NOT tag:广告` |

#### 解析容错

- 引号未闭合等语法错误时，自动**降级为普通搜索**（保留原词，不崩溃），并在界面给出友好提示。
- 否定词 / 括号**不会污染检索向量**——它们只作结果过滤，保证语义召回质量。

### 5.4 左栏多标签组合筛选

1. 在左栏「标签筛选」中**点击多个标签**进行多选。
2. 用标签区右上角的 **`且` / `或`** 按钮切换组合模式：
   - `且` → 等价于 `tag:A AND tag:B`（交集）。
   - `或` → 等价于 `tag:A OR tag:B`（并集）。
3. 文本框查询与左栏标签筛选以 **AND 语义合并**（即"文本命中 且 满足标签条件"）。
4. 点 `清除` 取消所有已选标签。

### 5.5 结果分组

中栏顶部的「分组」切换提供四种视图：

| 模式 | 行为 |
|------|------|
| **扁平** | 每个文本片段（chunk）一张卡片（默认，兼容旧行为） |
| **按文件** | 同一文件多次命中折叠成**一篇文件卡**，展示最佳片段，可展开查看各片段 |
| **按标签** | 按结果文件的标签分组 |
| **按源** | 按所属索引源根目录分组 |

分组模式会随每次搜索保持，并随结果自动刷新。

### 5.6 应用内帮助 / 语法速查

- 点击顶栏 **`?`** 按钮，或按 **`?`** 键，唤起语法速查浮层。
- 按 `Esc` 或再次按 `?` 关闭。

### 5.7 主题簇与批量操作

- **主题簇**（左栏）：展示基于嵌入的自动聚类。默认手动触发——点簇区域的「重新聚类」按钮生成（设置中可开启"索引后自动聚类"）。
- **批量操作条**（中栏底部，选中结果后出现）：显示「已选 N」，提供 `加标签`（紫色）、`重建`、`移除`、`取消` 等操作。

### 5.8 可视化与导出

- **思维导图**：在文档 / 簇视图中打开，缩放按钮分别为 `放大` / `缩小` / `适应窗口`。
- **关系图谱**：查看文档间关联。
- **导出**：通过导出对话框将结果 / 文档导出为 Markdown / HTML / JSON 等格式。

### 5.9 设置（`Ctrl+,`）

可配置项（持久化到 `config.json`）：

- **主题**：dark / light（亦可 `Ctrl+T` 快速切换）。
- **设备**：auto / cpu / dml:0（嵌入推理设备）。
- **模型**：嵌入模型名（默认 `BAAI/bge-small-zh-v1.5`）。
- **搜索模式**：semantic / keyword / hybrid。
- **Top-K**：默认返回结果数。
- **切片参数**：chunk 最大长度、重叠长度、编码批大小。
- **设备优先级**：嵌入设备回退顺序（见配置说明）。

### 5.10 快捷键汇总

| 快捷键 | 功能 |
|--------|------|
| `Ctrl+F` | 聚焦搜索框 |
| `Ctrl+O` | 管理索引源 |
| `F5` | 刷新 / 重建索引 |
| `Ctrl+T` | 切换明暗主题 |
| `Ctrl+,` | 打开设置 |
| `Ctrl+K` | 快速启动器 |
| `?` | 帮助 / 语法速查（再次按或 `Esc` 关闭） |

---

## 六、配置说明

配置文件位于数据目录（默认 `C:\Users\<用户名>\.cache\article_searcher\config.json`），由应用自动创建与维护。

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `last_folder` | str | `""` | 上次索引的文件夹 |
| `theme` | str | `"dark"` | 主题：`dark` / `light` |
| `device` | str | `"auto"` | 嵌入设备：`auto` 或具体 key（如 `cpu`、`dml:0`） |
| `model` | str | `"BAAI/bge-small-zh-v1.5"` | 嵌入模型 |
| `top_k` | int | `10` | 默认返回结果数 |
| `search_mode` | str | `"hybrid"` | `semantic` / `keyword` / `hybrid` |
| `chunk_max` | int | `800` | 切片最大长度（字符） |
| `chunk_overlap` | int | `100` | 切片重叠长度 |
| `batch_size` | int | `32` | 编码批大小 |
| `priority` | str | `"gpu,cpu"` | 设备优先级（与 `device_manager.DEFAULT_PRIORITY` 一致） |
| `window_geometry` | dict\|null | `null` | 窗口尺寸 / 位置 |
| `recent_searches` | list[str] | `[]` | 搜索历史（去重，上限 50，最近在前） |
| `index_sources` | list[dict] | `[]` | 多索引源：`[{path, exclude_rules, enabled}]` |
| `auto_index_enabled` | bool | `False` | 文件监听自动索引总开关 |
| `auto_index_debounce_ms` | int | `1500` | 自动索引防抖时长（毫秒） |
| `cluster_enabled` | bool | `True` | 左栏"主题簇"是否展示 |
| `cluster_auto` | bool | `False` | 索引完成后是否自动聚类 |

> 直接编辑 `config.json` 后重启应用即可生效；字段缺省时会自动用默认值补全（向后兼容）。

---

## 七、架构与目录

```
article_searcher/
├── main.py                  # 应用入口：GUI 启动、冒烟自检、帮助/分组接线
├── requirements.txt         # Python 依赖
├── build.spec               # PyInstaller 打包配置（onedir 文件夹版）
├── core/                    # 核心逻辑（与 UI 解耦，可独立测试）
│   ├── engine.py            # ArticleSearchEngine：索引 / 混合检索 / 布尔求值
│   ├── query_parser.py      # 高级语法解析 + 布尔 AST（tag:/path:/-/"..."/AND/OR/NOT/括号）
│   ├── embedding.py         # EmbeddingEngine（ONNX，支持 cpu / dml:0）
│   ├── onnx_engine.py       # ONNX 会话加载与设备管理
│   ├── vectorstore.py       # ChromaDB 向量库封装
│   ├── device_manager.py    # 设备探测与优先级（DEFAULT_PRIORITY）
│   ├── config.py            # 配置读写（AppConfig / ConfigStore）
│   ├── watcher.py           # 文件监听自动索引（watchdog）
│   ├── chunker.py           # 文档切片
│   ├── tagger.py            # 标签提取 / 管理
│   ├── clustering.py        # 主题聚类
│   ├── dedup.py             # 去重
│   ├── lexical.py           # 关键词 / BM25 检索
│   ├── search.py            # 检索融合（RRF）
│   ├── exporter.py          # 结果导出
│   ├── link_graph.py        # 关系图谱数据
│   ├── mindmap.py           # 思维导图数据
│   ├── multisource.py       # 多索引源管理
│   ├── backup.py            # 备份
│   └── parser.py            # 文档解析（md / docx / pdf / html）
├── ui/                      # PyQt6 界面
│   ├── main_window.py       # 主窗体（搜索/分组/?按钮/combine_parsed 合并）
│   ├── search_result_list.py  # 结果列表 + GroupMode 分组渲染
│   ├── tag_filter.py        # 左栏标签筛选（多选 + 且/或）
│   ├── help_overlay.py      # 帮助 / 语法速查浮层
│   ├── cluster_panel.py     # 主题簇面板 + 批量操作条
│   ├── batch_action_bar.py  # 选中结果后的批量操作条
│   ├── document_viewer.py   # 文档预览
│   ├── quick_launcher.py    # 快速启动器（Ctrl+K）
│   ├── sources_dialog.py    # 管理索引源
│   ├── settings_dialog.py   # 设置
│   ├── export_dialog.py     # 导出
│   ├── link_graph_panel.py / link_graph_viewer.py  # 关系图谱
│   ├── mindmap_viewer.py    # 思维导图
│   ├── dashboard_dialog.py  # 仪表盘
│   ├── duplicate_dialog.py  # 重复项
│   ├── related_panel.py     # 相关文档
│   ├── indexed_files_panel.py  # 已索引文件
│   ├── status_bar.py        # 状态栏
│   ├── history_completer.py # 搜索历史补全
│   └── styles.py            # 样式表
├── tests/                   # 测试套件
│   ├── run_all.py           # 统一入口（显式 CASES 列表，非自动发现）
│   └── test_*.py            # 各模块用例（含布尔查询、结果分组、多标签、帮助、config 等）
└── docs/                    # 设计文档（架构、类图、时序图、回归报告）
```

设计约束（向后兼容）：`ParsedQuery` 新增字段均带默认值；`engine.search` 签名不变，布尔分支由 `has_boolean` 门控；`display_results` 默认 `FLAT` 与旧行为一致。

---

## 八、开发与测试

### 环境与依赖

```bash
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
```

主要依赖：`PyQt6`、`chromadb`、`sentence-transformers==3.3.1`、`transformers==4.46.3`、`onnx` / `onnxruntime-directml`、`torch==2.4.1` / `torch-directml`、`PyMuPDF`、`python-docx`、`watchdog` 等（详见 `requirements.txt`）。

### 运行全部测试

```bash
venv\Scripts\python.exe tests/run_all.py
```

测试入口 `tests/run_all.py` 使用**显式 CASES 列表**（不依赖自动发现）。当前套件覆盖核心逻辑、UI 组件、布尔查询、结果分组、多标签筛选、帮助浮层、配置等，基线约 **300+ 用例**全绿。

### 打包（onedir 文件夹版）

```bash
venv\Scripts\python.exe -m PyInstaller build.spec --noconfirm
```

产物位于 `dist/ArticleSearcher/`（含 `_internal` 运行时目录）。

### 打包后冒烟自检

```bash
AS_SELFTEST=1 QT_QPA_PLATFORM=offscreen dist/ArticleSearcher/ArticleSearcher.exe
```

成功时输出类似：

```json
{"selftest":"pass","hybrid_hits":3,"keyword_hits":1,"dml":true,"dml_provider":"GPU · DirectML (AMD Radeon 780M Graphics) [DmlExecutionProvider]"}
```

> ⚠️ **重要**：冒烟自检由环境变量 `AS_SELFTEST=1` 触发；**不是**命令行参数 `--selftest`。误用 `--selftest` 会让进程进入 GUI 事件循环（无头环境下永不退出），表现为"卡住无输出"。

---

## 九、目录与文件说明（区分源码 / 产物 / 诊断脚本）

为避免混淆，下表说明哪些属于**应维护的源码**，哪些属于**构建 / 运行产物**（可忽略或清理），哪些是**开发诊断脚本**（非应用运行所需）：

| 类别 | 路径 / 文件 | 说明 |
|------|------------|------|
| ✅ 源码 | `main.py`、`core/`、`ui/`、`tests/` | 应用主体，应版本维护 |
| ✅ 配置 | `requirements.txt`、`build.spec` | 依赖与打包配置 |
| 📄 文档 | `docs/`、`README.md`、各 `*.md` 报告 | 设计 / 说明 |
| 🗑️ 构建产物 | `build/`、`dist/`、`__pycache__/`、`*.log` | PyInstaller 中间物、打包日志，可删 |
| 🗑️ 运行缓存 | `venv/`、`C:\Users\<用户>\.cache\article_searcher\` | 虚拟环境、模型与索引缓存（清缓存会丢失索引，需重建） |
| 🛠️ 诊断脚本 | `verify_*.py`、`measure_startup.py`、`rthook_qt.py`、`nul` | 历史调试 / 性能测量脚本，**非应用运行所需**，可安全忽略或删除 |

> 重新发布 / 清理时，只需保留源码目录与 `requirements.txt` / `build.spec`，其余产物可重建。

---

## 十、常见问题（FAQ）

**Q：首次启动很慢？**
A：首次需从 HuggingFace 下载嵌入模型（约 130MB）并建立索引；之后离线且更快。打包版用 onedir，启动快于一次性解压的 onefile 版。

**Q：搜索没有结果？**
A：确认已在「管理索引源」添加文件夹并建立索引（状态栏可见进度）；检查是否误加了排除规则，或搜索语法是否过严。

**Q：如何启用核显 / GPU 加速？**
A：默认 `priority: "gpu,cpu"`，DirectML 会自动启用支持的 AMD / Intel / NVIDIA 显卡；可在设置中手动指定设备。自检日志会显示实际 provider（`DmlExecutionProvider` 或 `CPUExecutionProvider`）。

**Q：换电脑 / 清缓存后索引没了？**
A：索引在 `C:\Users\<用户>\.cache\article_searcher\chromadb`，清理该目录会丢失索引，重新建立即可。

**Q：打包后运行卡住无输出？**
A：检查是否误用了 `--selftest` 参数（见第八节冒烟自检说明），应使用环境变量 `AS_SELFTEST=1`。

**Q：支持哪些文档格式？**
A：Markdown、常见文本、PDF（`PyMuPDF`）、Word（`python-docx`）、HTML 等，由 `core/parser.py` 解析。

---

## 十一、许可证与备注

本项目为本地个人知识检索工具，数据完全私有。许可证与分发条款以所在仓库约定为准。

> 文档基于当前代码库实际实现编写，涵盖最新一轮增量功能（应用内帮助、结果分组、多标签组合筛选、高级布尔语法、设备优先级修复）。历史构建 / 诊断脚本已在上文区分，不影响应用运行。
