# lark-autocontext 飞书知识库升级设计

> 基于 WeKnora 架构参考，将 lark-autocontext 从飞书文件夹模式升级为飞书知识库（Wiki Space）模式，实现人机共治的业务知识管理。

---

## 1. 背景与目标

### 1.1 现状

lark-autocontext 当前基于飞书文件夹（folder）获取文档，通过 lark-cli 提取内容，Agent 分类后生成 OKF Markdown 存储到本地 bundle。查询引擎为纯子串匹配，无结构化过滤，无语义理解。

### 1.2 目标场景

```
飞书知识库（Wiki Space）
  ├── 原始文档区（人类随手扔文档，不整理）
  ├── Agent 维护区（AI 自动整理产出：结构化文档 + 知识图谱）
  └── 置顶文档："有问题直接问 Agent"
        ↓
    人类提问 → Agent 自动调用 SKILL → 结构化查询 → 回复
```

三个角色分工：
- **人类**：往知识库扔原始文档 + 有问题直接问 Agent
- **Agent**：自动维护知识库（分类、结构化、生成图谱）+ 回答提问
- **知识库本身**：既是输入源，也是输出载体，还是查询对象

### 1.3 设计约束

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 数据源 | 飞书 Wiki Space | 知识库比文件夹更适合业务知识载体，权限管理完善 |
| 存储模式 | 本地 bundle + 飞书知识库双写 | 本地查询更快，飞书供人类阅读 |
| 知识库组织 | 同空间同层级分区 | 原始区和 Agent 维护区在同一个知识库的不同节点下 |
| 产出形式 | 飞书原生文档（docx） | 人类在飞书里直接阅读编辑 |
| 文档映射 | 1:1 + 主题索引 | 每篇原始文档独立处理，同时按主题生成导航页 |
| 同步触发 | 定时（Agent cron） + 查询时检查 | 复用 Agent 平台 cron 能力 |
| 查询机制 | 渐进式 RAG（参考 WeKnora） | 先搜节点定位，再读全文理解 |
| 编辑同步 | 双向同步 | 飞书编辑需同步回本地 bundle |

---

## 2. 整体架构

### 2.1 模块全景

```
飞书知识库（Wiki Space）
├── 原始文档区（节点）        ← 人类随手扔文档
└── Agent 维护区（节点）      ← Agent 产出
     ├── 结构化文档（飞书 docx）
     └── 知识图谱（HTML 附件）

                    ↕ 读写
        ┌─────────────────────┐
        │  wiki_connector.py   │  ← 飞书 Wiki Space 读写封装
        └──────────┬──────────┘
                   │
        ┌──────────┴──────────┐
        │  dual_storage.py     │  ← 双写协调 + 冲突检测
        └──────────┬──────────┘
                   │
    ┌──────────────┼──────────────┐
    ↓              ↓              ↓
┌────────┐  ┌───────────┐  ┌──────────────┐
│本地bundle│  │okf_writer │  │query_engine  │
│(OKF .md)│  │.py (不变) │  │.py (新)      │
└────────┘  └───────────┘  └──────────────┘
                                │
                          ┌─────┴─────┐
                          │ SQLite FTS5│
                          │ 索引库     │
                          └───────────┘
```

### 2.2 三条核心数据流

**摄入流（人类扔文档 → Agent 整理）：**
```
飞书原始文档区 → wiki_connector.fetch_raw_docs()
  → scanner.clean_content()（复用现有清洗）
  → Subagent 分类（复用现有 SKILL.md Workflow A/B）
  → okf_writer.write_okf() → 本地 bundle（OKF Markdown）
  → dual_storage.sync_to_feishu()
      → OKF Markdown 转飞书 docx → wiki_connector.create_doc()
      → viz.html 生成 → wiki_connector.upload_attachment()
```

**查询流（人类提问 → Agent 回复）：**
```
人类提问 → SKILL.md 激活 → query_engine.search()
  → 阶段1：FTS5 全文索引粗召回（关键词）
  → 阶段2：OKF 结构化字段过滤（type/project/tags）
  → 阶段3：深度读取命中文档全文（参考 WeKnora Mandatory Deep Read）
  → Agent 综合上下文回复
```

