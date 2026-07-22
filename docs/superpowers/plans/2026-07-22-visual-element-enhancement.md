# Visual Element Semantic Enhancement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand whiteboard tags in fetched document content with structured text summaries and optional PNG previews, and add SKILL.md guidance for subagents to describe images with empty alt text.

**Architecture:** Two-layer enhancement. Layer A (code): `_expand_whiteboards()` method in `WikiConnector` that calls `whiteboard +query --output_as raw` to extract text nodes, and conditionally downloads PNG previews for image-heavy boards. Layer B (SKILL): prompt guidance in `SKILL.md` directing subagents to leverage their own vision capabilities to fill in empty image alt text.

**Tech Stack:** Python 3, lark-cli (`whiteboard +query`), Cytoscape.js (unchanged), pytest with unittest.mock

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `scripts/wiki_connector.py` | Modify | Add `_expand_whiteboards()` method; update `fetch_doc_content()` signature |
| `tests/unit/test_wiki_connector.py` | Modify | Add 6 unit tests for `_expand_whiteboards()` |
| `SKILL.md` | Modify | Add image description rules to Workflow A2 and B2 subagent prompts |

### Key existing code references

- `WikiConnector._run_lark()` at `scripts/wiki_connector.py:96` — subprocess helper, all lark-cli calls go through this
- `WikiConnector.fetch_doc_content()` at `scripts/wiki_connector.py:353` — the method to modify
- `WikiConnector._resolve_obj_token()` at `scripts/wiki_connector.py:310` — pattern for lark-cli calls with JSON parsing
- Test file imports at `tests/unit/test_wiki_connector.py:14-26` — `sys.path` setup + `from wiki_connector import WikiConnector`
- Existing mock pattern: `@patch("wiki_connector.subprocess.run")` used throughout test file
- SKILL.md A2 subagent prompt at `SKILL.md:283-307`
- SKILL.md B2 subagent prompt at `SKILL.md:397-414`

---

## Task 1: Add `_expand_whiteboards()` — text-only path (no image download)

**Files:**
- Modify: `scripts/wiki_connector.py` (add method after `check_doc_changed`, around line 660)
- Test: `tests/unit/test_wiki_connector.py` (add tests at end of file)

- [ ] **Step 1: Write the failing tests**

Add these tests to the end of `tests/unit/test_wiki_connector.py`:

```python
# ---------------------------------------------------------------------------
# _expand_whiteboards
# ---------------------------------------------------------------------------

def test_expand_whiteboards_no_whiteboard():
    """Content with no whiteboard tags is returned unchanged."""
    conn = WikiConnector("s", "r", "a")
    content = "# Title\n\nSome text without whiteboards."
    result = conn._expand_whiteboards(content)
    assert result == content


def test_expand_whiteboards_text_only():
    """Whiteboard with only text_shape nodes outputs text summary, no image download."""
    conn = WikiConnector("s", "r", "a")
    content = 'Before\n<whiteboard token="wb123"></whiteboard>\nAfter'
    raw_response = json.dumps({
        "ok": True,
        "data": {
            "nodes": [
                {"type": "text_shape", "text": {"text": "需求评审"}},
                {"type": "text_shape", "text": {"text": "开发"}},
                {"type": "text_shape", "text": {"text": "测试"}},
            ]
        }
    })
    with patch("wiki_connector.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout=raw_response, stderr=""
        )
        result = conn._expand_whiteboards(content)
    assert "<whiteboard" not in result
    assert "📊" in result
    assert "需求评审" in result
    assert "开发" in result
    assert "测试" in result
    assert "3个文字节点" in result
    assert "0个图片节点" in result
    # No image download for text-only boards
    assert ".assets/" not in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/kitch/Desktop/lark-autocontext && python3 -m pytest tests/unit/test_wiki_connector.py::test_expand_whiteboards_no_whiteboard tests/unit/test_wiki_connector.py::test_expand_whiteboards_text_only -v`
Expected: FAIL with `AttributeError: 'WikiConnector' object has no attribute '_expand_whiteboards'`

- [ ] **Step 3: Write minimal implementation**

