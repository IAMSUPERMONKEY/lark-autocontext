---
name: context-wizard
description: |
  Trigger when user mentions ANY of: 保存上下文 / 存入上下文 / 业务记忆 / 项目知识 / 存入知识库 / context-wizard / /context-wizard / 保存到业务上下文.
  Also trigger when: user sends a Feishu doc/sheet link with intent to store or remember.
  Supports: save documents, retrieve project history, ask cross-project questions.
  If config.json is missing, guide first-time setup automatically.
---

# Business Context Wizard

## 🎯 Quick Start (首次使用引导)

**When this skill is triggered for the first time** (config.json missing or base_token empty):

Show the user this onboarding card **before any other action**:

```
🧙 **Business Context Wizard** — 你的业务记忆库

让业务知识永不丢失。把飞书文档、会议纪要、复盘报告自动转化为可检索的结构化知识。

━━━━━━━━━━━━━━━━━━━━━━

📌 **如何使用？**

方式 1️⃣  自动触发
  直接发文档链接 + "帮我存一下" / "保存到上下文"
  → 自动提取 → 预览确认 → 入库

方式 2️⃣  斜杠命令
  /context-wizard [文档链接]
  → 同上，更明确的指令

方式 3️⃣  直接提问
  "XX 项目里关于优惠券做了什么决策？"
  → 跨表搜索 → 时间线回答

━━━━━━━━━━━━━━━━━━━━━━

⚡ **首次使用：需要初始化**

我将为你自动创建一个飞书多维表格 Base 来存储上下文数据。
确认后输入 ✅ 或 "开始"，我会自动完成。
```

**After user confirms**, run `python scripts/init_base.py` automatically and show:
```
✅ 业务上下文引擎已初始化！
🔗 Base URL: https://...
📝 现在可以开始保存上下文了。
```

Then **wait for the user's next message** (the actual document or question).

---

This skill guides the agent to extract, verify, and store business context from Feishu documents, sheets, images, or text into a structured database (Feishu Base).

## ⚠️ Context Protection Architecture
To prevent context window pollution and ensure the Main Agent remains responsive:
- **Main Agent (Manager)**: Handles user interaction, shows preview cards, orchestrates saving data. **NEVER** reads raw document content or runs heavy extraction scripts directly.
- **Sub-Agent (Worker)**: Handles data fetching, parsing, and summarization. **ALWAYS** delegates raw data processing to a sub-agent.

## 🔧 Configuration

Scripts automatically load `scripts/config.json` if `base_token` is not provided explicitly.

**First-time Setup (Auto-detected):**
- If `config.json` is missing or `base_token` is empty → trigger the **Quick Start** onboarding flow above.
- **NEVER** show raw Python tracebacks to the user. Always catch errors and provide friendly guidance.

## Mandatory Workflow

### Step 0: First-Time Check (ALWAYS run first)
- Run `python scripts/onboarding.py` to check setup status.
- If it shows ❌ or ⚠️ → trigger the **Quick Start** onboarding flow above.
- If all ✅ → proceed to Step 1.

### Step 1: Identify Intent
Check for these trigger patterns:

| Pattern | Action |
|---------|--------|
| `/context-wizard <link>` | Save document to context |
| `/context-wizard <question>` | Search context and answer |
| "保存上下文" / "存入上下文" / "保存到业务上下文" | Save document to context |
| Feishu doc link + save intent | Save document to context |
| "XX 项目里关于 XX" / "做过什么决策" | Search context and answer |
| Open-ended question about past work | Global search (Mode B) |

Identify the input type: Feishu Doc/Sheet link, image, or text.
Identify the `Entity Type`: Project, Client, Partner, Product.
Identify the `Project Name`: The name of the project this context belongs to.

### Automation Mode: Feishu Drive Auto Scan
When the user asks to 自动扫描 / 定时扫描 / 扫描飞书文档 / 自动入库 / review candidates, use the auto-scan scripts.