**同步流（双向）：**
```
定时触发（Agent cron）:
  → auto_sync.py list-only（检查原始区变更）
  → Agent 分类写入（摄入流）
  → auto_sync.py finalize

查询时检查:
  → dual_storage.detect_feishu_edits()（检查维护区文档的飞书编辑）
  → 有变更 → 拉取飞书内容 → 转回 OKF → 更新本地 bundle → 重建索引
```

### 2.3 与现有模块的关系

| 现有模块 | 变化 |
|---------|------|
| `cli.py` | 飞书读写逻辑迁移到 `wiki_connector.py`，`cli.py` 保留但标记 deprecated，飞书读写逻辑完全迁移到 `wiki_connector.py` |
| `scanner.py` | 保留，数据源从 folder 改为 wiki（调用 `wiki_connector` 而非 `cli`） |
| `okf_writer.py` | 保留不变，新增 `generate_index_pages()` |
| `visualize.py` | 保留不变 |
| `auto_sync.py` | 保留，适配 `wiki_connector` |
| `query.py` | 被 `query_engine.py` 替换 |
| `init_bundle.py` / `onboarding.py` | 保留，小改（配置项变化） |

### 2.4 方案选择：模块化重构（方案 C）

在现有脚本架构上做有针对性的模块化拆分，不过度抽象。三个核心新模块：
1. `wiki_connector.py`：飞书 Wiki Space 读写封装
2. `dual_storage.py`：双写协调器
3. `query_engine.py`：渐进式查询引擎

保留现有 OKF 生成逻辑、可视化逻辑、增量同步协调器。不引入 Web 框架，保持纯 Python 轻量特色。

---

## 3. 模块设计

### 3.1 wiki_connector.py — 飞书 Wiki Space 读写封装

#### 职责边界

飞书知识库的唯一访问出口，所有与飞书 Wiki Space 的交互都通过它。参考 WeKnora 的 Connector 接口思路，但针对飞书 Wiki Space 定制，不做过度抽象。

#### 核心接口

```python
class WikiConnector:
    """飞书知识库读写封装，基于 lark-cli"""

    def __init__(self, space_id: str, raw_node_token: str,
                 agent_node_token: str, identity: str = "user"):
        """
        space_id: 飞书知识空间 ID
        raw_node_token: 原始文档区根节点 token
        agent_node_token: Agent 维护区根节点 token
        identity: lark-cli 身份（user/tenant）
        """
```

#### 读操作（原始文档区）

| 方法 | 功能 |
|------|------|
| `list_raw_docs(since: str = None) -> list[DocInfo]` | 列出原始文档区所有文档，支持时间戳增量 |
| `fetch_doc_content(node_token: str) -> str` | 拉取文档内容（Markdown），调用 `scanner.clean_feishu_content()` 清洗 |
| `fetch_doc_meta(node_token: str) -> DocMeta` | 获取文档元数据（标题、编辑时间、创建者） |
| `list_wiki_subtree(node_token: str) -> list[DocInfo]` | 递归列出某节点下所有子节点（懒加载层级） |

#### 写操作（Agent 维护区）

| 方法 | 功能 |
|------|------|
| `create_doc(parent_node_token: str, title: str, content_md: str) -> str` | 创建飞书 docx，返回 node_token |
| `update_doc(node_token: str, content_md: str)` | 更新已有 docx 内容（全量替换） |
| `upload_attachment(parent_node_token: str, filename: str, file_bytes: bytes) -> str` | 上传文件附件（viz.html） |
| `delete_doc(node_token: str)` | 删除文档（Agent 重组时清理旧产出） |
| `move_doc(node_token: str, new_parent_token: str)` | 移动文档到新节点（主题重组时使用） |

#### 同步检测操作

| 方法 | 功能 |
|------|------|
| `check_doc_changed(node_token: str, last_known_time: str) -> bool` | 检查文档自上次已知时间后是否被编辑 |
| `list_agent_docs(since: str = None) -> list[DocInfo]` | 列出 Agent 维护区的文档（用于检测人类编辑） |

#### 数据结构

```python
@dataclass
class DocInfo:
    node_token: str          # 飞书节点 token（唯一标识）
    title: str
    obj_type: str            # docx / sheet / file
    modified_time: str       # ISO8601，用于增量同步
    url: str                 # 飞书链接
    has_children: bool       # 是否有子节点

@dataclass
class DocMeta:
    title: str
    created_time: str
    modified_time: str
    creator: str
    owner: str
```

