# Lark AutoContext

> 把飞书（Lark）文档自动转成 [OKF (Open Knowledge Format)](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md) 标准的项目知识 bundle，供 AI Agent 长期上下文使用。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Why lark-autocontext?

团队的业务知识散落在飞书文档、会议纪要、需求文档里，Agent 每次对话都从零开始——不知道项目背景、不知道历史决策、不知道谁负责什么。

OKF（Open Knowledge Format）用纯 Markdown + YAML frontmatter 表示知识，天然具备几个关键属性：

- **人和 Agent 都能直接读。** 不需要 SDK 或查询语言，`cat` 一下就能看，LLM 可以原样注入上下文。
- **天然版本控制。** Bundle 在 git 里，PR、diff、blame、review 全部开箱即用——知识管理变成正常的软件工程活动。
- **无锁定。** 一个目录就是全部，打包、托管、同步到任何文件系统，没有私有 API 挡在你和数据之间。
- **结构化与非结构化混存。** frontmatter 放你要查询/过滤的字段（`type`、`tags`、`timestamp`），markdown body 放 LLM 和人真正要读的内容。
- **与现有工具无缝衔接。** Obsidian、Notion、MkDocs、Hugo 都原生支持 markdown + YAML frontmatter。

**lark-autocontext 做的事：** 把飞书这个数据源接入 OKF 生态——自动提取、AI 分类、生成 OKF 合规文档、增量同步、交叉链接、可视化。所有操作均需用户主动触发且仅访问用户配置的数据源。

**核心方程：** AI 的产出 = 模型能力 × Agent 框架 × 上下文质量

## Agent-First: 一行安装，即装即用

**给 Agent 这个 GitHub 地址，让它自己装：**

```
https://github.com/KitchSupermonkey/lark-autocontext
```

Agent 会 clone 仓库、读取 `SKILL.md`，即可按用户指令操作飞书文档——保存、扫描、同步、查询，所有操作均需用户主动触发且仅访问用户配置的数据源。

### 支持的 Agent 平台

| Agent | 安装方式 |
|-------|---------|
| **TRAE** | Clone 到工作区 → Agent 自动识别 `SKILL.md` → 触发词激活 |
| **Cursor** | Clone 到项目 → Agent 读取 `SKILL.md` 作为项目指令 |
| **Claude Code** | Clone → `SKILL.md` 作为 CLAUDE.md 的补充指令 |
| **Codex** | Clone → `SKILL.md` 作为任务指令注入 |
| **Hermes Agent** | Clone → 将 `SKILL.md` 注册为 Agent Skill |
| **OpenClaw** | Clone → 将 `SKILL.md` 配置为知识源 + 工具链 |

### Agent Skill 触发词

当用户说出以下任何一种，Agent 自动激活此 Skill：

`保存上下文` / `存入上下文` / `业务记忆` / `项目知识` / `存入知识库` / `扫描飞书` / `同步飞书知识` / 发送飞书文档链接并意图存储

## Architecture

```
飞书文档 → Scanner 提取 → Agent AI分类 → OKF Writer 生成 .md → Bundle (Git)
                                                              ↓
                                                        Query Engine
                                                              ↓
                                                           Agent
```

- **OKF-first**: 知识以 OKF Markdown 存储，Git 版本控制
- **Agent-agnostic**: 任何 Agent 都能通过 Skill 脚本读取
- **Auto-Sync**: 定时增量同步，Agent Cron 驱动，无需守护进程

## Agent Quick Start

### Step 0: Pre-flight Check (每次执行前必检)

**Agent 在执行任何工作流之前，必须先检查 lark-cli 是否可用：**

```bash
# 1. 检查 lark-cli 是否安装
lark-cli --version
# 若未安装：npm install -g @larksuiteoapi/lark-cli

# 2. 检查是否已登录
lark-cli auth status
# 若未登录：lark-cli auth login --recommend --no-wait
# 然后在浏览器完成授权

# 3. 检查 config.json 是否存在
ls scripts/config.json
# 若不存在：cp scripts/config.json.example scripts/config.json
# 然后填入飞书 token

# 4. 检查 bundle 是否已初始化
ls bundle/index.md
# 若不存在：python scripts/init_bundle.py
```

4 项全部通过后，才能执行工作流。

### Step 1: Clone & Setup

```bash
git clone https://github.com/KitchSupermonkey/lark-autocontext.git
cd lark-autocontext
pip install -r requirements.txt
cp scripts/config.json.example scripts/config.json
cp scripts/scan_config.json.example scripts/scan_config.json
# Edit config.json and scan_config.json with your Feishu tokens
python scripts/init_bundle.py
python scripts/onboarding.py --quiet
```

### Step 2: Agent 自动操作（SKILL.md 定义了 4 种工作流）

| Workflow | 触发方式 | Agent 做什么 |
|----------|---------|-------------|
| **A: 单文档** | 用户发飞书链接 + "保存" | 提取 → 分类 → 写入 bundle |
| **B: 批量扫描** | "扫描飞书文档" | 批量提取 → 逐篇分类 → 写入 bundle |
| **C: 查询** | "XX项目里关于XX的信息？" | 查询 bundle → 综合回答 |
| **D: 自动同步** | Agent 定时任务 / "同步飞书" | list-only → 分类写入 → finalize |

### Step 3: 定时同步（Agent Cron）

Agent 原生定时功能驱动，项目不内置守护进程：

```bash
# 每次同步只需两步
python scripts/auto_sync.py list-only
# Agent 按 SKILL.md Workflow D 分类并写入 bundle
python scripts/auto_sync.py finalize --commit
```

**TRAE Schedule 示例：** cron `0 9 * * *`，message 填写"执行 Workflow D 自动同步飞书到 bundle"。

## Human Usage

不需要懂代码，直接对 Agent 说：

```
保存这个文档 https://feishu.cn/docx/xxx
```
```
扫描飞书文档
```
```
XX项目里关于XX的信息？
```
```
同步飞书知识
```

## Visualization

```bash
python scripts/visualize.py --bundle bundle/ --out viz.html
```

单文件 HTML（Cytoscape.js 力导向图 + marked.js），节点按 OKF `type` 着色，支持搜索。

## Project Structure

```
lark-autocontext/
├── SKILL.md              # Agent Skill 定义（4 种工作流 + 分类指南）
├── scripts/
│   ├── cli.py            # Feishu API wrapper
│   ├── scanner.py        # 文档扫描器 (--list-changed 增量模式)
│   ├── okf_writer.py     # OKF Markdown 生成 (交叉链接, upsert)
│   ├── auto_sync.py      # Auto-Sync 协调器 (list-only + finalize)
│   ├── visualize.py      # 单文件 HTML 可视化
│   ├── query.py          # 查询引擎
│   ├── init_bundle.py    # Bundle 初始化
│   └── onboarding.py     # 状态检查 (--quiet 非交互模式)
├── bundle/               # OKF Bundle (知识存储)
├── tests/                # pytest 测试套件
└── README.md
```

## License

MIT License - see [LICENSE](LICENSE) file for details.
