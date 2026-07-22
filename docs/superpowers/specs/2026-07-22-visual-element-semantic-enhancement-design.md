# 视觉元素语义增强设计

## 背景

当前管线对飞书文档中的视觉元素（图片、画板）处理存在严重缺陷：

1. **画板完全未处理**：`docs +fetch --doc-format markdown` 返回的画板仅为 `<whiteboard token="xxx">` 标签，语义信息为零。`clean_feishu_content()` 不会清理它（非 HTML `<image>` 标签），但也不会展开它，导致 OKF 中保留了一个无意义的标签。
2. **无 alt 图片无语义**：当 `docs +fetch` 返回的图片 Markdown 为 `![](url)`（空 alt text）时，管线没有任何补偿机制。subagent 分类时完全看不到图片内容。

### 飞书官方画板读取能力

`lark-cli whiteboard +query` 支持四种导出模式：

| 模式 | 命令参数 | 输出 | 内容 |
|------|---------|------|------|
| `raw` | `--output_as raw` | JSON | 节点结构数组：`text_shape`（含文字）、`image`（含 token）、`shape`（几何形状）等 |
| `svg` | `--output_as svg` | SVG 文件 | 矢量图，含文字和图片 URL |
| `image` | `--output_as image` | PNG 文件 | 位图预览（约 470KB） |
| `code` | `--output_as code` | JSON | 代码块节点（多数画板无此内容） |

实测"甄选"画板（`BbYfwbg8zhf1aib8gGicyKDRn1d`）的 `raw` 输出包含 19 个节点：4 个 `text_shape`（文字标签）+ 15 个 `image`（图片引用）。

## 目标

1. **A. 画板处理（管线代码层）**：在 `fetch_doc_content()` 中展开画板标签，提取文字节点摘要，图片密集时下载预览图。
2. **B. 无 alt 图片处理（SKILL 引导层）**：通过 `SKILL.md` 引导 subagent 利用自身识图能力补全图片描述，不嵌入 OCR/Vision API。

## 设计

### A. 画板处理管线

#### A.1 触发点

在 `WikiConnector.fetch_doc_content()` 中，`clean_feishu_content()` 之后新增 `_expand_whiteboards()` 调用。该方法检测 Markdown 中的 `<whiteboard token="xxx">` 和 `<whiteboard token="xxx"></whiteboard>` 标签，逐个展开。

#### A.2 处理流程

```
fetch_doc_content(node_token)
  ├─ docs +fetch --doc-format markdown       (现有)
  ├─ clean_feishu_content()                  (现有)
  └─ _expand_whiteboards(content, bundle_dir) (新增)
       ├─ 正则匹配所有 <whiteboard token="xxx"> 标签
       ├─ 对每个 token:
       │    ├─ whiteboard +query --output_as raw  → JSON 节点数组
       │    ├─ 提取 text_shape 节点 .text.text → 文字列表
       │    ├─ 统计 image 节点数量
       │    ├─ 计算图片占比 = image_count / total_count
       │    └─ if image 占比 > 50% AND bundle_dir is not None:
       │         ├─ whiteboard +query --output_as image --output <bundle_dir>/.assets/
       │         └─ 引用本地路径 .assets/whiteboard_<token>.png
       └─ 用结构化 Markdown 替换原始标签
```

#### A.3 接口设计

```python
def _expand_whiteboards(self, content: str, bundle_dir: str = None) -> str:
    """将 Markdown 中的 <whiteboard> 标签替换为结构化描述。

    对每个画板:
    1. whiteboard +query --output_as raw 获取节点结构
    2. 提取 text_shape 文字 + 统计 image 节点
    3. image 占比 > 50% 时下载 PNG 预览图到 bundle_dir/.assets/
    4. 替换为 Markdown 格式的画板摘要

    Args:
        content: 经 clean_feishu_content() 清理后的 Markdown
        bundle_dir: OKF bundle 目录路径，用于保存预览图。
                    为 None 时不下载图片，仅输出文字摘要。

    Returns:
        替换画板标签后的 Markdown
    """
```

`bundle_dir` 参数的设计考虑：
- folder 模式：scanner 传入 bundle 输出目录
- wiki 模式：`fetch_doc_content()` 的调用方传入 agent 维护区对应的临时目录
- 不传（None）：跳过图片下载，仅输出 raw 文字摘要——保持向后兼容

#### A.4 OKF 中的输出格式

画板标题来源：raw 返回的节点结构中没有标题字段。标题使用 `whiteboard_<token前8位>` 作为标识，不猜测业务含义（业务含义由 subagent 在分类阶段补充）。

**文字为主的画板（image ≤ 50%）**：
```markdown
**📊 画板：whiteboard_BbYfwbg8**
- 文字内容：需求评审 → 开发 → 测试 → 上线
- 节点统计：8个文字节点，2个图片节点
```