**Default behavior:** not dry-run. High-confidence candidates are automatically ingested. Medium/low-confidence candidates stay in the review queue.

Commands:

```bash
# Daily freshness scan: recent changes, then ingest high-confidence candidates
python scripts/scan_drive.py --mode daily

# Weekly historical backfill scan: advances the backfill cursor
python scripts/scan_drive.py --mode weekly

# Check queue counts
python scripts/ingest_candidates.py status

# Show candidates that need review
python scripts/ingest_candidates.py review

# Approve selected candidates and optionally ingest immediately
python scripts/ingest_candidates.py approve <candidate_id> --ingest

# Skip selected candidates
python scripts/ingest_candidates.py skip <candidate_id>
python scripts/ingest_candidates.py approve --all --ingest

# Force rescan for an existing candidate ID, token, or URL
python scripts/ingest_candidates.py rescan <candidate_id_or_token_or_url> --ingest
```

Auto-scan uses `lark-cli drive +search --as user`, so user identity must be configured. If scanning fails with an auth/config error, guide the user to run:

```bash
lark-cli config init --new
lark-cli auth login --recommend --no-wait
lark-cli auth login --scope "search:docs:read" --no-wait
```

If Feishu says the app is pending approval for `search:docs:read`, wait for the Feishu app/admin approval and retry the scan after approval.

Auto-scan uses post-review by default: discovered resources are written into one or more topic tables with `状态`, `扫描分数`, `扫描原因`, and `入库方式`. Users can later mark bad records as `失效` or delete them directly in Feishu Base. State is stored in `.context_wizard/scan_state.jsonl`; historical backfill progress is stored in `.context_wizard/backfill_cursor.json`.

### Step 2: Delegate Extraction (CRITICAL STEP)
- **Do NOT** run extraction scripts or read content in this main session.
- **Spawn a Sub-Agent** with the following specific task:
  > "Run the command `python scripts/extract_data.py <link_or_text>`. The output will contain the raw content. 
  > 
   > Parse this content and extract ONLY the following fields into a JSON object:
   > - `project_name` (**主项目名**：业务线/产品/客户的主名称，如「星选咖啡」。用于匹配/创建多维表格表名)
   > - `entity_name` (**具体实体名**：文档标题/会议名/事件名，如"630项目复盘"、"Q2 需求评审会")
   > - `doc_type` (文档类型: 会议纪要/需求文档/复盘报告/运营方案/合作协议/数据分析/竞品调研/其他)
   > - `core_conclusion` (核心结论/决策/发现，简明扼要)
   > - `key_time` (文档提及的关键时间节点，如"2026 Q2"、"630 大促"、"5月15日")
   > - `people` (涉及的关键人员/团队，逗号分隔)
   > - `tags` (关键词标签，便于检索，逗号分隔)
   > - `source_link` (The original link or source description)
   > - `doc_token` (Extract from link, or 'N/A' if not a Feishu doc)
  > 
  > **重要判断规则**：
  > - 如果文档标题包含日期/阶段/版本号（如"630"、"Q2"、"v2.0"），那通常是具体实体名
  > - 主项目名是更上层的业务线名称
  > - 例：「context-wizard 630项目复盘」→ project_name="context-wizard", entity_name="630项目复盘"
  > 
  > Return ONLY the valid JSON object."
- **Receive Result**: The Sub-Agent returns the clean JSON.

### Step 3: Prepare Target Table (One Project = One Table)
- **Identify Project Name**: Use the `project_name` from extraction.
- **Ensure Table Exists**: Run `python scripts/get_or_create_table.py <project_name>`.
  - The script automatically uses the `base_token` from `config.json`.
- **Receive Result**: The script returns the `table_id` for that project table.

### Step 4: Handle Non-Feishu Inputs (Auto-Generate Doc)
- **Check**: Is the input a Feishu Doc/Sheet link?
  - **YES**: Skip this step. Use the original link as `source_link`.
  - **NO** (Image/Text/External Link): 
    1. Call `python scripts/create_doc.py '<entity_name> - Context Note' '<formatted_content>'`.
    2. The script returns a new Feishu Doc URL.
    3. Update the `source_link` to this new Feishu Doc URL.
    *This ensures every record in the Base has a clickable Feishu doc for traceability.*

