---
name: lark-autocontext
description: |
  Trigger when user mentions ANY of: 保存上下文 / 存入上下文 / 业务记忆 / 项目知识 / 存入知识库 / lark-autocontext / /lark-autocontext / 保存到业务上下文 / 扫描飞书 / 同步飞书知识 / 同步飞书知识库 / 搜索知识库 / 查询知识库 / 问一下知识库 / 查一下业务.
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
   - If missing → Run `python scripts/setup.py` (auto-creates config, no manual editing needed).

4. **Check bundle initialized:** Verify `bundle/index.md` exists.
   - If missing → Run `python scripts/init_bundle.py` first.

5. **Check wiki config (if wiki mode):** If `config.json` has `wiki.space_id`, verify `raw_node_token` and `agent_node_token` are also set.
   - If missing → Tell user: "Wiki 模式配置不完整，运行 `python scripts/migrate_to_wiki.py --space-id <id> --raw-node <token> --agent-node <token>` 或手动编辑 config.json 的 wiki 字段"

6. **Check FTS5 search index (if wiki mode):** Verify `bundle/.index/search.db` exists.
   - If missing → Run `python scripts/query_engine.py rebuild` 从已有 OKF 文件构建索引。

Only after ALL checks pass, proceed to the requested workflow.

---

## Wiki Mode vs Folder Mode

**Wiki 模式是 opt-in。** 当且仅当 `config.json` 配置了 `wiki.space_id` 字段时，wiki 模式生效；否则使用 legacy folder 模式。两种模式都必须支持，文档并存。

### Wiki 模式架构

- **Raw docs area（人类写入区）**：由 `wiki.raw_node_token` 指定。人类把原始飞书文档丢进这个 Wiki Space 节点下，AI Agent 只读不写。
- **Agent maintenance area（Agent 维护区）**：由 `wiki.agent_node_token` 指定。AI Agent 把结构化 OKF 输出写到这里，供人类阅读。
- **Local bundle（本地 OKF Markdown）**：仍然保留，用于快速查询和 FTS5 全文检索。飞书侧仅用于人类可读性。
- **双向同步 + 冲突解决**：采用 "Feishu wins + local backup" 策略——当文档在两侧都被编辑时，拉取飞书版本，本地版本备份到 `bundle/.conflicts/`。

### Folder 模式（legacy）

完全保留不变。`config.json` 没有 `wiki.space_id` 时，所有 Workflow 走原有的 folder/scan_config 路径。Wiki 模式是增量能力，不破坏现有行为。

### 从 folder 模式迁移到 wiki 模式

```bash
python scripts/migrate_to_wiki.py --space-id <space_id> --raw-node <raw_node_token> --agent-node <agent_node_token>
```

迁移脚本会：备份 `config.json` → 写入 `wiki.space_id` / `raw_node_token` / `agent_node_token` → 保留所有现有字段。迁移后按提示运行 `onboarding.py` 验证、`query_engine.py rebuild` 重建索引、`scanner.py --wiki` 测试 wiki 扫描。

---

## 🛑 STOP — 执行前必读 (HARD CONSTRAINTS)

> **此节为最高优先级硬约束，凌驾于所有 Workflow 描述之上。**
> **任何 Workflow (A/B/C/D) 在执行分类步骤前，必须先满足以下三条铁律。**
> **违反任何一条 = 执行失败，必须回滚重来。**

### 铁律 1: 分类必须由 subagent 执行，主对话禁止自己分类

```
禁止行为（一旦出现即判定违规）：
  ❌ 主对话根据文档标题/片段自己写 classified JSON
  ❌ 主对话"为了快"跳过 Task() 调用直接分类
  ❌ 主对话用 grep/扫描前 200 字代替完整阅读
  ❌ subagent 只看 title 不读 content 字段

正确行为：
  ✅ 主对话调用 Task(subagent_type="general_purpose_task", ...)
  ✅ Task prompt 中必须包含 scanner 输出的完整 content 字段
  ✅ subagent 逐篇读取 content 全文后输出 classified JSON
  ✅ subagent 输出必须包含 `"_classified_by": "subagent"` 签名字段
  ✅ 主对话只负责传参和验收，不参与内容判断
  ✅ okf_writer 会校验签名，缺失时打印警告（见 scripts/okf_writer.py _check_subagent_signature）
```

**为什么**：主对话上下文已被多轮对话污染，容易基于标题/片段臆测。
subagent 拥有独立、干净的上下文窗口，能完整阅读长文档并按 Classification Guide 抽取。

### 铁律 2: 主对话必须按 9 项验收清单逐项检查，禁止跳过

subagent 返回 classified JSON 后，主对话**必须**对每一篇文档执行以下检查。
**任何一项不通过 → 打回 subagent 重做，禁止"差不多就放行"。**

```
验收清单 (9 项全过才算合格):
  1. type 非空, Title Case 名词短语 (如 "Meeting Minutes", 不是 "meeting"/"会议")
  2. description 有实质内容 (≥ 10 字, 不是 "本文档是关于..." 模板)
  3. people 与正文实际出现的人名一致 (不多不少)
  4. tags ≥ 2 个, 且与内容相关 (不是 ["文档", "飞书"] 这种万能 tag)
  5. summary 非空 (≥ 20 字, 概括核心结论)
  6. key_points ≥ 1 个 (每个 point 是具体信息, 不是 "本文讨论了 X")
  7. entities 合理 (0-8 个, 每个必须有 type/name/brief 三字段)
  8. filename 合法 (slug, 无空格中文特殊字符, .md 结尾)
  9. resource 是有效飞书 URL (https://feishu.cn/... 或 https://*.feishu.cn/...)
```