#### OKF → 飞书 docx 转换

飞书 docx 不是纯 Markdown，是块结构（Block API）。转换策略：

```python
def okf_to_feishu_blocks(okf_content: str) -> list[dict]:
    """
    OKF Markdown → 飞书 docx blocks
    - YAML frontmatter → 不写入飞书（仅本地保留）
    - # 标题 → heading1/heading2/heading3 block
    - 段落 → text block
    - 列表 → bullet/ordered block
    - 表格 → table block
    - 代码块 → code block
    - 图片链接 → image block（如果是飞书内部链接）
    - [文档链接](url) → 飞书内联 mention（如果 url 是飞书链接）
    """
```

**frontmatter 处理**：飞书文档不包含 YAML frontmatter。frontmatter 仅存在于本地 OKF 文件中。飞书文档正文开头放一个"元数据区"作为人类可读的替代：
```
📝 类型：Meeting Minutes | 项目：lark-autocontext | 标签：重构, OKF
👥 相关人员：张三, 李四 | 📅 2026-06-20
🔗 原始文档：[飞书链接]
---
（正文内容）
```

#### 错误处理

- 飞书 API 限流（429）：指数退避重试，最多 3 次
- 文档不存在（404）：记录日志跳过，不中断批量操作
- 权限不足（403）：明确报错，提示检查 lark-cli 权限范围
- 网络超时：重试 1 次，失败则标记该文档为 `fetch_failed`

### 3.2 dual_storage.py — 双写协调与双向同步

#### 职责边界

本地 bundle 和飞书知识库之间的数据一致性管理。唯一执行"写入飞书"和"从飞书拉取编辑"的模块。

#### 同步状态文件

本地新增 `bundle/.sync_state.json`，记录每个文档的双向同步状态：

```json
{
  "docs": {
    "feishu_node_token_abc123": {
      "local_path": "projects/lark-autocontext/meeting-minutes/2026-06-20-重构讨论.md",
      "feishu_node_token": "abc123",
      "feishu_url": "https://feishu.cn/docx/abc123",
      "local_content_hash": "sha256:abc...",
      "feishu_modified_time": "2026-06-20T15:00:00+08:00",
      "local_modified_time": "2026-06-20T15:05:00+08:00",
      "sync_direction": "in_sync",
      "last_sync_at": "2026-06-20T15:10:00+08:00"
    }
  }
}
```

`sync_direction` 枚举：
- `in_sync`：双方一致
- `local_newer`：本地有更新，待推送到飞书
- `feishu_newer`：飞书有编辑，待拉取回本地
- `conflict`：双方都有修改，需要解决

#### 写入流：本地 → 飞书（Agent 产出）

```python
def sync_to_feishu(okf_path: str, okf_content: str) -> SyncResult:
    """
    Agent 生成 OKF 后调用：
    1. 计算 content_hash
    2. 查 sync_state，判断是新建还是更新
    3. 转换 OKF → 飞书 blocks
    4. 新建：wiki_connector.create_doc() → 记录 node_token
    5. 更新：wiki_connector.update_doc()
    6. 更新 sync_state（local_content_hash, feishu_modified_time, sync_direction=in_sync）
    """
```

图谱同步：`viz.html` 作为附件上传，每次重新生成时覆盖旧文件。

#### 拉取流：飞书 → 本地（人类编辑）

```python
def detect_feishu_edits() -> list[SyncItem]:
    """
    检测 Agent 维护区中哪些文档被人类编辑过：
    1. wiki_connector.list_agent_docs() 获取所有 Agent 文档
    2. 对比 sync_state 中的 feishu_modified_time
    3. 返回 feishu_newer 的文档列表
    """

def pull_from_feishu(node_token: str) -> SyncResult:
    """
    拉取飞书编辑回本地：
    1. wiki_connector.fetch_doc_content() 获取飞书最新内容
    2. 飞书 docx → OKF Markdown 转换（保留本地 frontmatter，仅更新 body）
    3. 检测冲突：比较 local_content_hash 与本地文件实际 hash
       - 一致 → 安全覆盖（本地未改）
       - 不一致 → conflict，标记待解决
    4. 更新本地 OKF 文件 body
    5. 更新 sync_state
    6. 重建 FTS5 索引
    """
```

