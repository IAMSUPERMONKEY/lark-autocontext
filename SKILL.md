---
name: lark-autocontext
description: |
  Trigger when user mentions ANY of: 保存上下文 / 存入上下文 / 业务记忆 / 项目知识 / 存入知识库 / lark-autocontext / /lark-autocontext / 保存到业务上下文 / 扫描飞书 / 同步飞书知识.
  Also trigger when: user sends a Feishu doc/sheet link with intent to store or remember.
  Supports: save single document, batch scan Feishu sources, retrieve project history, ask cross-project questions.
  If config.json is missing, guide first-time setup automatically.
---

# Lark AutoContext (OKF Architecture)

## Pre-flight Check (每次执行前必检)

**Before running ANY workflow (A/B/C/D), always check lark-cli availability first:**

1. **Check lark-cli installed:** Run `lark-cli --version` in terminal.
   - If command not found → Tell user: "需要先安装 lark-cli：`npm install -g @larksuiteoapi/lark-cli`"
   - Do NOT proceed until installed.

2. **Check lark-cli logged in:** Run `lark-cli auth status`.
   - If not logged in → Tell user: "需要先登录飞书：`lark-cli auth login --recommend --no-wait`，然后在浏览器完成授权"
   - Do NOT proceed until authenticated.

3. **Check config.json exists:** Verify `scripts/config.json` exists.
   - If missing → Guide user to copy from `config.json.example` and fill in tokens.

4. **Check bundle initialized:** Verify `bundle/index.md` exists.
   - If missing → Run `python scripts/init_bundle.py` first.

Only after ALL 4 checks pass, proceed to the requested workflow.

## 🎯 Quick Start (首次使用引导)

**When this skill is triggered for the first time** (config.json missing or bundle not initialized):

Show the user this onboarding card **before any other action**:

```
🧙 **Lark AutoContext** — 你的业务记忆库 (OKF 架构)

让业务知识永不丢失。把飞书文档、会议纪要、复盘报告自动转化为 OKF 标准知识。

━━━━━━━━━━━━━━━━━━━━━━

📌 **如何使用？**

方式 1️⃣  保存单文档
  直接发文档链接 + "帮我存一下" / "保存到上下文"
  → 自动提取 → AI分类 → 生成OKF Markdown → 入库

方式 2️⃣  批量扫描
  "扫描飞书文档" / "同步飞书知识"
  → 批量提取 → AI分类 → 生成OKF Markdown → 入库

方式 3️⃣  查询上下文
  "XX 项目里关于优惠券做了什么决策？"
  → 查询OKF Bundle → 返回结果

━━━━━━━━━━━━━━━━━━━━━━

⚡ **首次使用：需要初始化**

我将为你自动创建 OKF Bundle 目录来存储知识。
确认后输入 ✅ 或 "开始"，我会自动完成。
```

**After user confirms**, run `python scripts/init_bundle.py` automatically and show:
```
✅ OKF Bundle 已初始化！
📝 现在可以开始保存上下文了。
```

Then **wait for the user's next message**.

---

This skill guides the agent to extract, classify, and store business context from Feishu documents into an OKF-compliant Markdown Bundle.

## 🔧 Configuration

Scripts automatically load `scripts/config.json` for bundle path and optional Feishu config.
Batch scanning requires `scripts/scan_config.json` (copy from `scan_config.json.example`).

**First-time Setup (Auto-detected):**
- If `config.json` is missing → trigger the **Quick Start** onboarding flow above.
- If Bundle directory doesn't exist → run `python scripts/init_bundle.py`.
- **NEVER** show raw Python tracebacks to the user. Always catch errors and provide friendly guidance.

## Mandatory Workflows

### Step 0: First-Time Check (ALWAYS run first)
- Run `python scripts/onboarding.py` to check setup status.
- If it shows ❌ or ⚠️ → trigger the **Quick Start** onboarding flow above.
- If all ✅ → proceed to appropriate workflow.

---

## Workflow A: Single Document Save

**Trigger:** User sends a Feishu doc/sheet link with save intent, or says "保存这个文档 <link>".

### Step A1: Extract Document Content
- Run `python scripts/scanner.py --doc "<url>"`.
- The script extracts content from Feishu and returns JSON with `source_type`, `doc_token`, `title`, `url`, `content`.

### Step A2: Classify Document (AI Classification)
Read the document content and apply the **Classification Guide** below to extract structured fields.

### Step A3: Generate OKF Markdown
- Construct the classified JSON (see Classification Guide for fields).
- Run `python scripts/okf_writer.py '<classified_json>' '<raw_content>'`.
- The script writes the .md file to the Bundle and updates index.md/log.md.

### Step A4: Show Save Summary
```
✅ 已保存到 OKF Bundle
📄 文件: projects/{project}/{category}/{filename}
🏷️ 类型: {type}
📝 描述: {description}
🔗 来源: {resource}
```

### Step A5: Commit to Git
- Run `git add bundle/` and `git commit -m "docs: save {title} to OKF Bundle"`.