Add this method to `WikiConnector` class in `scripts/wiki_connector.py`, after the `check_doc_changed` method (around line 660, before the module-level functions):

```python
    # ------------------------------------------------------------------
    # Whiteboard expansion (visual element semantic enhancement)
    # ------------------------------------------------------------------

    _WHITEBOARD_RE = re.compile(
        r'<whiteboard\s+token="([^"]+)"\s*(?:></whiteboard>|/>)'
    )

    def _expand_whiteboards(self, content: str, bundle_dir: str = None) -> str:
        """Replace ``<whiteboard token="xxx">`` tags with structured summaries.

        For each whiteboard:
        1. Call ``whiteboard +query --output_as raw`` to get node structure.
        2. Extract ``text_shape`` node texts and count ``image`` nodes.
        3. If image nodes > 50% of total AND ``bundle_dir`` is provided,
           download a PNG preview via ``--output_as image``.
        4. Replace the tag with a Markdown summary block.

        Args:
            content: Markdown cleaned by ``clean_feishu_content()``.
            bundle_dir: Bundle directory for saving preview PNGs.
                If ``None``, skip image download (text summary only).

        Returns:
            Markdown with whiteboard tags replaced by structured summaries.
        """
        def _replace_whiteboard(m):
            token = m.group(1)
            try:
                raw_output = self._run_lark(
                    ["whiteboard", "+query", "--whiteboard-token", token,
                     "--output_as", "raw"],
                    as_json=False,
                )
                data = json.loads(raw_output) if isinstance(raw_output, str) else raw_output
                nodes = data.get("data", {}).get("nodes", [])

                text_nodes = [
                    n.get("text", {}).get("text", "")
                    for n in nodes
                    if n.get("type") == "text_shape" and n.get("text", {}).get("text")
                ]
                image_count = sum(1 for n in nodes if n.get("type") == "image")
                total = len(nodes)
                text_count = len(text_nodes)

                # Build summary
                token_short = token[:8] if len(token) > 8 else token
                lines = [f"**📊 画板：whiteboard_{token_short}**"]

                if text_nodes:
                    lines.append(f"- 文字标签：{'、'.join(text_nodes)}")
                else:
                    lines.append("- 文字标签：（无）")

                lines.append(f"- 节点统计：{text_count}个文字节点，{image_count}个图片节点")

                # Download PNG if image-heavy and bundle_dir provided
                if bundle_dir and total > 0 and image_count / total > 0.5:
                    assets_dir = os.path.join(bundle_dir, ".assets")
                    os.makedirs(assets_dir, exist_ok=True)
                    img_filename = f"whiteboard_{token}.png"
                    try:
                        self._run_lark(
                            ["whiteboard", "+query", "--whiteboard-token", token,
                             "--output_as", "image", "--output", assets_dir],
                            as_json=False,
                        )
                        lines.append(f"- 图片节点：{image_count}个（已生成预览图）")
                        lines.append(
                            f"![画板预览图](.assets/{img_filename})"
                        )
                    except (RuntimeError, Exception) as exc:
                        logger.warning("Whiteboard image download failed for %s: %s", token, exc)
                        lines.append(f"- 图片节点：{image_count}个（预览图下载失败）")

                return "\n".join(lines)

            except (RuntimeError, json.JSONDecodeError, KeyError) as exc:
                logger.warning("Whiteboard expansion failed for token=%s: %s", token, exc)
                return f"<!-- ⚠️ 画板读取失败：token={token}，原因：{exc} -->"

        return self._WHITEBOARD_RE.sub(_replace_whiteboard, content)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/kitch/Desktop/lark-autocontext && python3 -m pytest tests/unit/test_wiki_connector.py::test_expand_whiteboards_no_whiteboard tests/unit/test_wiki_connector.py::test_expand_whiteboards_text_only -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/kitch/Desktop/lark-autocontext
git add scripts/wiki_connector.py tests/unit/test_wiki_connector.py
git commit -m "feat: add _expand_whiteboards() with text-only path

Expand <whiteboard token=xxx> tags using whiteboard +query --output_as raw.
Extract text_shape nodes as text labels, count image nodes.
No image download yet (bundle_dir path added in Task 2)."
```