#### 冲突解决策略

冲突场景：Agent 在本地改了文档，同时人类在飞书也改了同一文档。

**策略：飞书优先 + 保留本地版本**

```python
def resolve_conflict(node_token: str) -> SyncResult:
    """
    冲突时：
    1. 飞书版本覆盖本地（飞书优先，人类编辑意图更明确）
    2. 本地旧版本备份到 bundle/.conflicts/{node_token}_{timestamp}.md
    3. 记录冲突日志到 bundle/.conflicts/log.md
    4. sync_direction = in_sync
    """
```

不在 SKILL.md 中要求 Agent 自动合并冲突。飞书优先 + 本地备份是人类可审计的简单策略。

#### 飞书 docx → OKF 转换

```python
def feishu_to_okf(feishu_content: str, existing_frontmatter: dict) -> str:
    """
    飞书 docx Markdown → OKF Markdown
    - 保留已有 frontmatter（人类编辑不涉及 frontmatter）
    - 飞书导出的 Markdown 清洗（复用 scanner.clean_feishu_content）
    - 跳过飞书正文开头的"元数据区"
    - 重新组装为 OKF 格式
    """
```

#### 触发时机

| 触发场景 | 调用的方法 |
|---------|-----------|
| Agent 摄入新文档后 | `sync_to_feishu()` |
| 定时同步（Agent cron） | `detect_feishu_edits()` → `pull_from_feishu()` |
| 查询前检查 | `detect_feishu_edits()`（轻量检查，有变更才 pull） |
| Agent 重组主题后 | `sync_to_feishu()`（批量） |

#### 原子性保证

- `sync_state.json` 写入用原子操作（先写 `.tmp` 再 `os.replace`，与现有 `auto_sync.py` 一致）
- 推送飞书前先写入本地 → 推送成功后更新 sync_state → 失败则 sync_state 保持 `local_newer`，下次重试
- 拉取飞书前先备份本地 → 覆盖成功后更新 sync_state → 失败则恢复本地备份

### 3.3 query_engine.py — 渐进式查询引擎

#### 职责边界

替换现有 `query.py`（纯子串匹配），实现参考 WeKnora 的渐进式 RAG 工作流。核心原则：先搜节点定位，再读全文理解，最后交由 Agent 回复。

#### 三阶段查询工作流

```
人类提问
  │
  ▼
阶段1：粗召回（FTS5 全文索引）
  │  → 关键词分词匹配，返回 TopN 候选文档
  │  → 仅返回 frontmatter + body 摘要，不返回全文
  ▼
阶段2：结构化过滤与排序
  │  → 按 OKF 字段（type/project/tags/people）过滤
  │  → 按时间衰减 + 相关度评分排序
  ▼
阶段3：深度读取（Mandatory Deep Read）
  │  → 读取命中文档的完整 OKF 内容
  │  → 追踪 mentions 交叉链接，读取关联文档
  │  → 组装为 Agent 上下文
  ▼
Agent 综合上下文回复
```

#### SQLite FTS5 索引设计

新建 `bundle/.index/search.db`（SQLite + FTS5 虚拟表）：

```sql
-- 文档主表
CREATE TABLE documents (
    local_path TEXT PRIMARY KEY,
    feishu_node_token TEXT,
    title TEXT,
    description TEXT,
    doc_type TEXT,
    project TEXT,
    tags TEXT,
    people TEXT,
    body_text TEXT,
    modified_time TEXT,
    content_hash TEXT
);

-- FTS5 全文索引虚拟表
CREATE VIRTUAL TABLE documents_fts USING fts5(
    title,
    description,
    body_text,
    tags,
    people,
    content='documents',
    content_rowid='rowid',
    tokenize='unicode61'
);
```

中文分词说明：FTS5 的 `unicode61` 分词器对中文按字（单字）切分，能覆盖基本的子串匹配需求。如果后续需要更精准的词组匹配，可切换到 `jieba` 分词器（需要 SQLite 扩展），但首版先用 `unicode61` 保持零依赖。

#### 索引构建与增量更新

```python
class QueryEngine:
    def __init__(self, bundle_path: str):
        self.bundle_path = bundle_path
        self.db_path = f"{bundle_path}/.index/search.db"

    def rebuild_index(self):
        """全量重建：扫描 bundle 所有 .md，解析 frontmatter + body，写入索引"""

    def update_index(self, okf_path: str):
        """增量更新：单文档变更时更新索引"""

    def remove_from_index(self, okf_path: str):
        """删除文档时移除索引"""
```

