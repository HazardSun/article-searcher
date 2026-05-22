# 智能文章整理与语义检索

一个运行在 Windows PC 本地的 AI 驱动知识管理工具，支持语义搜索、标签筛选、思维导图可视化，以及多种文件格式（Markdown / HTML / TXT / PDF / DOCX）。

![screenshot](https://img.shields.io/badge/Platform-Windows-blue)
![python](https://img.shields.io/badge/Python-3.12-blue)
![license](https://img.shields.io/badge/License-MIT-green)

## 功能特性

- **语义搜索** — 用自然语言搜索文章内容，无需精确关键词匹配，支持模糊语义匹配
- **多格式支持** — 读取 `.md`、`.html` / `.htm`、`.txt`、`.pdf`、`.docx` 文件
- **智能切片** — 按段落、标题组语义完整性自动切片，保持上下文连贯
- **标签自动生成** — 索引时自动提取关键词作为标签，支持实时筛选和搜索
- **思维导图** — 将搜索结果可视化为交互式思维导图，支持缩放拖拽
- **多硬件加速** — 自动检测并支持 NVIDIA CUDA / AMD DirectML / Intel GPU / CPU
- **增量索引** — 仅处理新增或修改的文件，大型文件夹也能快速刷新
- **段落高亮跳转** — 精准定位匹配段落并高亮显示，支持上下翻页
- **深色/浅色主题** — 一键切换，舒适阅读

## 快速开始

### 方式一：下载 EXE 压缩包

从 [Releases](https://github.com/HazardSun/article-searcher/releases) 下载最新版 `ArticleSearcher_v1.0.zip`，解压后双击 `ArticleSearcher.exe` 即可运行。

> 首次启动索引时需要自动下载约 500 MB 的嵌入模型（bge-small-zh-v1.5），请确保网络畅通。

### 方式二：源码运行

```bash
# 进入项目目录
cd article_searcher

# 创建虚拟环境
python -m venv venv
venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 启动应用
python main.py
```

## 使用说明

1. **选择文件夹** — 点击"选择文件夹"，选择包含文章/文档的目录
2. **等待索引** — 系统自动扫描、解析、切片、向量化文件（首次需下载模型）
3. **语义搜索** — 在搜索框输入自然语言查询，按回车或点击"搜索"
4. **浏览结果** — 左侧列表显示匹配片段，右侧查看全文并高亮定位
5. **标签筛选** — 点击标签过滤结果，支持搜索标签关键词
6. **思维导图** — 点击"思维导图"按钮，将搜索结果可视化展示
7. **切换设备** — 在设备下拉框中选择不同的 GPU 或 CPU 运行模型

## 硬件加速

软件启动时自动检测可用硬件，在设备下拉框中列出所有可用设备。

| 选项 | 说明 |
|------|------|
| 自动 | 按 CUDA → DirectML → CPU 优先级自动选择 |
| GPU (NVIDIA ...) | NVIDIA 显卡 CUDA 加速 |
| GPU (AMD ...) | AMD 显卡 DirectML 加速 |
| GPU (Intel ...) | Intel 显卡 DirectML 加速 |
| CPU | 仅 CPU 运行（兼容最佳，速度最慢） |

> 若系统同时有 NVIDIA + AMD/Intel GPU，两者都会在设备列表中，可自由切换。

## 支持的格式

| 格式 | 扩展名 | 解析引擎 |
|------|--------|----------|
| Markdown | `.md` | Marko |
| HTML | `.html` / `.htm` | BeautifulSoup + lxml |
| 纯文本 | `.txt` | 按空行分段 |
| PDF | `.pdf` | PyMuPDF |
| Word | `.docx` | python-docx |

## 技术栈

| 组件 | 技术 |
|------|------|
| GUI | PyQt6 |
| 向量数据库 | ChromaDB |
| 嵌入模型 | BAAI/bge-small-zh-v1.5（512 维） |
| 语义切片 | 自定义段落分割（最大 512 tokens） |
| GPU 加速 | CUDA (PyTorch) / DirectML (torch-directml) |
| 打包工具 | PyInstaller（onedir 模式） |

## 目录结构

```
本地文章整理/
└── article_searcher/                # 主程序源码
    ├── main.py                      # 应用入口
    ├── requirements.txt             # Python 依赖
    ├── build.spec                   # PyInstaller 打包配置
    ├── rthook_qt.py                 # EXE 运行时钩子
    ├── core/                        # 核心逻辑
    │   ├── parser.py                # 文件扫描与解析
    │   ├── chunker.py               # 语义切片
    │   ├── embedding.py             # 嵌入引擎 + 硬件检测
    │   ├── onnx_engine.py           # ONNX Runtime 推理
    │   ├── vectorstore.py           # ChromaDB 向量数据库
    │   ├── tagger.py                # 标签生成
    │   ├── mindmap.py               # 思维导图生成
    │   └── engine.py                # 搜索引擎编排
    ├── ui/                          # 界面
    │   ├── styles.py                # 深色/浅色主题 QSS
    │   ├── main_window.py           # 主窗口
    │   ├── search_result_list.py    # 搜索结果列表
    │   ├── document_viewer.py       # 文档查看器（高亮）
    │   ├── tag_filter.py            # 标签筛选（流式布局）
    │   ├── status_bar.py            # 可伸缩状态栏
    │   └── mindmap_viewer.py        # 思维导图渲染器
    └── dist/ArticleSearcher/        # 打包输出
        ├── ArticleSearcher.exe
        └── README.md
```

## 模型说明

默认使用 `BAAI/bge-small-zh-v1.5`（中文优化，100MB），首次使用自动下载。

备选 `all-MiniLM-L6-v2`（英文优化）在 CUDA/DirectML 加载失败时自动回退。

## 打包 EXE

```bash
cd article_searcher
pip install pyinstaller
python -m PyInstaller build.spec --noconfirm
```

输出位置：`article_searcher/dist/ArticleSearcher/ArticleSearcher.exe`

## 隐私说明

- 所有计算均在本地完成，**不上传任何数据**
- 无需注册或登录
- 不收集任何统计信息

## 许可

MIT License