**验收输出格式**（主对话必须显式打印，便于追溯）：
```
[验收] 文档 1/3: <title>
  ✅ type: Meeting Minutes
  ✅ description: 142 字
  ✅ people: 3 人 (张三, 李四, 王五)
  ✅ tags: 3 个 (季度规划, OKR, 销售部)
  ✅ summary: 87 字
  ✅ key_points: 4 个
  ✅ entities: 2 个
  ✅ filename: 2024-q4-okr-review.md
  ✅ resource: https://xxx.feishu.cn/docs/xxx
  → 通过
```

### 铁律 3: 禁止任何形式的"绕过"

| 错误做法 | 为什么错 | 正确做法 |
|---------|---------|---------|
| "文档太短不用 subagent" | 短文档也需要按规范抽取 | 仍调 subagent, prompt 里说明文档较短 |
| "批量太多 subagent 慢" | 慢是正常的, 准确性 > 速度 | 仍调 subagent, 可分批 Task() |
| "我自己看一眼就能分" | 主对话上下文已被污染 | 必须用独立 subagent 上下文 |
| "验收太繁琐跳过吧" | 不验收 = 不可信 | 必须逐项打印验收结果 |
| "subagent 返回就直接写" | 返回 ≠ 合格 | 必须先验收, 通过后才调 okf_writer |
| "用 okf_writer 不带 --classified-file" | 等于让 writer 自己猜分类 | 必须先 subagent 分类, 再 --classified-file 传入 |

### 自检 checklist (执行任何 Workflow 前, 主对话必须默念确认)

```
□ 我即将调用 Task() 启动 subagent, 而不是自己分类
□ 我会把 scanner 输出的完整 content 传给 subagent
□ subagent 返回后, 我会按 9 项清单逐项验收
□ 验收通过前, 我不会调用 okf_writer
□ okf_writer 调用会带 --classified-file 参数
```

**如以上任何一项无法确认 → STOP, 重新阅读对应 Workflow 章节。**

---

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
  → 自动提取 → AI分类 → 生成OKF Markdown → 入库 → 自动更新可视化

方式 2️⃣  批量扫描
  "扫描飞书文档" / "同步飞书知识"
  → 批量提取 → AI分类 → 生成OKF Markdown → 入库 → 自动更新可视化

方式 3️⃣  查询上下文
  "XX 项目里关于优惠券做了什么决策？"
  → 查询OKF Bundle → 返回结果

方式 4️⃣  可视化
  打开 bundle/viz.html 查看知识图谱
  → 节点 = 文档/人物/概念/实体，边 = 交叉引用

━━━━━━━━━━━━━━━━━━━━━━

⚡ **首次使用需要初始化，我会自动帮你搞定一切：**

1. 检查 lark-cli 是否安装并登录飞书
2. 创建配置文件（不需要你手动编辑 JSON）
3. 初始化 OKF Bundle 知识库目录

唯一前提：需要先安装 lark-cli（飞书命令行工具）
  npm install -g @larksuiteoapi/lark-cli

确认后输入 ✅ 或 "开始"，我来完成剩下的。
```

**After user confirms**, the Agent runs `python scripts/setup.py` automatically.

> **setup.py 会自动完成：**
> 1. 检查 lark-cli 安装状态和飞书登录状态
> 2. 从 example 复制 config.json（无需手动填 token，认证通过 lark-cli 完成）
> 3. 交互式创建 scan_config.json（用户粘贴飞书文件夹/wiki 链接，脚本自动识别）
> 4. 初始化 OKF Bundle 目录结构
>
> **用户不需要手动编辑任何 JSON 文件，也不需要知道什么是 token。**

**If lark-cli not installed:**
```
⚠️ 需要先安装 lark-cli（飞书命令行工具）：
  npm install -g @larksuiteoapi/lark-cli
  lark-cli auth login --recommend --no-wait
安装并登录后告诉我，我继续初始化。
```

**If lark-cli not logged in:**
```
⚠️ lark-cli 还没登录飞书，需要先授权：
  lark-cli auth login --recommend --no-wait
在浏览器完成授权后告诉我，我继续初始化。
```

**After setup.py succeeds**, show:
```
✅ 初始化完成！
📦 OKF Bundle 已就绪
📊 可视化: bundle/viz.html (每次写入自动更新)
📝 现在可以开始保存上下文了
```

Then **wait for the user's next message**.

### Agent 自动化初始化（非交互模式）

当 Agent 需要在非交互场景下初始化时（如安装后自动执行）：

```bash
# 不带扫描源（后续用户发链接时再添加）
python scripts/setup.py --auto

# 带扫描源（用户已经提供了飞书 URL）
python scripts/setup.py --auto --sources "https://xxx.feishu.cn/drive/folder/ABC123" "https://xxx.feishu.cn/wiki/DEF456"
```

---

This skill guides the agent to extract, classify, and store business context from Feishu documents into an OKF-compliant Markdown Bundle.

## 🔧 Configuration

Scripts automatically load `scripts/config.json` for bundle path and optional Feishu config.
Batch scanning requires `scripts/scan_config.json` (created automatically by `setup.py` when user pastes Feishu links).

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

### Step A1: Extract Document Content (主对话)
- **Folder 模式：** Run `python scripts/scanner.py --doc "<url>"`.
- **Wiki 模式：** Run `python scripts/scanner.py --doc "<url>" --wiki`. Scanner 通过 `WikiConnector.fetch_doc_content()` 提取正文、`WikiConnector.fetch_doc_meta()` 提取元信息。
- The script extracts content from Feishu and returns JSON with `source_type`, `doc_token`, `title`, `url`, `content`.
- **主对话保留 scanner 输出**，作为 subagent 的输入和验收的基准。

### Step A2: Classify Document via Subagent (subagent 执行，主对话验收)

> **强制规则**：分类必须由 subagent 执行，主对话负责验收。禁止主对话自己分类。
> 原因：主对话自己分类时容易"走捷径"——不认真读内容、people 填空、key_points 随便写。
> subagent 独立执行可以确保 LLM 完整读取文档内容并按 Classification Guide 抽取。

**主对话操作**：启动 subagent，传入 scanner 输出的完整 content：

```
Task(subagent_type="general_purpose_task",
  description="Classify Feishu doc",
  query="""你是一个文档分类专家。请对以下飞书文档内容执行分类，严格按照 SKILL.md 的 Classification Guide 输出 JSON。

文档内容：
---
{scanner 输出的 content 字段，完整传入}
---

文档元信息：
- title: {scanner 输出的 title}
- url: {scanner 输出的 url}
- doc_token: {scanner 输出的 doc_token}

要求：
1. 完整阅读文档内容，不要跳读
2. 按 Classification Guide 抽取所有字段（type/project/title/description/tags/people/concepts/entities/summary/key_points/decisions/action_items）
3. people 必须从正文实际出现的人名中提取，不能为空数组（除非文档确实没提到人）
4. description 必须有实质信息，不能是 "{type} - {title}" 模板
5. entities 按 §5 的三条判据抽取，宁缺勿滥
6. 输出纯 JSON，不要包裹在 markdown code block 里
7. 必须在 JSON 中包含 `"_classified_by": "subagent"` 字段（okf_writer 会校验此签名，缺失会触发警告）""",
  response_language="Chinese")