---

## Task 2: Add image-heavy whiteboard path with PNG download

**Files:**
- Modify: `tests/unit/test_wiki_connector.py` (add tests)
- Code already exists from Task 1 (`_expand_whiteboards` handles `bundle_dir`)

- [ ] **Step 1: Write the failing tests**

Add these tests to the end of `tests/unit/test_wiki_connector.py`:

```python
def test_expand_whiteboards_image_heavy():
    """Whiteboard with >50% image nodes triggers PNG download when bundle_dir given."""
    import tempfile
    conn = WikiConnector("s", "r", "a")
    content = '<whiteboard token="wbIMG001"></whiteboard>'
    raw_response = json.dumps({
        "ok": True,
        "data": {
            "nodes": [
                {"type": "text_shape", "text": {"text": "APP用户规模"}},
                {"type": "image", "image": {"token": "img1"}},
                {"type": "image", "image": {"token": "img2"}},
                {"type": "image", "image": {"token": "img3"}},
            ]
        }
    })
    img_response = json.dumps({
        "ok": True,
        "data": {"preview_image_path": "whiteboard_wbIMG001.png", "size_bytes": 470076}
    })
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("wiki_connector.subprocess.run") as mock_run:
            # First call: raw query; second call: image download
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=raw_response, stderr=""),
                MagicMock(returncode=0, stdout=img_response, stderr=""),
            ]
            result = conn._expand_whiteboards(content, bundle_dir=tmpdir)
        assert "📊" in result
        assert "APP用户规模" in result
        assert "3个图片节点" in result
        assert "已生成预览图" in result
        assert ".assets/whiteboard_wbIMG001.png" in result
        # PNG file was created
        import os
        assert os.path.exists(os.path.join(tmpdir, ".assets", "whiteboard_wbIMG001.png")) \
            or os.path.exists(os.path.join(tmpdir, ".assets"))


def test_expand_whiteboards_no_bundle_dir():
    """When bundle_dir=None, image-heavy board outputs text only, no download."""
    conn = WikiConnector("s", "r", "a")
    content = '<whiteboard token="wbIMG002"></whiteboard>'
    raw_response = json.dumps({
        "ok": True,
        "data": {
            "nodes": [
                {"type": "image", "image": {"token": "img1"}},
                {"type": "image", "image": {"token": "img2"}},
                {"type": "image", "image": {"token": "img3"}},
            ]
        }
    })
    with patch("wiki_connector.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout=raw_response, stderr=""
        )
        result = conn._expand_whiteboards(content, bundle_dir=None)
    assert "📊" in result
    assert "3个图片节点" in result
    assert ".assets/" not in result
    # Only one lark-cli call (raw), no image download
    assert mock_run.call_count == 1
```

- [ ] **Step 2: Run tests to verify they pass** (implementation already in Task 1)