增量索引策略：
- `okf_writer.py` 每次写入后调用 `query_engine.update_index()`
- `dual_storage.pull_from_feishu()` 拉取飞书编辑后调用 `update_index()`
- 索引丢失或损坏时 `rebuild_index()` 全量重建
- `onboarding.py` 启动检查时如果索引不存在则触发全量重建

#### 查询接口

```python
def search(self, query: str, filters: SearchFilters = None,
           top_n: int = 10, deep_read: bool = True) -> SearchResult:
    """
    主查询入口
    query: 自然语言查询
    filters: 结构化过滤条件
    top_n: 粗召回数量
    deep_read: 是否执行深度读取（查询时 True，浏览时 False）
    """

@dataclass
class SearchFilters:
    project: str = None
    doc_type: str = None
    tags: list[str] = None
    people: str = None
    date_from: str = None
    date_to: str = None

@dataclass
class SearchResult:
    matches: list[DocMatch]
    context: str               # 组装好的 Agent 上下文（deep_read=True 时）
    total_found: int

@dataclass
class DocMatch:
    local_path: str
    title: str
    doc_type: str
    score: float
    snippet: str               # 命中片段（FTS5 snippet）
    full_content: str = None   # deep_read=True 时填充全文
    related_docs: list[str] = None  # mentions 关联文档路径
```

#### 评分算法

```python
def calculate_score(self, fts_rank: float, doc_type: str,
                    modified_time: str) -> float:
    """
    综合评分 = FTS相关度 × 0.6 + 时间衰减 × 0.2 + 类型权重 × 0.2

    - FTS相关度：FTS5 bm25() 函数返回值，归一化到 [0,1]
    - 时间衰减：最近30天=1.0，90天=0.8，180天=0.5，更早=0.3
    - 类型权重：Meeting Minutes/Decision=1.0，Requirement=0.9，
              Review=0.8，其他=0.6
    """
```

首版用简单加权，不引入向量检索。后续如需语义匹配，可在 FTS5 之上叠加 embedding 层。

#### 深度读取逻辑

```python
def deep_read(self, matches: list[DocMatch], max_context_chars: int = 8000) -> str:
    """
    参考WeKnora Mandatory Deep Read：
    1. 按 score 降序读取匹配文档全文
    2. 追踪 mentions 中的关联文档，按 score 加权读取（深度限制1层）
    3. 累计上下文不超过 max_context_chars
    4. 组装为结构化上下文
    """
```

#### 命令行接口

```bash
# 基础查询
python scripts/query_engine.py search "重构讨论的结论"

# 带过滤
python scripts/query_engine.py search "活动规则" --project lark-autocontext --type "Requirement"

# 仅粗召回不深度读取（浏览模式）
python scripts/query_engine.py search "OKF" --no-deep-read

# 重建索引
python scripts/query_engine.py rebuild

# 查看索引状态
python scripts/query_engine.py status
```

---

## 4. 飞书知识库分区与 SKILL.md 工作流

### 4.1 飞书知识库分区结构

```
飞书知识空间（Wiki Space）
│
├── 原始文档区（raw_root_node_token）
│   ├── 业务规则_活动机制.md          ← 人类随手扔进来
│   ├── 新人带训流程.png
│   ├── Q2复盘会议纪要.docx
│   └── ...（人类不整理，按时间堆积）
│
└── Agent 维护区（agent_root_node_token）
    ├── {project}/                       ← 按项目分区
    │   ├── meeting-minutes/             ← 按 OKF type 自动创建子节点
    │   │   ├── 2026-06-20-重构讨论       ← 飞书 docx
    │   │   └── 2026-07-01-Q2复盘
    │   ├── requirement/
    │   │   └── 活动规则-v2
    │   ├── review-report/
    │   │   └── 新人带训机制复盘
    │   └── concept/
    │       └── OKF标准
    │
    ├── index/                           ← 主题索引区
    │   ├── 项目导航
    │   ├── 人物档案/
    │   │   └── 张三
    │   └── 概念档案/
    │
    ├── 知识图谱.html                    ← viz.html 上传为附件
    └── _导航首页                        ← Agent 维护区的入口页
```