### Step 5: Preview Card (CRITICAL)
- Display the extracted information to the user:
  ```markdown
  📋 **Context Preview**
  - **项目名**: {project_name}
  - **实体**: {entity_name}
  - **文档类型**: {doc_type}
  - **关键时间**: {key_time}
  - **涉及人员**: {people}
  - **标签**: {tags}
  - **核心结论**: {core_conclusion}
  - **来源**: {source_link}
  
  ✅ 确认保存？（回复修改意见或"确认"）
  ```
- **STOP HERE**. Wait for user confirmation.

### Step 6: Deduplication Check
- Before saving, check if a record with the same `doc_token` already exists in the project table.
- **How to check**: Run `python scripts/search_context.py '<project_name>'` or use `lark-cli base +record-search --base-token <token> --table-id <table_id> --json '{"keyword":"<doc_token>","search_fields":["文档 Token"]}'`.
- **If exists**: Update the existing record using `lark-cli base +record-batch-update` with the found record_id.
- **If new**: Create a new record via `write_context.py`.

### Step 7: Save to Base
- Once confirmed, run `python scripts/write_context.py '<json_data>' <table_id>`.
  - The script automatically uses the `base_token` from `config.json`.
- Report the result and provide the link to the Base.

### Step 8: Dashboard (Auto-Created)
- The dashboard is **automatically created** when a new project table is created.
- It includes: context count, entity type pie chart, status column chart, and project info text.
- No manual action needed — it's part of the `get_or_create_table.py` flow.

## Data Schema
The target Feishu Base has the following fields:
- `实体名称` (Entity Name: Document/Meeting title)
- `文档类型` (Doc Type: 会议纪要/需求文档/复盘报告/运营方案/合作协议/数据分析/竞品调研/其他)
- `核心结论` (Core Conclusion: Key decision/finding)
- `关键时间` (Key Time: Important dates/phases mentioned)
- `涉及人员` (People: Key stakeholders)
- `标签` (Tags: Keywords for search)
- `关联文档` (Source Link: Feishu Doc URL)
- `文档 Token` (Doc Token for deduplication)
- `最后更新` (Last Updated)
- `状态` (Record status: 有效/待复核/失效)
- `扫描分数` (Discovery score)
- `扫描原因` (Scoring rationale)
- `入库方式` (Manual or auto-scan source)
- `候选 ID` (Local scan candidate ID)
- `主题表` (Topic table that received this copy)

## Retrieval Mode (Supports both project-specific and global search)
To prevent context overload when retrieving long histories:

### Mode A: Project-Specific Search (指定项目检索)
When the user explicitly mentions a project name (e.g., "context-wizard项目里关于 XX 的信息").

1. **Identify Intent**: User asks a question about a specific project.
2. **Spawn a Sub-Agent** with the following specific task:
   > "Run the command `python scripts/search_context.py '<Project Name>'`. 
   > 
   > This returns JSON records for that project's context history.
   > The script automatically uses the `base_token` from `config.json`.
   > 
   > **Your Task:**
   > 1. Parse the JSON records.
   > 2. Answer the user's question based ONLY on these records.
   > 3. If records exist, include the `source_link` in your answer.
   > 4. If no records found, say 'No context found for this project.'
   > 
   > **Output:** Return ONLY the answer text. Do NOT return raw JSON."
3. **Main Agent Action**: Receive the summarized answer from Sub-Agent and present it to the user.

### Mode B: Global Smart Search (全局智能检索)
When the user asks an **open-ended question** without specifying a project (e.g., "我们关于优惠券做了什么决策？").