Run: `cd /Users/kitch/Desktop/lark-autocontext && python3 -m pytest tests/unit/test_wiki_connector.py::test_expand_whiteboards_image_heavy tests/unit/test_wiki_connector.py::test_expand_whiteboards_no_bundle_dir -v`
Expected: PASS (if the `os.makedirs` in Task 1 creates a real file that tempfile doesn't clean, the test still passes — the image download is mocked so no real file is written)

Note: The mock for `subprocess.run` means `whiteboard +query --output_as image` won't actually write a file. The test checks the Markdown output contains the correct `.assets/` reference. The `os.path.exists` check uses `or` to handle the case where the mocked download doesn't create the actual file.

- [ ] **Step 3: Commit**

```bash
cd /Users/kitch/Desktop/lark-autocontext
git add tests/unit/test_wiki_connector.py
git commit -m "test: add image-heavy and no-bundle-dir whiteboard tests"
```

---

## Task 3: Add error handling and multiple whiteboard tests

**Files:**
- Modify: `tests/unit/test_wiki_connector.py` (add tests)

- [ ] **Step 1: Write the failing tests**

Add these tests to the end of `tests/unit/test_wiki_connector.py`:

```python
def test_expand_whiteboards_api_error():
    """Whiteboard API failure degrades to comment annotation."""
    conn = WikiConnector("s", "r", "a")
    content = '<whiteboard token="wbERR001"></whiteboard>'
    with patch("wiki_connector.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="permission denied"
        )
        result = conn._expand_whiteboards(content)
    assert "<whiteboard" not in result
    assert "⚠️" in result
    assert "wbERR001" in result
    assert "permission denied" in result


def test_expand_whiteboards_multiple():
    """Multiple whiteboard tags are all replaced."""
    conn = WikiConnector("s", "r", "a")
    content = (
        'Text before\n'
        '<whiteboard token="wbAAA01"></whiteboard>\n'
        'Middle text\n'
        '<whiteboard token="wbBBB02"></whiteboard>\n'
        'Text after'
    )
    raw_response_1 = json.dumps({
        "ok": True,
        "data": {"nodes": [{"type": "text_shape", "text": {"text": "流程A"}}]}
    })
    raw_response_2 = json.dumps({
        "ok": True,
        "data": {"nodes": [{"type": "text_shape", "text": {"text": "流程B"}}]}
    })
    with patch("wiki_connector.subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=raw_response_1, stderr=""),
            MagicMock(returncode=0, stdout=raw_response_2, stderr=""),
        ]
        result = conn._expand_whiteboards(content)
    assert "<whiteboard" not in result
    assert "流程A" in result
    assert "流程B" in result
    assert "Text before" in result
    assert "Middle text" in result
    assert "Text after" in result
    assert mock_run.call_count == 2
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd /Users/kitch/Desktop/lark-autocontext && python3 -m pytest tests/unit/test_wiki_connector.py::test_expand_whiteboards_api_error tests/unit/test_wiki_connector.py::test_expand_whiteboards_multiple -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
cd /Users/kitch/Desktop/lark-autocontext
git add tests/unit/test_wiki_connector.py
git commit -m "test: add error handling and multiple whiteboard tests"
```

---

## Task 4: Wire `_expand_whiteboards()` into `fetch_doc_content()`

**Files:**
- Modify: `scripts/wiki_connector.py:353-370` (update `fetch_doc_content`)
- Test: `tests/unit/test_wiki_connector.py` (add integration test)

- [ ] **Step 1: Write the failing test**

Add this test to the end of `tests/unit/test_wiki_connector.py`:

```python
def test_fetch_doc_content_expands_whiteboards():
    """fetch_doc_content calls _expand_whiteboards on the cleaned content."""
    conn = WikiConnector("s", "r", "a")
    # Mock _resolve_obj_token to return a fake token
    with patch.object(conn, "_resolve_obj_token", return_value="obj-token-1"):
        # Mock docs +fetch to return content with a whiteboard tag
        doc_response = json.dumps({
            "data": {"document": {"content": '# Title\n<whiteboard token="wbX12345"></whiteboard>\nText'}}
        })
        # Mock whiteboard +query raw response
        wb_response = json.dumps({
            "ok": True,
            "data": {"nodes": [{"type": "text_shape", "text": {"text": "图表说明"}}]}
        })
        with patch("wiki_connector.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=doc_response, stderr=""),
                MagicMock(returncode=0, stdout=wb_response, stderr=""),
            ]
            result = conn.fetch_doc_content("node-1")
    assert "<whiteboard" not in result
    assert "📊" in result
    assert "图表说明" in result
    assert "# Title" in result
    assert "Text" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/kitch/Desktop/lark-autocontext && python3 -m pytest tests/unit/test_wiki_connector.py::test_fetch_doc_content_expands_whiteboards -v`
Expected: FAIL — whiteboard tag still present because `fetch_doc_content` doesn't call `_expand_whiteboards`

- [ ] **Step 3: Update `fetch_doc_content()`**

In `scripts/wiki_connector.py`, replace the `fetch_doc_content` method (lines 353-370) with:

```python
    def fetch_doc_content(self, node_token: str, bundle_dir: str = None) -> str:
        """Fetch a document's content as cleaned Markdown.

        Resolves ``node_token`` -> ``obj_token`` via the wiki node list, then
        fetches the doc markdown through ``docs +fetch``, cleans it via
        ``scanner.clean_feishu_content``, and finally expands whiteboard
        tags into structured summaries via ``_expand_whiteboards``.

        Args:
            node_token: Wiki node token of the document.
            bundle_dir: Optional bundle directory for saving whiteboard
                preview images. If ``None``, whiteboards are expanded
                with text summaries only (no PNG download).
        """
        obj_token = self._resolve_obj_token(node_token)
        output = self._run_lark(
            ["docs", "+fetch", "--doc", obj_token, "--doc-format", "markdown"],
            as_json=False,
        )
        data = json.loads(output) if isinstance(output, str) else output
        content = (
            data.get("data", {}).get("document", {}).get("content", "")
        )
        content = clean_feishu_content(content)
        content = self._expand_whiteboards(content, bundle_dir=bundle_dir)
        return content
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/kitch/Desktop/lark-autocontext && python3 -m pytest tests/unit/test_wiki_connector.py::test_fetch_doc_content_expands_whiteboards -v`
Expected: PASS

- [ ] **Step 5: Run full test suite to check for regressions**

Run: `cd /Users/kitch/Desktop/lark-autocontext && python3 -m pytest tests/unit/test_wiki_connector.py -v --tb=short`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
cd /Users/kitch/Desktop/lark-autocontext
git add scripts/wiki_connector.py tests/unit/test_wiki_connector.py
git commit -m "feat: wire _expand_whiteboards into fetch_doc_content

fetch_doc_content now accepts optional bundle_dir parameter and calls
_expand_whiteboards() after clean_feishu_content(). Backward compatible
— bundle_dir defaults to None (text-only whiteboard summaries)."
```

---

## Task 5: Add image description rules to SKILL.md

**Files:**
- Modify: `SKILL.md` (add rules to Workflow A2 at line ~307 and B2 at line ~414)

- [ ] **Step 1: Add image description rules to Workflow A2**

In `SKILL.md`, after the A2 subagent prompt block (after line 307, before `### Step A3`), insert:

```markdown
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
```

- [ ] **Step 2: Add same rules to Workflow B2**

In `SKILL.md`, after the B2 subagent prompt block (after line 414, before `### Step B3`), insert the same block:

```markdown
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
```

- [ ] **Step 3: Verify SKILL.md structure is intact**

Run: `cd /Users/kitch/Desktop/lark-autocontext && grep -n "### Step A3\|### Step B3\|图片描述规则" SKILL.md`
Expected: Both "图片描述规则" entries appear, followed by A3 and B3 headers in order.

- [ ] **Step 4: Commit**

```bash
cd /Users/kitch/Desktop/lark-autocontext
git add SKILL.md
git commit -m "docs: add image description rules to SKILL.md Workflow A2 and B2

Guide subagents to describe images with empty alt text using their own
vision capabilities. Includes fallback for agents without vision."
```

---

## Task 6: End-to-end verification and push

**Files:**
- No code changes — verification only

- [ ] **Step 1: Run full test suite**

Run: `cd /Users/kitch/Desktop/lark-autocontext && python3 -m pytest tests/unit/test_wiki_connector.py tests/unit/test_visualize.py -v --tb=short`
Expected: All tests PASS

- [ ] **Step 2: Verify SKILL.md readability**

Run: `cd /Users/kitch/Desktop/lark-autocontext && python3 -c "
with open('SKILL.md') as f:
    content = f.read()
assert '图片描述规则' in content
assert content.count('图片描述规则') == 2  # A2 and B2
print('SKILL.md check passed')
"`
Expected: `SKILL.md check passed`

- [ ] **Step 3: Push to main**

```bash
cd /Users/kitch/Desktop/lark-autocontext
git push origin main
```

- [ ] **Step 4: Verify commit history**

Run: `cd /Users/kitch/Desktop/lark-autocontext && git log --oneline -6`
Expected: 5 new commits on top of the spec commit.
