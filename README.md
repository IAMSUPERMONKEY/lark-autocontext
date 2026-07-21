# Lark AutoContext

> 自动把飞书文档变成 Agent 能直接用的上下文——散落文档自动整理、增量同步、结构化存储、全文搜索。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## 它能做什么？

**场景：**

你负责多个项目/业务，想让 Agent 帮你干活——

1. **文档散落**：需求、会议、复盘散在飞书各处（知识库、文件夹、Wiki、表格），找起来费劲
2. **信息量大**：即便找到了，Agent 要一篇篇读、自己组织上下文、分析关系，效率低
3. **有过时信息**：历史文档里可能混着过期结论，Agent 无法自行判断
4. **反复劳动**：半个月后业务变化，又要重新整理、补充新上下文

**装了这个之后：**

```
你：扫描飞书文档（Wiki 空间 / 指定文件夹）
Agent：✅ 发现 12 篇变更，已按 项目/人物/概念 自动分类归档
       ✅ 结构化输出同步回飞书 Wiki，人类可直接阅读

你：半个月后，业务有更新
Agent：✅ 增量同步 3 篇新文档，自动更新上下文
       ✅ 检测飞书端编辑，双向同步无冲突

你：问一下知识库，XX项目里关于优惠券做了什么决策？
Agent：✅ FTS5 全文搜索（支持中文），3 秒返回相关文档 + 深度阅读

你：把整个 bundle 可视化看看
Agent：✅ 已生成 viz.html，浏览器打开可看到文档关系图
```

**一句话：** 飞书散落文档 → 结构化上下文，自动分类、双向同步、全文搜索、Agent 直接读。

## 两种模式

| | Folder 模式（传统） | Wiki Space 模式（新增） |
|---|---|---|
| **数据源** | 飞书文件夹 token | 飞书 Wiki Space 知识库 |
| **人类投放** | 手动整理到文件夹 | 扔进 Wiki "原始文档区"即可 |
| **结构化输出** | 仅本地 bundle | 本地 bundle + 飞书 Wiki "Agent 维护区" |
| **双向同步** | 单向拉取 | 推送 + 拉取 + 冲突检测 |
| **全文搜索** | 子串匹配 | SQLite FTS5（支持中文分词） |
| **配置方式** | `scan_config.json` | `config.json` 的 `wiki` 字段 |
| **启用条件** | 默认 | 设置 `wiki.space_id` 后自动激活 |

Wiki 模式是**可选的**——不配置 `wiki.space_id` 就完全使用传统 Folder 模式，已有配置不受影响。

从 Folder 模式迁移到 Wiki 模式：

```bash
python scripts/migrate_to_wiki.py --space-id <wiki_space_id> --raw-node <raw_area_node_token> --agent-node <agent_area_node_token>
```

## 底层格式

输出是纯 Markdown + YAML frontmatter，人可读、Git 可管、工具可解析：

- **人可直接读。** 不需要 SDK，`cat` / Obsidian / VSCode 直接看。
- **Git 版本控制。** diff、blame、PR review 全部开箱即用。
- **无平台锁定。** 一个目录就是全部上下文，随时迁移、备份、二次加工。
- **与 Obsidian / Notion / MkDocs 等工具原生兼容。**
- **任何 Agent / RAG / LLM 都能直接消费。** 标准 Markdown，无需特殊适配。

底层采用 [OKF (Open Knowledge Format)](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md) 标准。

## Agent-First: 即装即用

**把 GitHub 地址发给 Agent，让它自己装：**

```
https://github.com/IAMSUPERMONKEY/lark-autocontext
```

Agent 会 clone 仓库、读取 `SKILL.md`，即可按用户指令操作飞书文档——保存、扫描、同步、查询。

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

`保存上下文` / `存入上下文` / `业务记忆` / `项目知识` / `存入知识库` / `扫描飞书` / `同步飞书知识` / `同步飞书知识库` / `问一下知识库` / `查一下业务` / `搜索知识库` / `查询知识库` / 发送飞书文档链接并意图存储

## Architecture