```

**图片描述规则**（subagent 在分类时执行）：

检查 content 中的图片引用 `![alt](url)`：

1. 如果 alt text 已有描述（非空、非纯文件名）→ 保留不动
2. 如果 alt text 为空 `![](url)` 或仅是文件名：
   a. 如果你具备视觉识图能力，尝试描述图片内容，填入 alt text
   b. 如果你无法识别图片，标注为 `![图片内容待补充](url)`
3. 对于画板预览图 `![画板预览图](.assets/xxx.png)`：
   a. 如果你具备视觉识图能力，描述画板中的流程、结构、数据
   b. 如果你无法识别，保留 `画板预览图` 作为 alt text
4. 描述要求：一句话概括图片核心信息（谁/什么/做什么），不超过50字

### Step A3: 验收分类结果 (主对话执行)

> **强制规则**：主对话必须按以下清单逐项验收，任何一项不通过就打回 subagent 重做。

**验收清单**：

| # | 检查项 | 通过标准 | 不通过处理 |
|---|--------|---------|-----------|
| 1 | `type` 非空 | 是 Title Case 名词短语，不是空字符串 | 打回重做 |
| 2 | `description` 有实质内容 | 不是 `"{type} - {title}"` 模板，不是空 | 打回重做 |
| 3 | `people` 与正文一致 | 扫描正文出现的人名，与 people 数组比对；如果正文有人名但 people 为空 → 不通过 | 打回重做 |
| 4 | `tags` ≥ 2 个 | 至少 2 个关键词 | 打回重做 |
| 5 | `summary` 非空 | 1-3 句摘要 | 打回重做 |
| 6 | `key_points` ≥ 1 个 | 至少 1 个要点 | 打回重做 |
| 7 | `entities` 合理 | 每个实体都有 type/name/brief；数量在 0-8 之间 | 打回重做 |
| 8 | `filename` 合法 | 从 title 派生，无非法字符 | 打回重做 |
| 9 | `resource` 是有效 URL | 是飞书文档 URL | 打回重做 |

**验收通过后**，主对话将 classified JSON 写入临时文件，进入 A4。

### Step A4: Generate OKF Markdown (主对话执行)
- 将 classified JSON 写入临时文件 `_classified.json`，raw content 写入 `_content.md`。
- Run `python scripts/okf_writer.py --classified-file _classified.json --content-file _content.md`。
- **禁止用位置参数传 JSON**（见 Pitfall #8）。
- 检查 okf_writer 输出的 JSON，确认 `status: "created"` 或 `"updated"`。
- 清理临时文件。

### Step A4.5: Sync to Feishu (wiki 模式专属)
> 仅当 `config.json` 配置了 `wiki.space_id` 时执行。Folder 模式跳过本步。

OKF 写入本地后，把结构化输出同步到飞书 Agent 区，供人类阅读（飞书版本用 emoji 元数据头替代 YAML frontmatter）。

**主对话操作**：运行一段 Python 片段，调用 `DualStorage.sync_to_feishu(okf_path, okf_content)`：

```python
import json, sys
sys.path.insert(0, "scripts")
from dual_storage import DualStorage
from scanner import _get_wiki_connector