节点命名规则：
- 文档节点：`{date}-{slugified-title}`
- 类型子节点：`slugify(doc_type)`
- 索引节点：固定名称

### 4.2 SKILL.md 工作流更新

#### Pre-flight Check（更新）

```
Step 0: Pre-flight Check（每次执行前必检）
1. lark-cli 安装与登录（不变）
2. config.json 存在，且包含 wiki_space_id / raw_root_node_token / agent_root_node_token
3. bundle 已初始化
4. query_engine 索引存在（不存在则 rebuild）
5. dual_storage.sync_state 存在（不存在则初始化）
```

#### Workflow A：单文档保存（更新）

```
触发：用户发飞书知识库文档链接 + "保存"

1. wiki_connector.fetch_doc_content(node_token) → 原始内容
2. scanner.clean_feishu_content() → 清洗后 Markdown
3. Subagent 分类（不变，输出分类 JSON）
4. okf_writer.write_okf() → 本地 bundle OKF Markdown
5. dual_storage.sync_to_feishu() → 转换为飞书 docx → 上传到 Agent 维护区
   - 创建类型子节点（如不存在）
   - 创建项目子节点（如不存在）
   - 创建飞书 docx
6. query_engine.update_index() → 更新本地索引
7. visualize 重新生成 viz.html → dual_storage 上传图谱附件
```

#### Workflow B：批量扫描（更新）

```
触发："扫描飞书文档"

1. wiki_connector.list_raw_docs() → 所有原始文档列表
2. 逐篇执行 Workflow A 步骤 1-6
3. 使用 okf_writer --batch-file 批量模式（viz 只生成一次）
4. 生成主题索引页
```

#### Workflow C：查询（重写）

```
触发：人类提问（任何业务相关问题）

1. dual_storage.detect_feishu_edits() → 检查 Agent 维护区是否有人类编辑
   - 有变更 → pull_from_feishu() → update_index()
2. query_engine.search(question, deep_read=True) → SearchResult
3. 如果 total_found == 0：
   - Agent 告知"未找到相关内容"
   - 建议用户将相关文档放入飞书知识库原始文档区
4. 如果有命中：
   - Agent 基于 SearchResult.context 综合回答
   - 回答中引用飞书文档链接
   - 如果关联文档有助于理解，一并引用
```

#### Workflow D：自动同步（更新）

```
触发：Agent cron 定时 / "同步飞书知识"

阶段1 list-only（不变，数据源改为 wiki）：
1. wiki_connector.list_raw_docs(since=last_scan_at) → 变更文档列表
2. 写入 pending_changes.json（不推进水位线）

阶段2 Agent 分类写入（不变）：
3. 对每个变更文档执行 Workflow A 步骤 1-7

阶段3 finalize（更新）：
4. auto_sync.py finalize --commit → 推进水位线 + git commit
5. dual_storage.detect_feishu_edits() → 拉取飞书编辑
6. 生成/更新主题索引页
7. query_engine.rebuild_index()（如有大量变更）
```

### 4.3 主题索引页生成（新增）

Agent 维护区创建索引文档，作为人类浏览的导航入口：

```
# 项目导航

## lark-autocontext

### 会议纪要
- [2026-06-20 重构讨论](飞书链接) — 讨论 OKF taxonomy 开放化方案
- [2026-07-01 Q2复盘](飞书链接) — Q2 进度回顾与 Q3 规划

### 需求文档
- [活动规则 v2](飞书链接) — 2026下半年活动规则更新

## 全部人物
- [张三](飞书链接) — 参与：重构讨论、Q2复盘

## 全部概念
- [OKF标准](飞书链接) — 开放知识格式，定义知识结构化规范
```

索引页由 `okf_writer.py` 的新方法 `generate_index_pages()` 生成，每次批量写入或同步后自动更新，同步到飞书。

### 4.4 触发词更新

原有触发词保留，新增：
- `问一下知识库` / `查一下业务` — 触发 Workflow C
- `同步飞书知识库` — 触发 Workflow D

### 4.5 config.json 完整结构