---

## Workflow B: Batch Scan

**Trigger:** User says "扫描飞书文档" / "同步飞书知识" / "批量导入".

### Step B1: Run Scanner
- Run `python scripts/scanner.py` (reads scan_config.json automatically).
- The script scans all configured Feishu sources and returns JSON with `documents` array.

### Step B2: Classify Each Document
For each document in the scan result, apply the **Classification Guide** to extract structured fields.

### Step B3: Generate OKF Markdown for Each
- For each classified document, run `python scripts/okf_writer.py '<classified_json>' '<raw_content>'`.
- Process documents one by one.

### Step B4: Show Scan Summary
```
✅ 扫描完成
📊 总计: {total} 个文档
🆕 新增: {created} 个
🔄 更新: {updated} 个
⚠️ 失败: {failed} 个
```

### Step B5: Commit to Git
- Run `git add bundle/` and `git commit -m "docs: batch scan {total} documents to OKF Bundle"`.

---

## Workflow C: Query Context

**Trigger:** User asks a question about stored context.

### Mode 1: Project-Scoped Query
When user mentions a specific project (e.g., "lark-autocontext项目里关于 XX 的信息"):
- Run `python scripts/query.py --project "<project_name>" --keyword "<keyword>"`.
- Parse the JSON results and answer the user's question.
- Include `resource` links for traceability.

### Mode 2: Global Search
When user asks an open-ended question (e.g., "最近有什么关于 OKF 的讨论？"):
- Run `python scripts/query.py --keyword "<keyword>"`.
- Results are sorted by timestamp descending.
- Synthesize a coherent answer from multiple sources.

### Mode 3: Type Filter
When user asks for a specific type (e.g., "给我所有会议纪要"):
- Run `python scripts/query.py --type "Meeting Minutes"`.
- Return the list of matching documents.

### Getting Full Content
- Query results include `body_preview` (first 200 chars) and `file_path`.
- When full content is needed, Read the .md file at `bundle/{file_path}`.

---

## Workflow D: Auto-Sync (自动同步)

**Trigger:** Agent 定时任务调用 / 用户说"扫一遍飞书" / "同步飞书知识".

### Step D1: List Changed Documents
- Run `python scripts/auto_sync.py list-only --config config.json`.
- This produces `.auto_sync/pending_changes.json` with all changed docs since last sync.

### Step D2: Classify & Write Each Change
Read `.auto_sync/pending_changes.json`. For each entry in `changes`:
1. Fetch full content: `python scripts/scanner.py --doc "<url>"` (content is auto-cleaned).
2. Apply the **Classification Guide** to determine `type`, `project`, `category`, etc.
3. Run `python scripts/okf_writer.py '<classified_json>' '<raw_content>'`.
   - People/Concept entities are auto-upserted with preserved `# Profile` / `# Definition` regions.

### Step D3: Finalize
- Run `python scripts/auto_sync.py finalize --commit`.
- This updates `.auto_sync/state.json` watermarks and commits to git.

### Step D4: Summary
```
✅ 同步完成
📊 本次: {N} 篇文档
📁 路径: bundle/projects/...
⏭️  下次自动跳过未变更文档
```

**幂等保证:** 同一 `resource`（doc_token）重跑不会产生重复条目；人工编辑过的 `# Profile` / `# Definition` 区段不会被覆盖。

---

## Agent Cron Setup

本项目不内置守护进程，定时由 Agent 侧承担。

### TRAE Schedule
使用 Schedule 工具，cron `0 9 * * *`，message：
> 在 lark-autocontext 项目下执行 Workflow D（自动同步飞书到 bundle）。完成后只输出一句"同步 N 篇"或"无变化"。

### Cursor Tasks
在项目根 `.cursor/tasks.json` 里登记同样的命令序列。

### Claude Code cron
通过用户自己的 crontab：
```
0 9 * * * cd ~/projects/lark-autocontext && claude --workflow=auto-sync
```

---

## Classification Guide

When you receive document content from the Scanner, analyze it and extract:

1. **project**: Which project does this belong to? Infer from:
   - Document title (e.g., "Lark AutoContext 周会" → project: "lark-autocontext")
   - Content context (e.g., mentions of specific project names)
   - Folder/wiki space structure if available
   - If unclear, ask the user

2. **type**: What kind of document is this? Choose from:
   | type | Use when |
   |------|----------|
   | `Meeting Minutes` | Contains meeting notes, attendees, action items |
   | `Requirement Doc` | Describes features, user stories, acceptance criteria |
   | `Review Report` | Post-mortem analysis, lessons learned |
   | `Operation Plan` | Strategy, roadmap, operational procedures |
   | `Data Analysis` | Charts, metrics analysis, data insights |
   | `Competitor Research` | Market analysis, competitor comparison |
   | `Contract` | Agreements, terms, legal documents |
   | `Reference` | Documentation, guides, how-tos |
   | `Metric` | Business metrics definitions, KPIs |
   | `Person` | 文档作者 / 频繁被 `@` 的人（自动入库到 `bundle/people/`） |
   | `Concept` | 反复出现的术语 / 缩写 / 项目代号（自动入库到 `bundle/concepts/`） |
   | `Other` | Doesn't fit above categories |