```
                    ┌─────────────────────────────────────┐
                    │          Feishu Wiki Space           │
                    │  ┌─────────────┐  ┌───────────────┐  │
                    │  │  原始文档区  │  │ Agent 维护区  │  │
                    │  │ (人类投放)   │  │ (结构化输出)  │  │
                    │  └──────┬──────┘  └───────┬───────┘  │
                    └─────────┼─────────────────┼──────────┘
                              │                 │
                    WikiConnector   DualStorage (双向同步)
                              │                 │
                    ┌─────────▼─────────────────▼──────────┐
                    │           Local Bundle                │
                    │  ┌────────────────────────────────┐   │
                    │  │  OKF Markdown (.md files)      │   │
                    │  │  + QueryEngine (FTS5 索引)      │   │
                    │  │  + sync_state.json (同步状态)   │   │
                    │  └────────────────────────────────┘   │
                    └───────────────────┬───────────────────┘
                                        │
                              QueryEngine (渐进式 RAG)
                                        │
                                     Agent
```

- **OKF-first**: 知识以 OKF Markdown 存储，Git 版本控制
- **Wiki Space 双向同步**: 人类在飞书投放/编辑，Agent 维护结构化输出，冲突时"飞书优先 + 本地备份"
- **FTS5 全文搜索**: SQLite FTS5 + CJK 中文分词，渐进式 RAG（召回 → 过滤 → 深度阅读）
- **Agent-agnostic**: 任何 Agent 都能通过 Skill 脚本读取
- **Auto-Sync**: 定时增量同步，Agent Cron 驱动，无需守护进程

## Agent Quick Start

### Step 0: Pre-flight Check (每次执行前必检)

**Agent 在执行任何工作流之前，必须先检查环境：**

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

# 4. 检查 bundle 是否已初始化
ls bundle/index.md
# 若不存在：python scripts/init_bundle.py

# 5. (Wiki 模式) 检查 wiki 配置
# 若 config.json 有 wiki.space_id，确认 raw_node_token 和 agent_node_token 也已填写
# 若要从 Folder 模式迁移：python scripts/migrate_to_wiki.py --space-id ... --raw-node ... --agent-node ...

# 6. (Wiki 模式) 检查搜索索引
ls bundle/.index/search.db
# 若不存在：python scripts/query_engine.py rebuild
```

### Step 1: Clone & Setup

```bash
git clone https://github.com/IAMSUPERMONKEY/lark-autocontext.git
cd lark-autocontext
pip install -r requirements.txt

# Folder 模式（传统）
cp scripts/config.json.example scripts/config.json
cp scripts/scan_config.json.example scripts/scan_config.json
# Edit config.json and scan_config.json with your Feishu tokens

# Wiki 模式（推荐）
cp scripts/config.json.example scripts/config.json
# Edit config.json, fill in wiki.space_id / raw_node_token / agent_node_token

python scripts/init_bundle.py
python scripts/onboarding.py --quiet
```

### Step 2: Agent 自动操作（SKILL.md 定义了 4 种工作流）

| Workflow | 触发方式 | Agent 做什么 |
|----------|---------|-------------|
| **A: 单文档** | 用户发飞书链接 + "保存" | 提取 → 分类 → 写入 bundle → (Wiki 模式) 同步回飞书 |
| **B: 批量扫描** | "扫描飞书文档" | 批量提取 → 逐篇分类 → 写入 bundle → (Wiki 模式) 批量同步 |
| **C: 查询** | "XX项目里关于XX的信息？" | FTS5 搜索 → 结构化过滤 → 深度阅读 → 综合回答 |
| **D: 自动同步** | Agent 定时任务 / "同步飞书" | list-only → 分类写入 → 同步飞书 → finalize |

### Step 3: 定时同步（Agent Cron）

Agent 原生定时功能驱动，项目不内置守护进程：

```bash
# 每次同步只需两步
python scripts/auto_sync.py list-only
# Agent 按 SKILL.md Workflow D 分类并写入 bundle
python scripts/auto_sync.py finalize --commit
```

**Wiki 模式下**，`list-only` 自动检测 `config.json` 中的 wiki 配置，使用 `WikiConnector.list_raw_docs()` 列出变更文档，无需 `scan_config.json`。

**TRAE Schedule 示例：** cron `0 9 * * *`，message 填写"执行 Workflow D 自动同步飞书到 bundle"。

## FTS5 全文搜索

Wiki 模式下，`query_engine.py` 提供 SQLite FTS5 全文搜索，替代传统的子串匹配 `query.py`：

```bash
# 搜索
python scripts/query_engine.py search --query "微服务架构" --top-n 10