cfg = json.load(open("scripts/config.json"))
conn = _get_wiki_connector()  # 从 config.json 读取 wiki 配置构建 WikiConnector
storage = DualStorage(cfg["bundle_path"], wiki_connector=conn)
# okf_path: okf_writer 返回的 file_path（相对 bundle 的路径）
# okf_content: 写入的 OKF Markdown 全文（含 frontmatter）
result = storage.sync_to_feishu(okf_path, okf_content)
print(result)  # SyncResult(success, action=created/updated, node_token, feishu_url)
```

`sync_to_feishu` 会：解析 frontmatter 取 title → `okf_to_feishu_content` 转换（剥掉 YAML frontmatter，加 emoji 元数据头）→ 查 `sync_state.json` 决定 `create_doc` 还是 `update_doc` → 成功后更新 sync_state。

### Step A5: Show Save Summary
```
✅ 已保存到 OKF Bundle
📄 文件: projects/{project}/{category}/{filename}
🏷️ 类型: {type}
📝 描述: {description}
🔗 来源: {resource}
👥 人物: {people}
💡 实体: {entities}
📊 可视化: viz.html (已自动更新)
```
> okf_writer 每次写入后会自动重新生成 viz.html，无需手动触发。

### Step A6: Commit to Git
- Run `git add bundle/` and `git commit -m "docs: save {title} to OKF Bundle"`.

> **索引自动更新**：`okf_writer` 写入 OKF 文件后自动调用 `query_engine.QueryEngine.update_index(target_path)`，FTS5 索引无需手动维护。

---

## Workflow B: Batch Scan

**Trigger:** User says "扫描飞书文档" / "同步飞书知识" / "批量导入".

### Step B1: Run Scanner (主对话)
- **Folder 模式：** Run `python scripts/scanner.py` (reads scan_config.json automatically).
- **Wiki 模式：** Run `python scripts/scanner.py --wiki`. Scanner 通过 `WikiConnector.list_raw_docs()` 列出 raw 区所有文档，再逐篇 `fetch_doc_content()` 拉取。
- The script scans all configured Feishu sources and returns JSON with `documents` array.
- **主对话保留 scanner 输出**，作为 subagent 输入和验收基准。

### Step B2: Classify All Documents via Subagent (subagent 执行，主对话验收)

> **强制规则**：分类必须由 subagent 执行，主对话负责验收。禁止主对话自己分类。
> 批量场景下，subagent 逐篇读取内容并分类，返回 classified JSON 数组。

**主对话操作**：启动 subagent，传入 scanner 输出的完整 documents 数组：

```
Task(subagent_type="general_purpose_task",
  description="Batch classify Feishu docs",
  query="""你是一个文档分类专家。请对以下飞书文档逐一执行分类，严格按照 SKILL.md 的 Classification Guide 输出 JSON 数组。

文档列表（共 {N} 篇）：
{scanner 输出的 documents 数组，每篇包含 content/title/url/doc_token}

要求（每篇都必须）：
1. 完整阅读每篇文档内容，不要跳读
2. 按 Classification Guide 抽取所有字段
3. people 必须从正文实际出现的人名中提取
4. description 必须有实质信息，不能是 "{type} - {title}" 模板
5. entities 按 §5 三条判据抽取，宁缺勿滥
6. 输出 JSON 数组，每个元素对应一篇文档，不要包裹在 markdown code block 里
7. 每个 JSON 元素必须包含 `"_classified_by": "subagent"` 字段（okf_writer 会校验此签名，缺失会触发警告）""",
  response_language="Chinese")