3. **Structured fields**: Extract from content:
   - title: Document title
   - description: One-sentence summary (meaningful, not mechanical "{type} - {title}")
   - tags: 2-5 relevant keywords (as array)
   - people: Names mentioned as participants/authors (as array)
   - concepts: Key terms / acronyms / project codenames (as array)
   - summary: 1-3 sentence summary of the document
   - key_points: Main takeaways (as array of strings)
   - decisions: For Meeting Minutes / Review Reports (as array of {"decision","owner","deadline"})
   - action_items: For Meeting Minutes / Requirement Docs (as array of {"task","owner","due"})
   - filename: Generate from title, e.g., "2026-06-20 重构讨论" → "2026-06-20-重构讨论.md"
   - resource: The original Feishu document URL (for traceability and deduplication)
   - edited_time: Real edit timestamp from Feishu (used as `timestamp` in frontmatter)

4. **category**: Auto-derived from type:
   | type | category |
   |------|----------|
   | Meeting Minutes | meetings |
   | Requirement Doc | requirements |
   | Review Report | reviews |
   | Operation Plan | plans |
   | Data Analysis | analysis |
   | Competitor Research | research |
   | Contract | contracts |
   | Reference | references |
   | Metric | metrics |
   | Other | misc |

### OKF Body Structure (7 Sections)

All documents follow this body structure (sections are omitted when empty):

1. `# Summary` — 1-3 sentence summary
2. `# Key Points` — Bullet list of main takeaways
3. `# Decisions` — (Meeting Minutes / Review Report only) Decisions with owner + deadline
4. `# Action Items` — (Meeting Minutes / Requirement Doc only) Tasks with owner + due
5. `# Source Content` — Original document content (auto-cleaned)
6. `# Related` — Cross-links to people/concepts/projects (auto-generated)
7. `# Citations` — Link to original Feishu document

### Classified JSON Example

```json
{
  "project": "lark-autocontext",
  "type": "Meeting Minutes",
  "category": "meetings",
  "title": "2026-06-20 重构讨论",
  "description": "讨论 OKF 重构方案",
  "tags": ["重构", "OKF"],
  "people": ["张三", "李四"],
  "key_dates": [{"date": "2026-06-20", "event": "方案确定"}],
  "core_conclusion": "采用 Pipeline 架构，OKF 为主存储",
  "filename": "2026-06-20-重构讨论.md",
  "resource": "https://feishu.cn/docx/abc123"
}
```

---

## OKF Bundle Structure

```
bundle/
├── index.md              # Root navigation
├── log.md                # Change history
├── projects/             # Organized by project
│   ├── index.md
│   └── {project}/        # One directory per project
│       ├── index.md
│       ├── meetings/
│       ├── requirements/
│       └── ...
├── concepts/             # Cross-project concepts
└── people/               # People info
```

## Pitfalls & Lessons Learned

1. **Preview Card Rendering**: Use plain text list format, NOT markdown tables. Tables fail to render in Feishu mobile.
2. **Deduplication**: OKF Writer uses `resource` field (Feishu doc_token) to detect duplicates. Same doc scanned twice = update, not duplicate.
3. **Filename Safety**: Filenames are sanitized (no `<>:"/\|?*` characters).
4. **Index Updates**: Every write updates the category's index.md and root log.md automatically.
5. **Incremental Scan**: Scanner tracks `last_modified` to skip unchanged documents on subsequent scans.
6. **Windows Encoding**: All scripts force UTF-8 output on Windows to handle Chinese characters and emoji.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| 首次使用 / 没有 config.json | 运行 `python scripts/init_bundle.py` 自动创建 Bundle |
| Bundle 未初始化 | 运行 `python scripts/init_bundle.py` |
| Token 过期 / 认证失败 | 运行 `lark-cli auth login --recommend --no-wait` 重新登录 |
| 扫描配置缺失 | 从 `scan_config.json.example` 复制并填写飞书 token |
| 查询无结果 | 先保存文档或运行批量扫描 |
| 提取失败 | 检查文档链接格式是否正确（应为 feishu.cn/docx/xxx） |

### ⚠️ Windows 用户注意

| 问题 | 说明 |
|------|------|
| **PowerShell `&&` 不支持** | Windows PowerShell 不支持 `&&` 连接符，请用分号 `;` 或直接分开运行命令 |
| **PowerShell JSON 引号转义** | 在 PowerShell 中直接传递 `--json "{...}"` 时引号会被转义，**建议使用 Python 脚本调用** |

**错误处理铁律**：
- ❌ **禁止** 向用户展示 Python traceback 或 raw JSON 错误
- ✅ **必须** 捕获异常，转换为中文友好提示
- ✅ **必须** 告诉用户下一步该做什么
