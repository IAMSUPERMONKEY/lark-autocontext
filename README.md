# 🪄 Context Wizard — 基于飞书 CLI 的业务上下文引擎

> **Powered by Lark CLI** | **跨Agent通用** | **Context Engineering**

Context Wizard 是一个通用的业务上下文管理工具，通过纯 Python 脚本和 `lark-cli` 构建，不依赖任何特定 Agent 框架。它致力于解决 AI 时代的**上下文质量**瓶颈——让散落的信息变为结构化、可追溯、带时间线的业务知识。

---

## 🧠 为什么需要 Context Wizard？

**"AI 的产出 = 模型能力 × Agent 框架 × 上下文质量"**

在业务场景中，模型和Agent能力越来越强，**上下文质量**已成为核心挑战。
- **痛点**：项目文档散落在飞书云文档、多维表格、聊天记录中。当你需要 AI 协助时，往往面临信息遗漏、上下文断裂、Token 浪费等问题。
- **解决方案**：Context Wizard 基于 `lark-cli` 构建，自动将碎片化的业务信息提取、结构化、存储到多维表格中，并提供**带时间线的全局检索**。让你的 AI Agent 真正"懂业务"。

---

## ✨ 核心亮点

| 特性 | 说明 |
| :--- | :--- |
| 🚀 **纯 `lark-cli` 驱动** | 不依赖 MCP Server 或复杂插件。基于官方 CLI 构建，稳定、轻量、兼容性好。 |
| 🔗 **上下文可追溯** | 每一行数据都对应一个原始文档（自动转存飞书文档），点击即可溯源，拒绝 AI 幻觉。 |
| 📅 **决策时间线回溯** | 不仅记录结果，更记录演变过程。"4.1 定方案 A → 4.15 因成本改方案 B"，一目了然。 |
| 🔍 **全局智能检索** | 无需指定项目名，直接问"关于优惠券做了什么决策"，AI 自动跨表搜索并综合回答。 |
| 📦 **跨Agent通用** | 纯 Python + `SKILL.md` 设计。任何支持 Markdown 指令的 AI Agent 均可运行。 |

---

## 📽️ 核心场景演示

### 1. 碎片信息自动入库
直接把文档/图片链接发给 Agent，无需手动填写表单。
1. **输入**：`帮我存一下这个复盘文档：https://feishu.cn/docx/...`
2. **处理**：Agent 调用 `lark-cli` 提取内容，展示预览卡片。
3. **确认**：用户点击 OK，数据自动写入多维表格。

### 2. 上下文时间线回溯
当需要回顾历史决策时：
- **User**: "我们的运营策略变过几次？"
- **Context Wizard**: "经历 3 次变更：
  1. 4 月 1 日初定 A 方案 [🔗溯源]
  2. 4 月 15 日因成本调整为 B 方案 [🔗溯源]
  3. 5 月 1 日正式上线 C 方案 [🔗溯源]"

### 3. 全局智能检索
跨项目、跨表格的知识汇总：
- **User**: "我们在所有项目中关于预算的决策分别是什么？"
- **Context Wizard**: 自动遍历所有项目表，按时间线汇总并输出决策摘要。

---

## 🛠️ 快速开始 (Quick Start)

### 1. 前置准备
确保已安装并配置好官方 `lark-cli`：
```bash
# 验证安装
lark-cli --version

# 绑定飞书应用 (以 Hermes 为例)
lark-cli config bind --source hermes
lark-cli auth login --recommend
```

### 2. 安装与初始化
```bash
# 克隆仓库
git clone https://github.com/KitchSupermonkey/context-wizard.git
cd context-wizard

# 初始化多维表格 (自动创建 Base 并写入配置)
python scripts/init_base.py
```

### 3. 开始使用

在你的 AI Agent 中加载 `SKILL.md`，然后通过以下任一方式开始使用：

| 方式 | 示例 | 说明 |
| :--- | :--- | :--- |
| 💬 **自然语言** | "帮我存一下这个文档：https://feishu.cn/docx/..." | 发链接 + 保存意图，自动触发 |
| 🔧 **斜杠命令** | `/context-wizard https://feishu.cn/docx/...` | 明确的指令入口 |
| 🔍 **提问检索** | "我们关于优惠券做了什么决策？" | 自动跨表搜索 + 时间线回答 |

### 4. 自动扫描飞书文档

如果你不想手动逐个提供文档，可以启用自动扫描。它会通过 `lark-cli drive +search --as user` 发现你有权限访问的飞书云文档，高置信候选会直接入库，中低置信候选会进入待确认队列。

自动扫描需要用户身份和搜索权限：

```bash
lark-cli config init --new
lark-cli auth login --recommend --no-wait
lark-cli auth login --scope "search:docs:read" --no-wait
```

如果飞书提示应用正在等待 `search:docs:read` 审批，等管理员/飞书侧审批通过后再重试扫描。

```bash
# 每日轻扫：处理最近变化
python scripts/scan_drive.py --mode daily

# 每周深扫：推进历史文档回填
python scripts/scan_drive.py --mode weekly

# 查看状态和需要人工关注的候选
python scripts/ingest_candidates.py status
python scripts/ingest_candidates.py review

# 兼容旧状态：批准历史待确认候选并立即入库
python scripts/ingest_candidates.py approve <candidate_id> --ingest
python scripts/ingest_candidates.py approve --all --ingest

# 跳过候选
python scripts/ingest_candidates.py skip <candidate_id>

# 手动重扫某个候选、token 或 URL
python scripts/ingest_candidates.py rescan <candidate_id_or_token_or_url> --ingest
```

自动扫描不是 dry-run：默认采用**后置审核**，会把发现的可定位资源先写入主题表，并在记录里显示 `扫描分数`、`扫描原因`、`入库方式` 和 `状态`。用户认为不合适时，直接在多维表格中把 `状态` 改成 `失效`，或删除记录即可。扫描状态保存在 `.context_wizard/scan_state.jsonl`，历史回填游标保存在 `.context_wizard/backfill_cursor.json`。

---

## 🏗️ 技术架构

本项目展示了 `lark-cli` 在复杂业务流中的最佳实践：
- **Sub-Agent 隔离架构**：入库与检索均交由 Sub-Agent 执行，主 Agent 仅负责交互，彻底解决长上下文窗口污染问题。
- **一项目一表 (One Project One Table)**：动态多维表格架构，支持无限扩展，互不干扰。
- **自动化流水线**：`lark-cli` 驱动的全链路脚本（扫描 -> 评分 -> 提取 -> 建表 -> 写入 -> 仪表板生成 -> 全局检索）。

---

## 📜 许可证
本项目基于 MIT 许可证开源。

## 🙏 致谢
- [larksuite/cli](https://github.com/larksuite/cli) - 飞书 CLI 开源项目
- 飞书开放平台

---