```

**图片描述规则**（subagent 在分类时执行）：

检查 content 中的图片引用 `![alt](url)`：

1. 如果 alt text 已有描述（非空、非纯文件名）→ 保留不动
2. 如果 alt text 为空 `![](url)` 或仅是文件名：
   a. 如果你具备视觉识图能力，尝试描述图片内容，填入 alt text
   b. 如果你无法识别图片，标注为 `![图片内容待补充](url)`
3. 对于画板预览图 `![画板预览图](.assets/xxx.png)`：
   a. 如果你具备视觉识图能力，描述画板中的流程、结构、数据
   b. 如果你无法识别，保留 `画板预览图` 作为 alt text
4. 描述要求：一句话概括图片核心信息（谁/什么/做什么），不超过50字

### Step B3: 验收分类结果 (主对话执行)

> **强制规则**：主对话必须对每篇分类结果按验收清单逐项检查。

**验收清单**（同 Workflow A Step A3）：

| # | 检查项 | 通过标准 | 不通过处理 |
|---|--------|---------|-----------|
| 1 | `type` 非空 | Title Case 名词短语 | 打回该篇重做 |
| 2 | `description` 有实质内容 | 不是模板、不是空 | 打回该篇重做 |
| 3 | `people` 与正文一致 | 正文有人名时 people 不能为空 | 打回该篇重做 |
| 4 | `tags` ≥ 2 个 | 至少 2 个关键词 | 打回该篇重做 |
| 5 | `summary` 非空 | 1-3 句摘要 | 打回该篇重做 |
| 6 | `key_points` ≥ 1 个 | 至少 1 个要点 | 打回该篇重做 |
| 7 | `entities` 合理 | 每个有 type/name/brief，0-8 个 | 打回该篇重做 |
| 8 | `filename` 合法 | 从 title 派生 | 打回该篇重做 |
| 9 | `resource` 有效 URL | 飞书文档 URL | 打回该篇重做 |

**全部通过后**，进入 B4。如有不通过，将不通过的篇目重新交给 subagent 重做。

### Step B4: Batch Write All Documents (主对话执行)

> **批量模式**：使用 `--batch-file` 一次性写入所有文档，viz.html 只在最后生成一次（不是每篇都生成）。

**主对话操作**：
1. 将验收通过的 classified JSON 数组 + 对应 raw_content 组装成批量文件 `_batch.json`：
   ```json
   [
     {"classified": {...}, "raw_content": "..."},
     {"classified": {...}, "raw_content": "..."}
   ]
   ```
2. Run `python scripts/okf_writer.py --batch-file _batch.json`
3. 检查输出的 `batch_count` 和 `results` 数组，确认每篇 `action` 为 `Creation` 或 `Update`。
4. 清理临时文件 `_batch.json`。

> **性能对比**：
> - 旧方式（逐篇）：10 篇 = 10 次 Python 启动 + 10 次 viz.html 生成
> - 新方式（批量）：10 篇 = 1 次 Python 启动 + 1 次 viz.html 生成

### Step B4.5: Sync to Feishu (wiki 模式专属)
> 仅当 wiki 模式生效时执行。对 B4 批量写入的每一篇 OKF 文档，调用 `DualStorage.sync_to_feishu(okf_path, okf_content)` 同步到飞书 Agent 区。调用方式同 Workflow A Step A4.5；批量场景下可在同一 Python 进程内循环调用，复用同一个 `DualStorage` 实例。

### Step B5: Show Scan Summary
```
✅ 扫描完成
📊 总计: {total} 个文档
🆕 新增: {created} 个
🔄 更新: {updated} 个
⚠️ 失败: {failed} 个
📊 可视化: viz.html (已自动更新)
```
> 批量模式下 viz.html 只在所有文档写入完成后生成一次。

### Step B6: Commit to Git
- Run `git add bundle/` and `git commit -m "docs: batch scan {total} documents to OKF Bundle"`.

---

## Workflow C: Query Context

**Trigger:** User asks a question about stored context.

> 搜索基于 SQLite FTS5 全文检索，支持 CJK 分词（连续中文字符会被正确切分为单字 token）。命令统一用 `query_engine.py`（旧 `query.py` 仅作 substring fallback 保留）。

### Mode 1: Project-Scoped Query
When user mentions a specific project (e.g., "lark-autocontext项目里关于 XX 的信息"):
- Run `python scripts/query_engine.py search --query "<keyword>" --project "<project_name>" --top-n 10`.
- Parse the JSON results (`matches` 数组，含 `title` / `doc_type` / `score` / `snippet` / `local_path`) and answer the user's question.
- Include `resource` links for traceability.

### Mode 2: Global Search
When user asks an open-ended question (e.g., "最近有什么关于 OKF 的讨论？"):
- Run `python scripts/query_engine.py search --query "<keyword>" --top-n 10`.
- Results 按综合得分（FTS 相关度 × 0.6 + 时间衰减 × 0.2 + 类型权重 × 0.2）降序排列。
- 默认开启 deep read，`context` 字段已拼装好匹配文档全文供 Agent 直接使用。

### Mode 3: Type Filter
When user asks for a specific type (e.g., "给我所有会议纪要"):
- Run `python scripts/query_engine.py search --query "*" --type "Meeting Minutes"`.
- 也可叠加 tags 过滤：`--tags "tag1,tag2"`。
- Return the list of matching documents.

### Mode 4: Bidirectional Sync Check (wiki 模式专属)
当用户问 "飞书那边有没有更新" / "飞书上改了什么" / 类似意图时：
1. Run `python scripts/auto_sync.py list-only` 自动检测 raw 区变更（wiki 模式下脚本从 config.json 识别并调用 `WikiConnector.list_raw_docs(since=last_scan)`）。
2. 调用 `DualStorage.detect_feishu_edits()` 识别 Agent 区哪些文档被人类在飞书侧编辑过，返回 `SyncItem` 列表（`action_needed="pull"` 或 `"unknown"`）。
3. 对需要拉取的文档调 `DualStorage.pull_from_feishu(node_token)`。

### Other Commands
- **带结构化过滤：** `python scripts/query_engine.py search --query "<keyword>" --project "<project>" --type "<type>" --tags "tag1,tag2" --people "<name>"`。
- **Deep read 模式：** `python scripts/query_engine.py search --query "<keyword>"`（deep read 默认开启，`context` 字段含匹配文档全文；加 `--no-deep-read` 关闭，仅返回 snippet 用于快速浏览）。
- **重建索引：** `python scripts/query_engine.py rebuild`。
- **索引状态：** `python scripts/query_engine.py status`。

### Getting Full Content
- Search 结果的 `matches` 含 `snippet`（FTS5 高亮摘录）和 `local_path`。
- 默认 deep read 模式下 `context` 字段已包含匹配文档全文；关闭 deep read 时，需要全文则 Read `.md` 文件 at `bundle/{local_path}`。

---

## Workflow D: Auto-Sync (自动同步)

**Trigger:** Agent 定时任务调用 / 用户说"扫一遍飞书" / "同步飞书知识".

### Step D1: List Changed Documents (主对话)
- Run `python scripts/auto_sync.py list-only --config config.json`.
- **Wiki 模式：** 脚本自动从 `config.json` 识别 wiki 模式，调用 `WikiConnector.list_raw_docs(since=last_scan)` 列出 raw 区自上次扫描以来变更的文档（folder 模式仍走 scan_config.json）。
- This produces `.auto_sync/pending_changes.json` with all changed docs since last sync.
- 主对话读取变更列表，逐篇调 `scanner.py --doc "<url>" --wiki`（wiki 模式）或 `scanner.py --doc "<url>"`（folder 模式）拉取完整内容。

### Step D2: Classify via Subagent + 验收 + Write (subagent 分类，主对话验收+写入)

> **强制规则**：同 Workflow A/B，分类必须由 subagent 执行，主对话负责验收。
> 验收清单同 Workflow A Step A3。

1. **主对话**：对每篇变更文档，调 `python scripts/scanner.py --doc "<url>"` 拉取内容。
2. **主对话**：启动 subagent 对所有变更文档批量分类（同 Workflow B Step B2 的 Task 调用方式）。
3. **主对话**：按验收清单（同 A3）逐篇验收，不通过的打回 subagent 重做。
4. **主对话**：验收通过后，组装 `_batch.json` 用 `--batch-file` 批量写入（同 Workflow B Step B4）。
   - People/Concept/entities 自动 upsert，保留 `# Profile` / `# Definition` 区段。
   - viz.html 只在所有文档写入完成后生成一次。
5. **主对话（wiki 模式专属）**：对每篇写入的 OKF 文档，调 `DualStorage.sync_to_feishu(okf_path, okf_content)` 同步到飞书 Agent 区（同 Workflow A Step A4.5）。

### Step D3: Finalize (主对话)
- Run `python scripts/auto_sync.py finalize --commit`.
- This updates `.auto_sync/state.json` watermarks and commits to git.
- **Wiki 模式额外步骤**：运行 `DualStorage.detect_feishu_edits()` 检查飞书侧是否有人类编辑与本地变更冲突。若发现冲突，本地版本备份到 `bundle/.conflicts/`，飞书版本胜出（被拉取覆盖本地）。

### Step D4: Summary
```
✅ 同步完成
📊 本次: {N} 篇文档
📁 路径: bundle/projects/...
📊 可视化: viz.html (已自动更新)
⏭️  下次自动跳过未变更文档
```
> 批量模式下 viz.html 只在所有文档写入完成后生成一次。