```json
{
  "wiki_space_id": "7123456789012345",
  "raw_root_node_token": "NdexyB8XabXXXXXX",
  "agent_root_node_token": "WXyzB8XabYYYYYY",
  "identity": "user",
  "scan_sources": [
    {"type": "wiki_raw", "node_token": "NdexyB8XabXXXXXX", "label": "原始文档区"}
  ],
  "auto_sync_enabled": true,
  "auto_sync_cron": "0 9 * * *"
}
```

向后兼容：旧 `folder_token` 字段仍可解析为 `scan_sources` 的一项。

---

## 5. 错误处理、测试与迁移

### 5.1 错误处理矩阵

| 场景 | 处理策略 | 用户体验 |
|------|---------|---------|
| 飞书 API 限流（429） | 指数退避重试（1s→2s→4s），最多 3 次 | Agent 等待后继续，不中断批量操作 |
| 飞书文档不存在（404） | 记录到 sync_state 的 `fetch_failed` 列表，跳过 | Agent 报告"N 篇文档无法访问，已跳过" |
| 飞书权限不足（403） | 立即终止，提示检查 lark-cli 权限 | Agent 报告"权限不足，请运行 `lark-cli auth login --recommend`" |
| lark-cli 未安装/未登录 | Pre-flight Check 拦截 | Agent 引导用户安装或登录 |
| 网络超时 | 单次重试，失败标记 `fetch_failed` | 同 404 处理 |
| 本地索引损坏 | `onboarding.py` 检测，自动 `rebuild_index()` | Agent 报告"索引已重建" |
| sync_state.json 损坏 | 检测 JSON 解析失败 → 全量对比重建 | Agent 报告"同步状态已重建" |
| 飞书 docx 创建失败 | 本地 OKF 保留，`sync_direction=local_newer`，下次重试 | Agent 报告"N 篇文档待重试上传" |
| 冲突未解决 | 标记 `conflict`，查询时跳过冲突文档并提示 | Agent 报告"有 N 篇文档存在编辑冲突，已备份本地版本" |
| 飞书 blocks 转换失败 | 降级为纯文本上传（无格式），记录日志 | Agent 报告"文档已上传但格式简化" |
| 批量操作部分失败 | 成功的继续，失败的收集到结果列表 | Agent 汇总报告 |

### 5.2 测试策略

测试分层：

```
tests/
├── unit/                    # 单元测试（纯逻辑，无外部依赖）
│   ├── test_wiki_connector.py
│   ├── test_dual_storage.py
│   ├── test_query_engine.py
│   ├── test_okf_feishu_convert.py
│   └── test_conflict_resolution.py
├── integration/             # 集成测试（真实文件系统，mock 飞书 API）
│   ├── test_sync_flow.py
│   ├── test_query_flow.py
│   └── test_incr_sync_flow.py
└── conftest.py
```

关键测试用例：

| 模块 | 测试用例 | 说明 |
|------|---------|------|
| `wiki_connector` | `test_list_raw_docs_incremental` | 增量列表正确过滤已处理文档 |
| `wiki_connector` | `test_create_doc_returns_node_token` | 创建文档返回有效 token |
| `wiki_connector` | `test_429_retry` | 限流时正确重试 |
| `wiki_connector` | `test_okf_to_feishu_heading` | OKF 标题正确转为飞书 heading block |
| `wiki_connector` | `test_okf_to_feishu_table` | OKF 表格正确转为飞书 table block |
| `wiki_connector` | `test_feishu_to_okf_metadata_strip` | 飞书→OKF 时正确跳过元数据区 |
| `dual_storage` | `test_sync_to_feishu_new_doc` | 新文档正确写入飞书并记录 sync_state |
| `dual_storage` | `test_sync_to_feishu_update` | 已有文档正确更新飞书内容 |
| `dual_storage` | `test_detect_feishu_edits` | 正确检测飞书侧编辑 |
| `dual_storage` | `test_pull_from_feishu_no_conflict` | 无冲突时正确拉取覆盖 |
| `dual_storage` | `test_conflict_feishu_wins` | 冲突时飞书优先，本地备份 |
| `dual_storage` | `test_sync_state_atomic_write` | sync_state 原子写入 |
| `query_engine` | `test_fts5_chinese_search` | 中文关键词正确命中 |
| `query_engine` | `test_search_with_filters` | 结构化过滤正确生效 |
| `query_engine` | `test_deep_read_context_limit` | 深度读取遵守上下文长度限制 |
| `query_engine` | `test_score_time_decay` | 时间衰减评分正确 |
| `query_engine` | `test_rebuild_index` | 全量重建索引正确 |
| `query_engine` | `test_incremental_index_update` | 增量索引更新正确 |

