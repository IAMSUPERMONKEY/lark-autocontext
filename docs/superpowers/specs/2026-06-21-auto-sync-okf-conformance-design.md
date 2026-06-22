# Lark AutoContext: Auto-Sync + OKF v0.1 Full Conformance Design

**Date:** 2026-06-21
**Status:** Approved (pending user review)
**Author:** KitchXia + TRAE
**Supersedes parts of:** `2026-06-20-okf-refactor-design.md`

---

## 1. Motivation

The previous OKF refactor (2026-06-20) established the foundation: scanner / okf_writer / query / SKILL.md. But two gaps remain that block lark-autocontext from living up to its name:

1. **"Auto" 缺失**: All scanning is user-triggered ("保存这个文档" / "扫描飞书"). The project name `lark-**auto**context` implies background automation.
2. **OKF 合规度不足**: Audit against the official OKF v0.1 spec (README + SPEC.md) reveals 4 core gaps + 5 marginal gaps that prevent the Bundle from being a true OKF artifact consumable by external tools (including Google's reference visualizer).

This design closes both gaps in a single iteration, because:
- Auto-Sync (running frequently) without OKF conformance just produces "格式相似的 markdown" at scale — making future migration painful
- OKF conformance without Auto-Sync limits the system to manual operation

### Goals

1. **Auto-Sync 闭环**: One command (`auto_sync.py`) that an Agent can call via cron to scan → classify → write → commit, fully unattended.
2. **OKF v0.1 conformance**: Bundle becomes a valid OKF artifact: cross-links form a graph, body is structured, descriptions are meaningful, people/concepts are first-class entities.
3. **Self-built visualizer**: A single-file `viz.html` generated from the Bundle, mirroring Google's reference implementation, with lark-autocontext-specific dimensions.
4. **Agent-owned schedule**: lark-autocontext does NOT ship a background daemon. Triggering is delegated to Agent-native scheduling (TRAE Schedule / Cursor Tasks / Claude Code cron).

### Non-goals

- Building our own task scheduler (Agent ecosystems handle this)
- MCP server integration (future iteration)
- Vector / semantic search (keyword + graph traversal is sufficient for v1)
- Multi-bundle federation
- Mobile UI for viz.html (desktop-first)

---

## 2. Architecture

```
       ┌──────────────────────────────────────────────────────────────┐
       │  Agent 定时任务 (TRAE Schedule / Cursor / Claude Code cron)   │
       │            "每天 8:00 跑 lark-autocontext 自动同步"             │
       └─────────────────────────────┬────────────────────────────────┘
                                     │ trigger
                                     ▼
                    ┌────────────────────────────────┐
                    │      scripts/auto_sync.py      │  ← 新增协调器
                    └────────────────────────────────┘
                                     │
            ┌────────────────────────┼────────────────────────┐
            ▼                        ▼                        ▼
     ┌─────────────┐          ┌─────────────┐          ┌─────────────┐
     │ scan_config │          │  .state.    │          │ scanner.py  │ (升级)
     │   .json     │          │   json      │          │  增量+清洗  │
     └─────────────┘          └─────────────┘          └─────────────┘
       配置：扫哪些          状态：每源时间戳            变更检测+清洗
                                     │
                                     ▼ 输出"变更文档清单 JSON"
                    ┌────────────────────────────────┐
                    │   Agent (在 Schedule 上下文)    │  ← SKILL.md 引导
                    │   对每篇文档做 AI 分类           │
                    │   + 实体抽取(人物/概念)         │
                    └────────────────────────────────┘
                                     │
                                     ▼ 输出 classified_json
            ┌─────────────────────────────────────────────────┐
            │      scripts/okf_writer.py (升级)                │
            │  写 concept .md（# Summary/Key Points/Decisions） │
            │  upsert people/{name}.md                         │
            │  upsert concepts/{slug}.md                       │
            │  生成 cross-links（绝对路径 + # Related 段）       │
            │  更新 index.md（含 description）                 │
            │  追加 log.md                                     │
            └─────────────────────────────────────────────────┘
                                     │
                                     ▼
                  ┌─────────────────────────────────┐
                  │   scripts/visualize.py 🆕       │
                  │   重新生成 bundle/viz.html       │
                  └─────────────────────────────────┘
                                     │
                                     ▼
                  ┌─────────────────────────────────┐
                  │  git add + commit + push        │
                  │  更新 .state.json                │
                  │  输出标准化报告                  │
                  └─────────────────────────────────┘
```

### 2.1 New Files

- `scripts/auto_sync.py` — 协调器（薄壳，~250 行）
- `scripts/visualize.py` — OKF Bundle 单文件 HTML 查看器生成器（~400 行 Python + 内嵌 ~500 行 HTML 模板）
- `bundle/.state.json` — 增量同步状态（gitignore）
- `bundle/.failed/` — 失败重试目录（gitignore）

### 2.2 Upgraded Files

- `scripts/scanner.py` — 加 `--list-changed --since` 模式 + 飞书 `<callout>` 等私有标签清洗 + 使用飞书 `edited_time` 而非 `datetime.now()`
- `scripts/okf_writer.py` — 加跨链接生成、Body 结构化模板、people/concepts upsert、index.md 带 description
- `SKILL.md` — 新增 Workflow D + Agent 定时配置指南专章 + Classification Guide 升级
- `bundle/index.md` — 加 `okf_version: "0.1"` frontmatter

### 2.3 auto_sync.py 的 4 步契约

```
Step 1: 读 scan_config.json + .state.json
   → 拿到所有源 + 每个源的 last_scan_at

Step 2: scanner.py --list-changed --since <last_scan_at>
   → 输出 [{doc_token, url, title, edited_time, source}, ...]
   → 0 个变更直接退出

Step 3 (Agent 执行): 对每篇变更
   (a) scanner.py --doc <url> → 拉全文 + 清洗
   (b) Agent 按 SKILL.md Classification Guide 分类（含实体抽取）
   (c) okf_writer.py <classified_json> <raw_content>

Step 4: 收尾 (auto_sync.py --finalize)
   visualize.py → 生成 viz.html
   git add bundle/ → git commit → push
   .state.json 写新 last_scan_at（用本次开始时间戳）
   输出标准化报告
```

### 2.4 .state.json 结构

```json
{
  "last_scan_at": "2026-06-21T08:00:00+08:00",
  "sources": {
    "wiki:7234567890123456789": {
      "last_scan_at": "2026-06-21T08:00:00+08:00",
      "last_success": true,
      "last_error": null,
      "consecutive_failures": 0
    },
    "folder:fldcnXXXX": {
      "last_scan_at": "2026-06-20T08:00:00+08:00",
      "last_success": false,
      "last_error": "rate limited",
      "consecutive_failures": 1
    }
  },
  "stats": {
    "total_docs": 42,
    "last_run_created": 1,
    "last_run_updated": 3,
    "last_run_failed": 0
  }
}
```

**关键设计**：每个源独立维护时间戳——某个源失败不影响其他源水位线推进；下次只重试失败源。

---

## 3. OKF v0.1 Conformance Upgrades

Based on the audit against [OKF SPEC.md](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md), 4 core gaps + 5 marginal gaps must be closed.

### 3.1 Core Gap #1: Cross-Link Graph (SPEC §5)

**Problem**: 当前文档之间完全没有引用，违反 OKF 的"graph-shaped, not just tree-shaped"理念。

**Solution**:

新增 frontmatter 字段（OKF 允许的扩展键）:

```yaml
---
type: Meeting Minutes
project: demo-project
people: [Alice, 张三]
concepts: [示例项目, 核心指标]
mentions:                              # 🆕 自动生成的绝对路径链接列表
  - /people/Alice.md
  - /people/张三.md
  - /concepts/示例项目.md
  - /concepts/核心指标.md
  - /projects/demo-project/index.md
---
```

Body 末尾自动追加 `# Related` 段（使用 OKF 推荐的绝对路径）:

```markdown
# Related

* People: [Alice](/people/Alice.md), [张三](/people/张三.md)
* Concepts: [示例项目](/concepts/示例项目.md), [核心指标](/concepts/核心指标.md)
* Project: [demo-project](/projects/demo-project/index.md)
```

**Implementation**: `okf_writer.py` 收到 `classified_json` 后，根据 `people / concepts / project` 字段自动生成 `mentions` 数组和 `# Related` 段。

### 3.2 Core Gap #2: Body Structure (SPEC §4.2)

**Problem**: 当前 body 直接灌飞书原文，含 `<callout emoji="🚀">` 等飞书私有标签，违反 OKF "favor structural markdown" 推荐。

**Solution**:

**Scanner 清洗规则**（在 scanner.py 中实现）:
- 剥离 `<callout>` / `<image>` / `<details>` / `<title>` 等飞书私有标签，保留内部文本
- `<title>X</title>` → `# X`
- 折叠 3 个以上连续空行为 1 个

**AI 分类阶段产出结构化字段**（SKILL.md Classification Guide 强约束）:

```json
{
  "summary": "1-2 句话核心结论",
  "key_points": ["要点1", "要点2", "要点3"],
  "decisions": [{"decision": "...", "owner": "...", "deadline": "..."}],
  "action_items": [{"task": "...", "owner": "...", "due": "..."}],
  "raw_content": "<清洗后的飞书正文>"
}
```

**Body 模板**（okf_writer.py 生成）:

```markdown
# Summary
{summary}

# Key Points
- {point_1}
- {point_2}

# Decisions       ← 仅 Meeting Minutes / Review Report 类型
- **决策**: ... **负责人**: ... **截止**: ...

# Action Items    ← 仅 Meeting Minutes / Requirement Doc 类型
- [ ] {task} — @{owner} — {due}

# Source Content
{cleaned_raw_content}

# Related
{auto-generated cross-links}

# Citations
[1] [飞书原文]({resource})
```

类型不适用的段落跳过。

### 3.3 Core Gap #3: Meaningful Description (SPEC §4.1)

**Problem**: 当前 `description: "Requirement Doc - 示例项目-战略规划讨论-Alice"` 是机械字符串，违反 OKF "single sentence summarizing the concept" 要求。

**Solution**:

SKILL.md Classification Guide 加强约束:

> `description` MUST be a meaningful one-sentence summary (≤80 chars), NOT "{type} - {title}". Used in `index.md` listings for progressive disclosure.

`okf_writer.py` 写入时做长度校验，>100 字符截断到 97 + "…"。如果 description 形如 `^{type} - .*` 直接拒绝并抛出明确错误（强制 Agent 重新生成）。

### 3.4 Core Gap #4: People / Concept Auto-Filing (SPEC implicit)

**Problem**: `people/` 和 `concepts/` 目录是空架子，飞书文档里反复出现的实体没有自己的页面，缺失 OKF 的"reverse linking"维度。

**Solution**:

每次 okf_writer 写一篇 concept 时，遍历 `people[]` 和 `concepts[]` 数组，对每个名字做 upsert。

**`people/{name}.md` 结构**:

```markdown
---
type: Person
title: Alice
description: 在 lark-autocontext 知识库中出现的人物档案
tags: [demo-project]              # 累积出现过的 project
timestamp: 2026-06-21T08:00:00+08:00       # 最近一次提及时间
---

# Profile

<!-- 占位区，供后续人工补充，脚本永不覆盖 -->

# Mentioned In

* [2026-06-20 周会](/projects/demo-project/meetings/2026-06-20-周会.md) - 对齐 Q3 聚焦核心指标
* [战略规划讨论](/projects/demo-project/requirements/战略规划讨论.md) - H2 战略方向确定
```

**`concepts/{slug}.md` 结构**（业务概念档案）:

```markdown
---
type: Concept
title: 示例项目
description: 业务概念档案
tags: [demo-project]
timestamp: ...
---

# Definition

<!-- 占位，供后续人工补充 -->

# Mentioned In

* [...] - ...
```

**Upsert 算法**:
1. 文件不存在 → 创建带占位 `# Profile` / `# Definition` 的骨架
2. 文件存在 → 用"保留区"机制：
   - 重写 frontmatter
   - 重写 `# Mentioned In` 段（去重 + 按时间倒序）
   - **保留 `# Profile` / `# Definition` 等其他段（用户人工内容）**
3. `tags` 累积去重，`timestamp` 取最新

**Slug 生成**:
- 中文 → 直接用中文（OKF 支持 UTF-8 文件名）
- 英文 → kebab-case
- 文件名清洗：去 `<>:"/\\|?*`

### 3.5 Marginal Gaps

| # | 差距 | 升级动作 | 落点 |
|---|---|---|---|
| M1 | timestamp 用真"最后修改时间" | scanner 提取飞书 `edited_time`，传给 okf_writer | scanner.py + okf_writer.py |
| M2 | Body 段落用 OKF 约定标题 | 已在 3.2 解决 | okf_writer.py |
| M3 | index.md 带 description | 生成 index.md 时读取每个子文件的 frontmatter.description 拼上去 | okf_writer.py |
| M4 | 根 index.md 声明 `okf_version: "0.1"` | init_bundle.py 写入；okf_writer.py 永不覆盖根 index.md | init_bundle.py |
| M5 | log.md 日期 ISO 8601 | 已符合 | — |

**根 `bundle/index.md` 升级后**:

```markdown
---
okf_version: "0.1"
title: Lark AutoContext OKF Bundle
description: 飞书业务上下文的 OKF 标准知识库
---

# Lark AutoContext OKF Bundle

## Projects
* [demo-project](projects/demo-project/index.md) - 示例项目业务相关知识

## People
* [Alice](people/Alice.md) - 在 lark-autocontext 知识库中出现的人物档案

## Concepts
* [示例项目](concepts/示例项目.md) - 业务概念档案
```

### 3.6 完整示例

`bundle/projects/demo-project/meetings/2026-06-20-周会.md`:

```markdown
---
type: Meeting Minutes
title: 2026-06-20 示例项目周会
description: 对齐 Q3 聚焦核心指标，Alice确认 Q3 目标
resource: https://feishu.cn/docx/EXAMPLE
tags: [周会, 6月, 核心指标]
timestamp: 2026-06-20T14:30:00+08:00
project: demo-project
people: [Alice, 张三]
concepts: [示例项目, 核心指标]
mentions:
  - /people/Alice.md
  - /people/张三.md
  - /concepts/示例项目.md
  - /concepts/核心指标.md
  - /projects/demo-project/index.md
---

# Summary
2026-06-20 周会确认 Q3 聚焦核心指标提升，Alice承诺 Q3 目标。

# Key Points
- 6 月 GMV 落地 150 万
- 7 月转向核心指标 BI 建设
- 渠道侧暂缓投入

# Decisions
- **决策**: Q3 聚焦核心指标 BI **负责人**: Alice **截止**: 2026-07-31

# Action Items
- [ ] 完成核心指标接口对齐 — @张三 — 2026-06-30

# Source Content
（清洗后的飞书原文 ...）

# Related
* People: [Alice](/people/Alice.md), [张三](/people/张三.md)
* Concepts: [示例项目](/concepts/示例项目.md), [核心指标](/concepts/核心指标.md)
* Project: [demo-project](/projects/demo-project/index.md)

# Citations
[1] [飞书原文](https://feishu.cn/docx/EXAMPLE)
```

---

## 4. Visualizer (scripts/visualize.py)

### 4.1 Goals & Constraints

**Goal**: 生成单文件 `viz.html`，丢给任何人在浏览器里打开即可看到完整知识图谱。

**5 条硬约束**:
1. 单文件（自包含数据 + 样式）
2. 零安装（CDN 引 Cytoscape.js + marked.js）
3. 零后端（纯静态 HTML）
4. 覆盖官方核心特性（力导向图、详情面板、反向链接、搜索、type 过滤、布局切换）
5. 加 3 个 lark-autocontext 专属维度（节点形状区分实体类型、项目色块聚类、时间轴模式）

### 4.2 Visual Encoding

| 维度 | 编码 |
|---|---|
| 节点形状 | 圆 = 普通 concept；菱形 = Person；六边形 = Concept（业务概念）；矩形 = 项目根 `index.md` |
| 节点颜色 | 按 `project` 分配；Person/Concept 用灰白 |
| 节点描边 | 按 `type` 分配（Meeting Minutes 紫、Review 红、Requirement 蓝、Reference 绿、Person 金、Concept 橙、Other 灰） |
| 节点大小 | 基础大小固定；被引用次数（cited_by_count）越多越大 |
| 边颜色 | 灰色细线，鼠标悬停高亮 |
| 边方向 | 有向箭头（A → B 表示 A 提到 B） |

### 4.3 UI Layout

```
┌─────────────────────────────────────────────────────────────────┐
│ Lark AutoContext OKF Bundle (42 concepts)                       │
│ 🔍 [搜索 title / id / tag]   Type: [▾全部] Project: [▾全部]     │
│ 布局: [cose ▾]  时间轴: [□按 timestamp 排序]                    │
├──────────────────────────────────────┬──────────────────────────┤
│                                      │  📄 选中节点详情          │
│        ┌──────────┐                  │  ━━━━━━━━━━━━━━━━━━━━   │
│       O─┤2026-06-20│─O              │  type / title / desc     │
│        └──────────┘                  │  tags / resource         │
│             │                        │                          │
│        ◇ Alice                        │  # Summary               │
│             │                        │  ...                     │
│        ⬡ 示例项目                    │  # Key Points            │
│                                      │  • ...                   │
│   (Cytoscape 力导向图)                │                          │
│                                      │  📥 Cited by (3)         │
│                                      │  • ...                   │
└──────────────────────────────────────┴──────────────────────────┘
```

**搜索框**: 实时过滤 title / concept_id / tags / description，不匹配灰掉。
**Type 下拉**: 动态从 Bundle 所有 type 值生成（多选）。
**Project 下拉**: 动态从所有 project 值生成。
**布局切换**: cose / concentric / breadth-first / circle / grid。
**时间轴模式**: 勾选后切换为 `preset` 布局，X 坐标按 timestamp，Y 坐标按 type 分层。

**详情面板**:
- 上半: frontmatter 渲染（resource 可点击）
- 中段: body markdown 渲染（marked.js）
- body 里的 `/people/Alice.md` 这种内部链接改写为面板内导航
- 下半: 自动计算的 "Cited by" 反向链接列表

### 4.4 Implementation

4 个核心 Python 函数:

```python
def scan_bundle_to_graph(bundle_path: str) -> dict:
    """
    遍历 bundle/ 下所有 .md（排除 index.md / log.md / .state.json）
    返回:
    {
        "meta": {"name", "okf_version", "generated_at", "concept_count"},
        "nodes": [{"id", "title", "type", "project", "description",
                   "tags", "resource", "timestamp", "body_html", "cited_by_count"}],
        "edges": [{"source", "target"}]
    }
    """

def extract_links(body_content: str) -> list[str]:
    """解析 markdown body，提取所有 [text](/path/to/x.md) 内部链接，返回 target concept_id 列表"""

def compute_cited_by(nodes: list, edges: list) -> dict:
    """反向计算每个 node 的 cited_by 列表"""

def render_html(graph_json: dict, output_path: str, name: str):
    """嵌入 graph_json 到 HTML 模板，写出单文件"""
```

**关键技术点**:
- **markdown 预渲染**: 用 Python `markdown` 库在生成时预渲染 body（支持中文 + 避免浏览器端处理）
- **跨链接重写**: HTML 里 `<a href="/people/Alice.md">` 改成 `<a href="#" data-concept-id="people/Alice" class="okf-internal-link">`，JS 拦截点击切换节点
- **时间轴模式**: Cytoscape 切换为 `preset` 布局，X 按 timestamp 线性映射，Y 按 type 分层

### 4.5 CLI Usage

```bash
# 默认: 扫 ./bundle 写 ./bundle/viz.html
python scripts/visualize.py

# 指定 bundle 路径
python scripts/visualize.py --bundle ./custom-bundle

# 指定输出位置 + 自定义标题
python scripts/visualize.py --bundle ./bundle --out /tmp/lark.html --name "Lark AutoContext"
```

### 4.6 Integration with auto_sync.py

auto_sync.py 在 finalize 步骤自动调一次:

```python
# auto_sync.py --finalize 末尾
if changed_count > 0:
    subprocess.run([sys.executable, "scripts/visualize.py", "--bundle", "./bundle"])
    subprocess.run(["git", "add", "bundle/viz.html"])
    # viz.html 合并到 auto-sync commit
```

`bundle/viz.html` 提交进 git，clone 下来直接能打开。

---

## 5. Failure Handling & Idempotency

### 5.1 Five Failure Scenarios

| # | 场景 | 影响 | 对策 |
|---|---|---|---|
| F1 | 飞书 API 限流 / 网络抖动 | scanner.py 单篇失败 | 单篇失败不中断整体，记录到 state.json `sources[X].last_error`，下次重试 |
| F2 | 某个源不可达（wiki 不存在 / 权限丢失） | 整源失败 | 该源 last_scan_at **不推进**，其他源正常推进；连续 3 次失败后输出明显警告 |
| F3 | AI 分类返回非法 JSON | okf_writer 拒收 | auto_sync.py 捕获，原始 scanner 输出写到 `bundle/.failed/{doc_token}.json`，下次重试 |
| F4 | git commit 冲突 | 本地有未提交修改 | auto_sync.py 启动时先 `git status`，有未提交内容跳过 commit（只写文件） |
| F5 | 中途断电 / 进程被杀 | state.json 未写新值 | state.json 永远在所有写入成功后才更新；中断后从旧水位线开始（最多重复处理少量文档） |

### 5.2 Idempotency Guarantees

**核心原则**: 同一篇飞书文档跑 N 次 auto_sync，结果完全一致。

3 个幂等点:

1. **okf_writer 用 `resource` 字段（飞书 doc_token）查找已有文件** → 存在则 Update，不存在则 Create
2. **people / concepts 档案 upsert**:
   - 文件名（slug）确定性（"Alice" 总是映射到 `people/Alice.md`）
   - `# Mentioned In` 列表去重（concept_id 作为唯一键）
   - `tags` 累积去重
   - **用户人工编辑的 `# Profile` / `# Definition` 段落永不被覆盖**
3. **log.md 追加去重**: 同一天同一篇文档的同一种 action 只记一次（用 (date, concept_id, action) 三元组判重）

### 5.3 Standardized Report

auto_sync.py 退出时输出（stdout，给 Agent 在定时上下文里看一眼就懂）:

```
🔄 Lark AutoContext Auto-Sync Report (2026-06-21 08:00:12)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Sources scanned: 3 (✅ 2 ok, ❌ 1 failed)
Documents:
  📥 New:     1 (示例项目 7月规划)
  🔄 Updated: 0
  ❌ Failed:  0
Entities:
  👤 People upserted:   2 (Alice, 张三)
  💡 Concepts upserted: 1 (核心指标)
Git: ✅ committed 1ec3e91 + viz.html
Visualizer: ✅ bundle/viz.html updated

Next scan watermark: 2026-06-21T08:00:12+08:00
Failed sources to retry: wiki:7234567890 (RATE_LIMITED, retry next run)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 5.4 .failed/ Directory

`bundle/.failed/` 是 gitignore 的本地目录:
- `{doc_token}.json` — 分类失败的原始 scanner 输出（含 raw_content）
- `{doc_token}.error` — 失败原因（traceback）

每次 auto_sync 启动时先尝试重处理 `.failed/`，成功就清掉。

---

## 6. SKILL.md & Agent Cron Setup

### 6.1 SKILL.md New Workflow D

在现有 Workflow A/B/C 后追加:

```markdown
## Workflow D: Auto-Sync (定时自动同步)

**Trigger:** Agent 通过定时任务（cron）周期性调用，无需用户手动触发。

### Step D1: 检查环境
- 运行 `python scripts/onboarding.py --quiet` 确认 config / Bundle / scan_config 就绪
- 不就绪直接退出（让用户手动配置）

### Step D2: 列出变更
- 运行 `python scripts/auto_sync.py --list-only`
- 输出 JSON: `{"changed": [{"doc_token", "url", "title", "edited_time", "source"}, ...]}`
- 如 `changed` 为空 → 输出 "🟢 今天没有变更" 并退出

### Step D3: 逐篇分类
对每个 changed 文档:
- 调 `python scripts/scanner.py --doc <url>` 拉全文（清洗后）
- 应用本 SKILL **Classification Guide** 产出 classified_json，必须含:
  - `description`（真摘要，1 句话）
  - `summary` / `key_points` / `decisions` / `action_items`
  - `people` / `concepts`（实体抽取）
- 调 `python scripts/okf_writer.py '<classified_json>' '<raw_content>'`

### Step D4: 收尾
- 运行 `python scripts/auto_sync.py --finalize`
- 该命令: 生成 viz.html → git add → git commit → 更新 .state.json → 输出标准化报告

### Step D5: 报告给用户（如果用户在场）
- 把 Step D4 的报告原样转给用户
- 用户离场（纯后台定时）则只输出到 stdout
```

### 6.2 Agent Cron Setup Guide (专章)

```markdown
## How to Set Up Auto-Sync on Your Agent

> Auto-Sync 是 lark-autocontext 的核心能力之一，但**触发权交给你的 Agent**。
> 我们不造后台进程，因为现在主流 Agent 都自带定时功能。

### 方式 1: TRAE Schedule（推荐）
在 TRAE 对话里说一句即可:
> "每天上午 8 点跑一次 lark-autocontext 的自动同步"

TRAE Schedule 会创建 cron 任务，每天 8:00 在新会话里执行 prompt:
> "运行 lark-autocontext Workflow D (auto-sync)"

### 方式 2: Cursor Tasks / Claude Code cron
- **Cron 表达式**: `0 8 * * *`（每天 8:00）
- **Prompt**: `运行 lark-autocontext 的 Workflow D：扫描飞书变更、自动分类入库、生成 viz.html、commit 并 push`
- **工作目录**: `<你的 lark-autocontext 路径>`

### 方式 3: 手动触发
```bash
cd lark-autocontext
python scripts/auto_sync.py --list-only          # 看变更
# Agent 在循环里逐篇分类调 okf_writer.py
python scripts/auto_sync.py --finalize           # 收尾
```

### 频率建议
| 场景 | 建议频率 |
|---|---|
| 团队日活跃 | 每天 1 次（早 8:00） |
| 中等节奏 | 每 2-3 天 1 次 |
| 知识沉淀型 | 每周 1 次 |

**注意**: 飞书 API 有调用频率限制，**不要把扫描间隔设得短于 10 分钟**。
```

### 6.3 Classification Guide Upgrade

SKILL.md 的 Classification Guide 升级:

**强约束 description**:
> `description` MUST be a meaningful one-sentence summary (≤80 chars), NOT "{type} - {title}".

**新增结构化字段产出要求**:
- `summary`: 1-2 句话核心结论
- `key_points`: 3-5 个关键要点（数组）
- `decisions`: Meeting/Review 类必填，数组 of {decision, owner, deadline}
- `action_items`: Meeting/Requirement 类必填，数组 of {task, owner, due}

**实体抽取规则**:
- `people`: 文档中明确提到的人名（参与者、负责人、提及者）
- `concepts`: 文档中反复出现或加粗强调的业务概念/产品名/项目名（去除项目名本身，因为已在 `project` 字段）

### 6.4 SKILL.md Final Structure

```
1. Quick Start (首次引导)
2. Configuration
3. Mandatory Workflows
   - Step 0: First-Time Check
   - Workflow A: Single Document Save
   - Workflow B: Batch Scan
   - Workflow C: Query Context
   - Workflow D: Auto-Sync 🆕
4. Classification Guide (升级)
5. OKF Bundle Structure (升级)
6. How to Set Up Auto-Sync on Your Agent 🆕
7. Pitfalls & Lessons Learned
8. Troubleshooting (升级)
```

---

## 7. File Structure After Upgrade

```
lark-autocontext/
├── scripts/
│   ├── cli.py                  # 保留
│   ├── scanner.py              # 升级: --list-changed, 清洗, edited_time
│   ├── okf_writer.py           # 升级: 跨链接, Body 结构化, upsert
│   ├── query.py                # 保留
│   ├── init_bundle.py          # 升级: okf_version frontmatter
│   ├── onboarding.py           # 升级: --quiet 模式
│   ├── auto_sync.py            # 🆕 协调器
│   ├── visualize.py            # 🆕 单文件 HTML 生成器
│   ├── config.json.example     # 保留
│   └── scan_config.json.example # 保留
├── bundle/
│   ├── index.md                # 升级: okf_version: "0.1" frontmatter
│   ├── log.md                  # 保留
│   ├── viz.html                # 🆕 自动生成
│   ├── projects/
│   ├── people/                 # 🆕 自动 upsert
│   ├── concepts/               # 🆕 自动 upsert
│   ├── .state.json             # 🆕 gitignore
│   └── .failed/                # 🆕 gitignore
├── docs/superpowers/specs/     # 设计文档
├── SKILL.md                    # 升级
└── README.md                   # 升级
```

---

## 8. Risks & Mitigations

| 风险 | 缓解 |
|---|---|
| AI 分类不稳定（同一文档不同次分类结果不一致） | description 长度校验 + 拒绝机械字符串；Body 结构化字段用宽松 schema；下游用 `resource` 做幂等 |
| 飞书 `edited_time` 不准确（仅权限变更也触发） | 用 `--edited-since` 作为粗筛，下游用 content hash 作为细筛（可选下一轮加） |
| viz.html 在大 Bundle (>500 nodes) 卡顿 | v1 不优化；超过 500 时提示用户用 type/project 过滤；下一轮加分页/虚拟化 |
| 用户在 people/X.md 的 Profile 段手写内容被覆盖 | 用"保留区"机制：脚本只重写 frontmatter + `# Mentioned In`，其他段落原样保留 |
| 飞书私有标签清洗不完整 | scanner.py 用白名单/黑名单组合清洗 + 单元测试覆盖常见标签 |
| Agent 定时 prompt 漂移（同一 prompt 不同 Agent 行为不一致） | SKILL.md Workflow D 写成"机械步骤"，每步明确调哪个脚本输入什么 |

---

## 9. Out of Scope (本轮明确不做)

- MCP server 包装（Query Engine 暴露成 MCP，下一轮）
- 向量检索 / 语义搜索
- 多 Bundle 联邦
- viz.html 移动端适配
- 自动翻译（中英文双语 Bundle）
- 内置 LLM 调用（保持 Agent-driven 分类）
- 飞书 webhook 实时推送
- 飞书评论 / 任务的双向同步

---

## 10. Acceptance Criteria

本轮升级完成的标志:

1. ✅ `python scripts/auto_sync.py --list-only` 能输出飞书变更清单 JSON
2. ✅ Agent 走完 Workflow D 一遍后，bundle/ 下有新的 concept + 自动建档的 people/concepts + 更新的 index/log
3. ✅ `bundle/viz.html` 在浏览器打开能显示力导向图、搜索、过滤、详情面板、反向链接、时间轴模式
4. ✅ `auto_sync.py --finalize` 输出标准化报告，git commit 成功
5. ✅ 同一文档跑 2 次 auto_sync，第 2 次 description/body/mentions 完全一致（幂等）
6. ✅ `bundle/index.md` 含 `okf_version: "0.1"` frontmatter
7. ✅ 每篇 concept .md 含 `# Summary` / `# Key Points` / `# Related` / `# Citations`
8. ✅ SKILL.md 含 Workflow D 完整章节 + Agent Cron Setup 专章