**幂等保证:** 同一 `resource`（doc_token）重跑不会产生重复条目；人工编辑过的 `# Profile` / `# Definition` 区段不会被覆盖。

**冲突解决策略（wiki 模式）："Feishu wins + local backup"** —— 当一份文档在本地和飞书侧都被编辑时，拉取飞书版本覆盖本地，本地旧版本备份到 `bundle/.conflicts/{node_token}_{timestamp}.md`，并在 `bundle/.conflicts/log.md` 追加一条结构化记录（含 node_token / local_path / backup 文件名）。

---

## Query Engine (FTS5 Search)

`scripts/query_engine.py` 提供 SQLite FTS5 全文检索 + 渐进式 RAG，是 Workflow C 的底层引擎。

- **渐进式 RAG（三段式查询）：**
  1. **FTS5 召回** —— 对预处理后的 query 做全文匹配（`documents_fts MATCH ?`）。
  2. **结构化过滤** —— 在 Python 侧按 `project` / `doc_type` / `tags` / `people` / 日期区间过滤。
  3. **Deep read** —— 读取 top-N 匹配文档全文 + 一层 mentions，拼装成 `context` 字符串供 Agent 直接使用（上限 8000 字符）。
- **综合得分：** `FTS_rank × 0.6 + time_decay × 0.2 + type_weight × 0.2`。
  - FTS 相关度：`bm25()` 归一化为 `1.0 / (1.0 + abs(rank))`。
  - 时间衰减：≤30d=1.0，≤90d=0.8，≤180d=0.5，其余 0.3。
  - 类型权重：Meeting Minutes/Decision=1.0，Requirement=0.9，Review/Review Report=0.8，Reference=0.7，其余 0.6。
- **CJK 支持：** 索引和查询都会在连续中文字符之间插入空格（正则 `([\u4e00-\u9fff\u3400-\u4dbf])(?=[\u4e00-\u9fff\u3400-\u4dbf])` → `\1 `），让 `unicode61` tokenizer 把每个汉字切成独立 token，否则整段中文会被当成一个 token、单字查询永远命中不了。
- **CLI 命令：** `search` / `rebuild` / `status`（输出均为 JSON）。详见 Workflow C。
- **索引位置：** `bundle/.index/search.db`（FTS5 虚拟表 `documents_fts` + 元数据表 `documents` + 同步触发器）。
- **自动索引：** `okf_writer` 每次写入 OKF 文件后自动调 `QueryEngine.update_index(target_path)`（best-effort，失败只记 debug 日志，不阻断写入）。首次使用或大量变更后可手动 `python scripts/query_engine.py rebuild` 全量重建。

## Bidirectional Sync

`scripts/dual_storage.py` 的 `DualStorage` 类负责本地 bundle 与飞书 Wiki 之间的双向同步（wiki 模式专属）。

- **sync_state.json：** 每篇文档的同步状态记录在 `bundle/.sync_state.json`（原子写入，损坏时自动回退为空状态）。每条记录含 `local_path` / `feishu_node_token` / `feishu_url` / `local_content_hash` / `feishu_modified_time` / `local_modified_time` / `sync_direction` / `last_sync_at`。
- **同步方向（`SyncDirection`）：** `IN_SYNC`（两侧一致）/ `LOCAL_NEWER`（本地新，需 push）/ `FEISHU_NEWER`（飞书新，需 pull）/ `CONFLICT`（两侧都改，需解决）。
- **Push（本地 → 飞书）：** `DualStorage.sync_to_feishu(okf_path, okf_content)` —— 解析 frontmatter 取 title → `okf_to_feishu_content` 转换（剥 YAML frontmatter、加 emoji 元数据头）→ 查 sync_state 决定 `WikiConnector.create_doc` 还是 `update_doc` → 成功后更新 sync_state（`sync_direction=in_sync`）。失败时不更新 sync_state，文档保持 `local_newer` 下次重试。
- **Pull（飞书 → 本地）：** `DualStorage.pull_from_feishu(node_token)` —— `fetch_doc_content` 拉飞书内容 → `feishu_to_okf_body` 转回 OKF body（剥 emoji 头、清理）→ 复用本地原 frontmatter 拼回完整 OKF → 写入本地 → 更新 sync_state。
- **冲突解决：** "Feishu wins + local backup" —— pull 时比对 sync_state 记录的 `local_content_hash` 与本地文件实际 hash，不一致则把本地旧版本备份到 `bundle/.conflicts/{node_token}_{timestamp}.md` 并在 `log.md` 记录，再覆盖本地。
- **检测飞书侧编辑：** `DualStorage.detect_feishu_edits()` —— `list_agent_docs()` 列出 Agent 区全部文档，逐篇比对 sync_state 中记录的 `feishu_modified_time`，返回需要 pull 的 `SyncItem` 列表（`action_needed="pull"` 或 `"unknown"`）。

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

> **设计哲学**：本项目对齐 [OKF SPEC §1 Non-goals](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md) —— **不预设固定的 type 词表**。
> 飞书用户来自各行各业（产品、运营、法务、研究、教育……），任何硬编码的分类都会逼用户把内容塞进不合身的格子。
> Agent 的职责是 **基于实际内容判断**，并通过"bundle 内复用约束 + consumer 兜底"保持图谱一致。

When you receive document content from the Scanner, analyze it and produce a JSON with the fields below.

### 1. project
Which project does this belong to? Infer from:
- Document title (e.g., "Lark AutoContext 周会" → `lark-autocontext`)
- Content context (mentions of specific project names)
- Folder / wiki space structure if available
- If unclear, ask the user before writing

### 2. type — **open vocabulary, soft-constrained**