Mock 策略：
- `wiki_connector`：mock `subprocess.run`（lark-cli 调用），不依赖真实飞书
- `dual_storage`：mock `WikiConnector`，使用 `tmp_path` 做真实文件系统
- `query_engine`：使用 `tmp_path` 创建临时 bundle 和索引

### 5.3 迁移策略

现有用户从 folder 模式迁移到 wiki 模式：

**Step 1：配置迁移**
```bash
python scripts/migrate_to_wiki.py --space-id <wiki_space_id> \
  --raw-node <node_token> --agent-node <node_token>
```
脚本自动识别旧 `folder_token` 配置 → 转换为 `scan_sources` 格式 → 写入新字段 → 备份旧 config.json

**Step 2：首次全量同步**
```bash
python scripts/auto_sync.py full-resync
```
- 清空本地 bundle（备份到 `bundle.backup.{timestamp}/`）
- 重新拉取所有原始文档
- 执行分类、写入、上传到 Agent 维护区
- 重建索引

**Step 3：验证**
```bash
python scripts/onboarding.py --check
```

向后兼容保证：
- 旧 `folder_token` 配置继续可用（`auto_sync.py` 自动识别数据源类型）
- 旧 bundle 结构不变
- 旧 `query.py` 保留但标记 deprecated

### 5.4 新增依赖

```txt
# requirements.txt
# 现有：pyyaml, pytest
# 新增：无（SQLite FTS5 是 Python 内置 sqlite3 模块的一部分）
```

零新依赖。`onboarding.py` 增加 FTS5 可用性检查。

### 5.5 目录结构变化

```
lark-autocontext/
├── SKILL.md                  # 更新（工作流 A-D）
├── scripts/
│   ├── wiki_connector.py     # 新增
│   ├── dual_storage.py       # 新增
│   ├── query_engine.py       # 新增（替换 query.py）
│   ├── migrate_to_wiki.py    # 新增（迁移工具）
│   ├── cli.py                # 保留，标记 deprecated
│   ├── scanner.py            # 保留，小改（调用 wiki_connector）
│   ├── okf_writer.py         # 保留，新增 generate_index_pages()
│   ├── visualize.py          # 保留不变
│   ├── auto_sync.py          # 保留，适配 wiki_connector
│   ├── init_bundle.py        # 保留，小改（初始化 .index/ 目录）
│   ├── onboarding.py         # 保留，新增 FTS5 + sync_state 检查
│   └── setup.py              # 保留，新增 wiki 配置引导
├── bundle/
│   ├── .index/               # 新增
│   │   └── search.db         # SQLite FTS5 索引
│   ├── .sync_state.json      # 新增（双写同步状态）
│   └── .conflicts/           # 新增（冲突备份）
│       └── log.md
├── tests/
│   ├── unit/                 # 重组
│   └── integration/          # 重组
└── requirements.txt          # 不变
```

---

## 6. 参考 WeKnora 借鉴的设计点

| WeKnora 设计 | lark-autocontext 对应实现 | 借鉴价值 |
|-------------|--------------------------|---------|
| Connector 接口 + 懒加载层级 | `wiki_connector.list_wiki_subtree()` | 飞书 Wiki 树懒加载 |
| 渐进式 RAG（搜索→深读→反思） | `query_engine` 三阶段工作流 | 查询质量提升 |
| Mandatory Deep Read | `query_engine.deep_read()` | 避免仅依赖片段 |
| 自适应切分策略 | 首版不实现，后续可加 | 飞书文档标题层级适合 heading 切分 |
| Parent-Child 分块 | 首版不实现，后续可加 | 飞书文档层级结构天然适合 |
| 插件管道（洋葱模型） | 首版不实现，写入仍用脚本 | 架构简化优先 |
| Chat Pipeline 8 步合并 | `query_engine` 评分 + 过滤 | 精化检索结果 |
| 两层去重调度 | `auto_sync` 水位线机制（已有） | 增量同步幂等 |
| 飞书 Connector 实现 | `wiki_connector` 直接参考 | Wiki Space 遍历逻辑 |