**Flow:**
1. **Extract Keywords**: AI extracts 1-3 core keywords from the user's question.
2. **Call Global Search**: Run `python scripts/global_search.py "<keywords>"`. The script automatically uses the `base_token` from `config.json`.
3. **Receive Results**: The script returns JSON: `{"query": "...", "count": N, "records": [...]}`, with records sorted by `最后更新` in descending order.
   - Each record includes metadata fields:
     - `_project_name`: The project name (table name) the record belongs to. Use this to label the source in your answer.
     - `_table_id`: The Feishu Base table ID of the record.
   - **Error Handling**: If the response contains an `"error"` field, report the error to the user and guide them to run `python scripts/init_base.py` to initialize the base token.
   - **Boundary Handling**: If `count > 20`, inform the user "找到较多相关内容，建议缩小搜索范围" and provide a grouped summary by project.
4. **Synthesize Answer**:
   - If `count == 0`: Reply "未找到相关内容".
   - If `count > 0`: Based on the sorted records, provide a coherent, comprehensive answer organized by **timeline**.
   - If the same entity has multiple records, highlight changes (e.g., "最初计划 X，后在 Y 日期调整为 Z").
   - Append source links at the end, citing the `_project_name` for each referenced item.

## Pitfalls & Lessons Learned
1. **Preview Card Rendering**: Use plain text list format (Step 5 template), NOT markdown tables. Tables fail to render in Feishu mobile.
2. **Context ≠ Project Management**: Schema uses generic fields (文档类型/核心结论/关键时间/涉及人员/标签) — NOT project fields (风险/进度/预算/优先级). Any doc type (meeting notes, requirements, reports, agreements) fits this schema.
3. **Project vs Entity Name**: Table names are project-level (e.g., "context-wizard"), not document-level. Multiple docs from same project accumulate in one table as separate records.
4. **关联文档 Auto-fill**: `write_context.py` auto-generates Feishu doc URLs from `doc_token`. Only skips if token is "N/A" or starts with "TEST_".
5. **Record Updates**: `lark-cli base +record-upsert` without `--record-id` always creates new records. Use `+record-batch-update` with `'{"record_id_list":[...],"patch":{...}}'` for updates.
6. **lark-cli field-create**: Use `--json '{"name":"X","type":"text"}'` — do NOT include `ui_type` or `property` keys (API rejects them).
7. **Field Name Matching**: JSON keys in `record-upsert` must exactly match field names in the table. Use `lark-cli base +field-list` to verify.
8. **Dashboard Block Creation**: Must run serially with `time.sleep(1.5)` between each block to avoid QPS rate limits.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| 首次使用 / 没有 config.json | 运行 `python scripts/init_base.py` 自动创建 Base 和配置文件 |
| Token 过期 / 认证失败 | 运行 `lark-cli auth login --recommend --no-wait` 重新登录 |
| 字段名不匹配 | 运行 `lark-cli base +field-list --base-token <token> --table-id <id>` 查看实际字段名 |
| 表不存在 | 确保 `project_name` 拼写正确，系统会自动创建新表 |
| 提取失败 | 检查文档链接格式是否正确（应为 feishu.cn/docx/xxx） |
| Dashboard 已存在 | 正常，系统会复用已有 Dashboard |

### ⚠️ Windows 用户注意
项目已内置跨平台兼容（GBK 编码 / subprocess shell），但以下事项仍需注意：

| 问题 | 说明 |
|------|------|
| **PowerShell `&&` 不支持** | Windows PowerShell 不支持 `&&` 连接符，请用分号 `;` 或直接分开运行命令 |
| **PowerShell JSON 引号转义** | 在 PowerShell 中直接传递 `--json "{...}"` 时引号会被转义，**建议使用 Python 脚本调用**而非直接敲 lark-cli |
| **命令行参数传递中文** | PowerShell 传递含中文/特殊字符的 JSON 参数可能解析失败，**建议通过脚本内部调用** |

**错误处理铁律**：
- ❌ **禁止** 向用户展示 Python traceback 或 raw JSON 错误
- ✅ **必须** 捕获异常，转换为中文友好提示
- ✅ **必须** 告诉用户下一步该做什么