`type` 是 OKF SPEC 唯一必填的 frontmatter 字段。它的值应该：

- **2–4 个词的英文 Title Case 或 PascalCase 名词短语**（例如：`Meeting Minutes`、`Design Doc`、`ADR`、`Postmortem`、`Runbook`、`Policy`、`Research Note`、`Contract Clause`）
- 描述"**这是什么**"，不描述"它属于哪个项目"（项目用 `project` 字段表达）
- **跨文档可复用**：同一类内容必须给同一个 `type` 值

#### 优先复用已有 type（强约束）

写入前**先读取** `bundle/index.md` 的 frontmatter，里面有 `types_seen: [...]` 列表（由 okf_writer 自动维护）。

- 如果当前文档**完全可以匹配**已有 type，**必须复用**——不要用同义词造新值（不要今天写 `Meeting Minutes`、明天写 `Meeting Note` 或 `会议纪要`）。
- 只有当文档**确实属于一个新类别**，且这个类别**预计未来还会重复出现**时，才创造新 type。

#### 何时该创造新 type

- 法务用户首次导入合同条款 → 可以创造 `Contract Clause` / `Legal Opinion`
- 研究用户首次导入实验记录 → 可以创造 `Experiment Log` / `Dataset Card` / `Finding`
- 教育用户首次导入教案 → 可以创造 `Lesson Plan` / `Rubric`

#### 常见 type 参考（**非穷举、非强制**，仅作为命名风格参考）

| 场景 | 常见 type 候选 |
|------|---------------|
| 通用协作 | `Meeting Minutes`, `Requirement Doc`, `Design Doc`, `Review Report`, `Operation Plan` |
| 工程研发 | `ADR`, `Runbook`, `Postmortem`, `API Reference`, `Tech Spec` |
| 数据/分析 | `Data Analysis`, `Metric`, `Dataset Card`, `Dashboard Note` |
| 法务/合规 | `Contract`, `Contract Clause`, `Policy`, `Legal Opinion` |
| 研究 | `Research Note`, `Experiment Log`, `Hypothesis`, `Finding`, `Reference` |
| 实体节点（跨文档） | `Person`, `Concept`, `Project`, `Product`, `Feature` |

不要**强行**把内容塞进上表。表里没有的，按上面的命名规则自己起。

### 3. Structured fields (extract from content)

| 字段 | 说明 |
|------|------|
| `title` | 文档标题 |
| `description` | 一句话摘要，必须有实质信息，不能是 `"{type} - {title}"` 这种机械模板 |
| `tags` | 2–5 个关键词（array），用于过滤和搜索 |
| `people` | 文中出现的真实人名（array） |
| `concepts` | 反复出现的术语 / 缩写 / 代号（array） |
| `summary` | 1–3 句正文摘要 |
| `key_points` | 主要要点（array of string） |
| `decisions` | 仅当文档包含明确决议时填，结构 `{decision, owner, deadline}` |
| `action_items` | 仅当文档包含可追踪行动项时填，结构 `{task, owner, due}` |
| `filename` | 从 title 派生，例如 `"2026-06-20 重构讨论"` → `"2026-06-20-重构讨论.md"` |
| `resource` | 原飞书文档 URL（用于去重和回溯） |
| `edited_time` | 飞书原始编辑时间（写入 frontmatter 的 `timestamp`） |

### 4. category — auto-derived (不需要 LLM 决定)

`category` 由 okf_writer 根据 `type` 自动 slugify：
- `Meeting Minutes` → `meeting-minutes`
- `ADR` → `adr`
- `Contract Clause` → `contract-clause`

写出到 `bundle/projects/{project}/{category}/{filename}`。Agent **不需要**显式输出 `category`，传了也会被覆盖。

### 5. Atomic Entities — second-pass extraction (核心增强)

> 这一步是让图谱"活起来"的关键。**不要只把文档本身当节点**，要把文档里出现的**跨文档复用实体**也升格为独立的 OKF 文件。

扫描文档正文，识别出**值得独立成节点**的原子实体。判断标准（三条全部满足）：

1. **跨文档复用可能性高**——这个实体**很可能**在其他文档里被再次提到
2. **有自己的属性**——不只是一个标签词，而是有定义/描述/关系的东西
3. **独立于当前文档存在**——脱离当前文档后仍然有意义

**type 由你自己定**（同样遵循"开放 + 复用"原则）。一些示例（**非穷举**）：

| 用户类型 | 可能抽取出的实体 type |
|---------|---------------------|
| 产品/运营 | `Person`, `Concept`, `Project`, `Product`, `Feature`, `Metric` |
| 法务 | `Person`, `Party`, `Contract`, `Clause` |
| 研究 | `Person`, `Concept`, `Dataset`, `Hypothesis`, `Finding`, `Reference` |
| 工程 | `Person`, `Service`, `API`, `Library`, `Incident` |

对每个抽取出的实体，在最终 JSON 里**直接用对应字段表达**：
- `people: [...]` 会被自动 upsert 为 `bundle/people/{name}.md`
- `concepts: [...]` 会被自动 upsert 为 `bundle/concepts/{name}.md`
- 其他自定义实体类型，写入 `entities` 字段（见下方扩展示例），okf_writer 会自动 upsert 到 `bundle/{slug(type)}/{name}.md`

```json
"entities": [
  {"type": "Metric", "name": "GMV", "brief": "总成交额，运营核心指标"},
  {"type": "Feature", "name": "智能推荐 v2", "brief": "..."},
  {"type": "Project", "name": "618 大促", "brief": "..."}
]
```

