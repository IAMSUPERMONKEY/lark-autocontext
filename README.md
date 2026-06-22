# Lark AutoContext

> **lark-autocontext** —— 把飞书（Lark）文档自动转成 OKF 标准的项目知识 bundle，供 AI Agent 长期上下文使用。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Agent-First: 一行安装，即装即用

**给 Agent 这个 GitHub 地址，让它自己装：**

```
https://github.com/KitchSupermonkey/lark-autocontext
```

Agent 会 clone 仓库、读取 `SKILL.md`，自动获得完整的飞书知识管理能力——保存文档、批量扫描、增量同步、查询上下文，无需任何手动配置脚本。

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

## What is this?

Lark AutoContext 把飞书文档、会议纪要、复盘报告自动转化为 [OKF (Open Knowledge Format)](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf) 标准的 Markdown 知识库，让任何 Agent 都能快速准确地获取业务上下文。

**核心方程：** AI 的产出 = 模型能力 × Agent 框架 × 上下文质量

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