# 带过滤条件
python scripts/query_engine.py search --query "优惠券" --project "payment" --type "Design Doc" --tags "架构,API"

# 深度阅读模式（默认开启，返回完整内容上下文）
python scripts/query_engine.py search --query "架构评审" --no-deep-read

# 重建索引
python scripts/query_engine.py rebuild

# 查看索引状态
python scripts/query_engine.py status
```

**渐进式 RAG 三段式：**
1. **FTS5 召回** — 全文匹配（支持中文单字搜索，CJK 分词变通方案）
2. **结构化过滤** — 按 project / type / tags / people / 日期范围过滤
3. **深度阅读** — 读取匹配文档全文，组装为 Agent 上下文

**综合评分：** FTS 相关度 × 0.6 + 时间衰减 × 0.2 + 类型权重 × 0.2

索引在每次 OKF 文档写入时自动更新（`okf_writer` 内置索引钩子），无需手动维护。

## 双向同步

Wiki 模式下，`dual_storage.py` 协调本地 bundle 与飞书 Wiki 的双向同步：

| 方向 | 方法 | 说明 |
|------|------|------|
| **推送 (Local → Feishu)** | `sync_to_feishu()` | OKF 转 Feishu 格式（YAML frontmatter → emoji 元数据头），创建/更新 Agent 区文档 |
| **拉取 (Feishu → Local)** | `pull_from_feishu()` | 拉取飞书编辑，转回 OKF body 格式，保留本地 frontmatter |
| **检测编辑** | `detect_feishu_edits()` | 对比 sync_state.json 与飞书端 modified_time，返回需同步的文档列表 |

**冲突解决策略：** "飞书优先 + 本地备份" — 当文档在本地和飞书同时被编辑时，飞书版本覆盖本地，旧版本备份到 `bundle/.conflicts/{node_token}_{timestamp}.md`，并记录到 `bundle/.conflicts/log.md`。

同步状态跟踪在 `bundle/.sync_state.json`，记录每篇文档的 `sync_direction`（IN_SYNC / LOCAL_NEWER / FEISHU_NEWER / CONFLICT）。

## Human Usage

不需要懂代码，直接对 Agent 说：

```
保存这个文档 https://feishu.cn/docx/xxx
```
```
扫描飞书文档
```
```
问一下知识库，XX项目里关于XX的信息？
```
```
同步飞书知识库
```

## Visualization

```bash
python scripts/visualize.py --bundle bundle/ --out viz.html
```

单文件 HTML（Cytoscape.js 力导向图 + marked.js），节点按 OKF `type` 着色，支持搜索。

## Project Structure

```
lark-autocontext/
├── SKILL.md                  # Agent Skill 定义（4 种工作流 + 分类指南）
├── scripts/
│   ├── cli.py                # Feishu API wrapper (Folder 模式)
│   ├── wiki_connector.py     # Wiki Space 读写 + OKF↔飞书转换 (Wiki 模式)
│   ├── dual_storage.py       # 双向同步 + 冲突解决 (Wiki 模式)
│   ├── query_engine.py       # FTS5 全文搜索 + 渐进式 RAG (Wiki 模式)
│   ├── migrate_to_wiki.py    # Folder → Wiki 模式迁移工具
│   ├── scanner.py            # 文档扫描器 (--wiki 支持 Wiki 模式)
│   ├── okf_writer.py         # OKF Markdown 生成 (交叉链接, upsert, 索引钩子)
│   ├── auto_sync.py          # Auto-Sync 协调器 (list-only + finalize)
│   ├── visualize.py          # 单文件 HTML 可视化
│   ├── query.py              # 旧版查询引擎 (子串匹配, 保留兼容)
│   ├── init_bundle.py        # Bundle 初始化
│   ├── onboarding.py         # 状态检查 (--quiet 非交互模式)
│   └── setup.py              # 首次安装引导
├── bundle/                   # OKF Bundle (知识存储)
│   ├── .index/search.db      # FTS5 搜索索引
│   ├── .sync_state.json      # 同步状态
│   └── .conflicts/           # 冲突备份
├── tests/                    # pytest 测试套件 (188 tests)
└── README.md
```

## License

MIT License - see [LICENSE](LICENSE) file for details.