**图片为主的画板（image > 50%）**：
```markdown
**📊 画板：whiteboard_BbYfwbg8**
- 文字标签：APP用户规模、微信小程序用户规模、超猩整体用户评价
- 图片节点：15个（已生成预览图）
![画板预览图](.assets/whiteboard_BbYfwbg8zhf1aib8gGicyKDRn1d.png)
```

#### A.5 错误处理

画板 API 调用失败时（权限不足、token 无效、网络错误），降级为保留原始标签并添加注释：
```markdown
<!-- ⚠️ 画板读取失败：token=xxx，原因：permission denied -->
```

不抛异常、不阻塞流程——画板是辅助信息，不应导致整个文档读取失败。

#### A.6 `fetch_doc_content()` 签名变更

当前签名：
```python
def fetch_doc_content(self, node_token: str) -> str:
```

变更为：
```python
def fetch_doc_content(self, node_token: str, bundle_dir: str = None) -> str:
```

`bundle_dir` 默认为 None，保持向后兼容。调用方可选传入 bundle 目录路径用于保存画板预览图。

### B. 无 alt 图片处理（SKILL 引导层）

#### B.1 SKILL.md 变更

在 Workflow A2（单文档分类）和 B2（批量分类）的 subagent prompt 模板中，新增 **图片描述规则** 章节：

```markdown
## 图片描述规则

检查 content 中的图片引用 `![alt](url)`：

1. 如果 alt text 已有描述（非空、非纯文件名）→ 保留不动
2. 如果 alt text 为空 `![](url)` 或仅是文件名：
   a. 如果你具备视觉识图能力，尝试描述图片内容，填入 alt text
   b. 如果你无法识别图片，标注为 `![图片内容待补充](url)`
3. 对于画板预览图 `![画板预览图](.assets/xxx.png)`：
   a. 如果你具备视觉识图能力，描述画板中的流程、结构、数据
   b. 如果你无法识别，保留 `画板预览图` 作为 alt text
4. 描述要求：一句话概括图片核心信息（谁/什么/做什么），不超过50字
```

#### B.2 OKF 输出规范

`okf_writer.py` 的 `generate_body()` 中 `# Source Content` 部分原样保留 raw_content，不做图片语法修改。如果 subagent 在分类阶段补全了 alt text，补全结果体现在 OKF 的 summary / key_points 部分。

**设计决策：原始内容保持保真，AI 补充的描述体现在分类产出中。**

#### B.3 优雅降级

| Agent 类型 | 行为 |
|-----------|------|
| 有视觉能力（如 GPT-4V） | 自动补全 alt text |
| 无视觉能力但 URL 可访问 | 可选：尝试通过其他工具获取图片信息 |
| 无视觉能力且 URL 不可达 | 标注 `![图片内容待补充](url)`，不阻塞流程 |

#### B.4 与 A 的协作

两层递进：

```
管线层（A）：raw 文字摘要 + PNG 预览图下载
  ↓
Agent 层（B）：subagent 读 PNG，补全画板视觉描述
```

管线层提供基础语义（文字标签 + 预览图文件），Agent 层在此基础上进一步细化（识图描述）。两层可独立工作，互不依赖。

## 文件变更清单

| 文件 | 变更类型 | 职责 |
|------|---------|------|
| `scripts/wiki_connector.py` | 修改 | 新增 `_expand_whiteboards()` 方法；`fetch_doc_content()` 新增 `bundle_dir` 参数并调用 |
| `SKILL.md` | 修改 | Workflow A2/B2 新增图片描述规则 |
| `tests/unit/test_wiki_connector.py` | 修改 | 新增 `_expand_whiteboards()` 单元测试 |

### 不修改的文件

- `scripts/scanner.py` — `clean_feishu_content()` 逻辑不变
- `scripts/okf_writer.py` — `generate_body()` 原样保留 raw_content
- `scripts/dual_storage.py` — 同步逻辑不变
- `scripts/visualize.py` — 图谱可视化与图片语义无关
- `_preserve_image_alt_text()` — 已有功能，与本次升级正交

## 测试策略

| 测试 | 覆盖点 |
|------|--------|
| `test_expand_whiteboards_text_only` | 画板只有 text_shape 节点，输出文字摘要，不下载图片 |
| `test_expand_whiteboards_image_heavy` | image 节点 > 50%，触发 PNG 下载，输出含 `![](.assets/...)` |
| `test_expand_whiteboards_no_bundle_dir` | `bundle_dir=None`，仅输出文字摘要，不下载图片 |
| `test_expand_whiteboards_api_error` | API 调用失败，降级为注释标注 |
| `test_expand_whiteboards_no_whiteboard` | content 中无画板标签，原样返回 |
| `test_expand_whiteboards_multiple` | 多个画板标签，全部正确替换 |

所有测试 mock `lark-cli` subprocess 调用，不依赖真实飞书 API。