**重要**：实体抽取宁缺勿滥。每篇文档的 `entities` 应保持在 0–8 个之间，只挑**真正会被复用**的。把所有名词都升格为节点 = 退化成另一种"灰球海"。

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
  "title": "2026-06-20 重构讨论",
  "description": "讨论 OKF taxonomy 开放化与 Agent 自决实体抽取方案",
  "tags": ["重构", "OKF", "taxonomy"],
  "people": ["张三", "李四"],
  "concepts": ["OKF", "taxonomy"],
  "entities": [
    {"type": "Project", "name": "Lark AutoContext", "brief": "飞书知识库自动归档项目"},
    {"type": "Metric", "name": "节点图谱密度", "brief": "可视化质量指标"}
  ],
  "summary": "团队决定放弃硬编码 type 词表，改为 Agent 基于内容自决",
  "key_points": ["对齐 OKF SPEC 开放性原则", "bundle 自描述 types_seen 列表"],
  "decisions": [
    {"decision": "采用开放 type + 软约束方案", "owner": "张三", "deadline": "2026-06-25"}
  ],
  "action_items": [
    {"task": "重写 SKILL.md Classification Guide", "owner": "李四", "due": "2026-06-22"}
  ],
  "filename": "2026-06-20-重构讨论.md",
  "resource": "https://feishu.cn/docx/abc123",
  "edited_time": "2026-06-20T15:00:00+08:00"
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
├── people/               # People info
├── .index/               # FTS5 search index (search.db) — wiki mode
└── .conflicts/           # Conflict backups + log.md — wiki mode
```

## Pitfalls & Lessons Learned

### 🚫 绝对禁止：绕过标准流程

> **核心教训**：标准流程 scanner → Agent AI 分类 → okf_writer 存在的意义是**质量保障**。
> 每一步都有不可替代的作用：scanner 保证内容提取完整，Agent AI 保证分类准确，okf_writer 保证格式正确。
> **连续三次想走捷径绕过标准流程，每次都产出垃圾数据。不要做第四次。**

| 禁止行为 | 为什么禁止 | 正确做法 |
|---------|----------|---------|
| 硬编码分类信息在脚本里 | LLM 的理解能力被完全浪费，分类质量 = 0 | 走 Workflow A/B 的 AI 分类步骤 |
| 跳过 scanner 直接调 lark-cli | 拿到的内容未经 clean_feishu_content 清理，HTML 混 Markdown | 用 `scanner.py --doc` 或 `--batch` |
| 不读文档内容就分类 | people/key_points/decisions 全是空的，图谱无意义 | Agent 必须完整读取 scanner 输出再分类 |
| 用位置参数传长 JSON 给 okf_writer | 命令行长度限制 + 转义地狱，必然出错 | 用 `--classified-file` + `--content-file` |
| 不配 scan_config.json 就跑 | 里面还是占位符，scanner 会卡死或报错 | 运行 `setup.py`，粘贴飞书链接自动配置 |
| 在 wiki 模式下不 sync 到飞书 | Agent 区只有本地 OKF，人类看不到结构化输出 | 写入 OKF 后调 `DualStorage.sync_to_feishu()` |
| 不 rebuild 索引就搜索 | FTS5 索引为空，搜索无结果 | 首次使用或大量变更后运行 `python scripts/query_engine.py rebuild` |

### 技术注意事项

1. **Preview Card Rendering**: Use plain text list format, NOT markdown tables. Tables fail to render in Feishu mobile.
2. **Deduplication**: OKF Writer uses `resource` field (Feishu doc_token) to detect duplicates. Same doc scanned twice = update, not duplicate.
3. **Filename Safety**: Filenames are sanitized (no `<>:"/\|?*` characters).
4. **Index Updates**: Every write updates the category's index.md and root log.md automatically.
5. **Incremental Scan**: Scanner tracks `last_modified` to skip unchanged documents on subsequent scans.
6. **Windows Encoding**: All scripts force UTF-8 output on Windows to handle Chinese characters and emoji.
7. **drive files list (not +search)**: `fetch_folder_files` uses `drive files list` API, not `drive +search`. The latter requires `search:docs:read` scope that most users don't have.
8. **okf_writer 参数传递**: **NEVER** 用位置参数传 classified JSON。必须用 `--classified-file <path>` + `--content-file <path>`。

## Troubleshooting

| Problem | Solution |
|---------|----------|
| 首次使用 / 没有 config.json | 运行 `python scripts/init_bundle.py` 自动创建 Bundle |
| Bundle 未初始化 | 运行 `python scripts/init_bundle.py` |
| Token 过期 / 认证失败 | 运行 `lark-cli auth login --recommend --no-wait` 重新登录 |
| 扫描配置缺失 | 运行 `python scripts/setup.py`，粘贴飞书链接自动配置 |
| 查询无结果 | 先保存文档或运行批量扫描 |
| 提取失败 | 检查文档链接格式是否正确（应为 feishu.cn/docx/xxx） |
| Wiki 模式未配置 | 运行 `python scripts/migrate_to_wiki.py --space-id <id> --raw-node <token> --agent-node <token>` |
| 搜索索引缺失 | 运行 `python scripts/query_engine.py rebuild` |
| 搜索无结果 | 索引可能为空。运行 `python scripts/query_engine.py rebuild` 从已有 OKF 文件重建索引 |
| 同步冲突 | 本地版本已备份到 `bundle/.conflicts/`，查看 `log.md` 了解详情。飞书版本已被拉取覆盖本地 |

### ⚠️ Windows 用户注意

| 问题 | 说明 |
|------|------|
| **PowerShell `&&` 不支持** | Windows PowerShell 不支持 `&&` 连接符，请用分号 `;` 或直接分开运行命令 |
| **PowerShell JSON 引号转义** | 在 PowerShell 中直接传递 `--json "{...}"` 时引号会被转义，**建议使用 Python 脚本调用** |

**错误处理铁律**：
- ❌ **禁止** 向用户展示 Python traceback 或 raw JSON 错误
- ✅ **必须** 捕获异常，转换为中文友好提示
- ✅ **必须** 告诉用户下一步该做什么
