# Auto-Sync + OKF v0.1 Full Conformance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade lark-autocontext from manual scanning to Agent-driven Auto-Sync closed loop, while bringing the Bundle into full OKF v0.1 conformance (cross-links, structured body, real summaries, people/concept entities) and shipping a self-built single-file HTML visualizer.

**Architecture:** A thin coordinator (`auto_sync.py`) drives the existing scanner/okf_writer/query trio; scanner gains `--list-changed` for incremental detection via Feishu `edited-since`; okf_writer gains graph-shaping (mentions field, `# Related` section), structured body templates (Summary/Key Points/Decisions/Action Items), and people/concepts auto-upsert with preserved human-edited regions; new `visualize.py` emits self-contained `viz.html` (Cytoscape.js + marked.js via CDN); SKILL.md adds Workflow D plus Agent cron setup guide.

**Tech Stack:** Python 3.13, `markdown` (PyPI) for server-side body rendering, Cytoscape.js + marked.js (CDN, runtime only), lark-cli (existing), Git.

---

## File Structure

**Create:**
- `scripts/auto_sync.py` — Coordinator (~250 lines)
- `scripts/visualize.py` — Single-file HTML generator (~400 lines Python + embedded ~500-line HTML template)
- `tests/__init__.py`
- `tests/conftest.py`
- `tests/test_scanner_cleaning.py`
- `tests/test_scanner_list_changed.py`
- `tests/test_okf_writer_crosslinks.py`
- `tests/test_okf_writer_body.py`
- `tests/test_okf_writer_frontmatter.py`
- `tests/test_okf_writer_upsert.py`
- `tests/test_auto_sync_state.py`
- `tests/test_visualize_graph.py`
- `tests/fixtures/sample_bundle/...`

**Modify:**
- `scripts/scanner.py` — `--list-changed --since` mode; `clean_feishu_content()`; real `edited_time`
- `scripts/cli.py` — `fetch_folder_files_since`, `fetch_wiki_changed_since`, `fetch_doc_metadata`
- `scripts/okf_writer.py` — `generate_mentions`, `generate_related_section`, structured body, `validate_description`, `upsert_person`, `upsert_concept`
- `scripts/init_bundle.py` — Write `okf_version: "0.1"` frontmatter to root `index.md`
- `scripts/onboarding.py` — Add `--quiet` flag
- `SKILL.md` — Add Workflow D, Agent Cron Setup section, upgraded Classification Guide
- `.gitignore` — Add `bundle/.state.json`, `bundle/.failed/`
- `README.md` — Document Auto-Sync workflow and visualizer

**Test command:** `python -m pytest tests/ -v`

---

## Task 1: Test Infrastructure Setup

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/fixtures/sample_bundle/` (sample files)

- [ ] **Step 1: Create tests/__init__.py**

Create empty `tests/__init__.py`.

- [ ] **Step 2: Create tests/conftest.py**

```python
"""Shared pytest fixtures."""
import pytest


@pytest.fixture
def tmp_bundle(tmp_path):
    """Create a temporary empty OKF bundle directory structure."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "projects").mkdir()
    (bundle / "people").mkdir()
    (bundle / "concepts").mkdir()
    (bundle / "index.md").write_text(
        '---\nokf_version: "0.1"\ntitle: Test Bundle\n---\n\n# Test Bundle\n',
        encoding='utf-8'
    )
    (bundle / "log.md").write_text(
        '# Change Log\n\n## 2026-06-21\n\n* **Initialization**: Bundle created\n',
        encoding='utf-8'
    )
    return bundle


@pytest.fixture
def sample_classified_json():
    """Standard classified_json output for testing okf_writer."""
    return {
        "project": "demo",
        "type": "Meeting Minutes",
        "category": "meetings",
        "title": "2026-06-01 测试会议",
        "description": "测试会议讨论 OKF 重构方案",
        "summary": "确定采用 Pipeline 架构。",
        "key_points": ["要点1", "要点2"],
        "decisions": [{"decision": "采用 OKF", "owner": "Alice", "deadline": "2026-07-01"}],
        "action_items": [{"task": "写实现", "owner": "张三", "due": "2026-06-30"}],
        "tags": ["测试", "OKF"],
        "people": ["Alice", "张三"],
        "concepts": ["OKF", "Pipeline 架构"],
        "filename": "2026-06-01-测试会议.md",
        "resource": "https://feishu.cn/docx/TESTTOKEN",
        "edited_time": "2026-06-01T14:30:00+08:00"
    }
```

- [ ] **Step 3: Install pytest and markdown**

Run: `pip install pytest markdown`

- [ ] **Step 4: Verify pytest collects**

Run: `python -m pytest tests/ -v --collect-only`
Expected: `collected 0 items`

- [ ] **Step 5: Commit**

```bash
git add tests/ docs/superpowers/plans/2026-06-21-auto-sync-okf-conformance.md
git commit -m "test: add pytest scaffolding and shared fixtures"
```

---

## Task 2: Feishu Content Cleaning (scanner.py)

**Files:**
- Modify: `scripts/scanner.py`
- Create: `tests/test_scanner_cleaning.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_scanner_cleaning.py`:

```python
"""Tests for Feishu private-tag cleaning in scanner.py."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from scanner import clean_feishu_content


def test_clean_callout_tag():
    raw = '<callout emoji="🚀">整体框架</callout>'
    assert clean_feishu_content(raw) == '整体框架'


def test_clean_title_tag():
    raw = '<title>2026-06-20 周会</title>\n正文内容'
    cleaned = clean_feishu_content(raw)
    assert cleaned.startswith('# 2026-06-20 周会')
    assert '正文内容' in cleaned


def test_clean_image_tag():
    raw = '<image src="abc.png" />之后的文字'
    cleaned = clean_feishu_content(raw)
    assert '<image' not in cleaned
    assert '之后的文字' in cleaned


def test_collapse_blank_lines():
    raw = 'line1\n\n\n\n\nline2'
    assert clean_feishu_content(raw) == 'line1\n\nline2'


def test_preserves_markdown():
    raw = '# Heading\n\n- item1\n- item2\n\n```code```'
    assert clean_feishu_content(raw) == raw
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_scanner_cleaning.py -v`
Expected: FAIL with `ImportError: cannot import name 'clean_feishu_content'`

- [ ] **Step 3: Implement clean_feishu_content**

In `scripts/scanner.py`, add `import re` if not present, then add:

```python
def clean_feishu_content(raw: str) -> str:
    """Clean Feishu private tags and normalize whitespace.

    - <title>X</title> → # X
    - <callout ...>X</callout> → X
    - <image .../> → removed entirely
    - <details ...>X</details> → X
    - 3+ blank lines → 1 blank line
    """
    if not raw:
        return raw
    raw = re.sub(r'<title>(.*?)</title>', r'# \1', raw, flags=re.DOTALL)
    raw = re.sub(r'<image[^>]*/>', '', raw)
    raw = re.sub(r'<image[^>]*>.*?</image>', '', raw, flags=re.DOTALL)
    raw = re.sub(r'<callout[^>]*>(.*?)</callout>', r'\1', raw, flags=re.DOTALL)
    raw = re.sub(r'<details[^>]*>(.*?)</details>', r'\1', raw, flags=re.DOTALL)
    raw = re.sub(r'\n{3,}', '\n\n', raw)
    return raw.strip()
```

- [ ] **Step 4: Run tests to verify**

Run: `python -m pytest tests/test_scanner_cleaning.py -v`
Expected: all 5 tests PASS

- [ ] **Step 5: Wire cleaning into scan_single_doc**

In `scripts/scanner.py`, find the existing `scan_single_doc()` function and locate the line where content is fetched. Add right after fetch:

```python
content = clean_feishu_content(content)
```

- [ ] **Step 6: Commit**

```bash
git add scripts/scanner.py tests/test_scanner_cleaning.py
git commit -m "feat(scanner): add clean_feishu_content for private-tag stripping"
```

---

## Task 3: Scanner --list-changed Mode + Real edited_time

**Files:**
- Modify: `scripts/scanner.py`, `scripts/cli.py`
- Create: `tests/test_scanner_list_changed.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_scanner_list_changed.py`:

```python
"""Tests for scanner --list-changed mode."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))


def test_list_changed_returns_dict_shape():
    from scanner import list_changed
    sources = [{"type": "folder", "token": "fldcnFAKE", "name": "Test Folder"}]
    result = list_changed(sources, since="2099-01-01T00:00:00+08:00")
    assert "changed" in result
    assert isinstance(result["changed"], list)
    assert "source_results" in result


def test_normalize_changed_entry_shape():
    from scanner import _normalize_changed_entry
    raw = {
        "token": "DOCABC",
        "url": "https://feishu.cn/docx/DOCABC",
        "name": "Test Doc",
        "edit_time": "2026-06-20T14:30:00+08:00"
    }
    entry = _normalize_changed_entry(raw, source_key="folder:fldcnFAKE")
    assert entry["doc_token"] == "DOCABC"
    assert entry["url"] == "https://feishu.cn/docx/DOCABC"
    assert entry["title"] == "Test Doc"
    assert entry["edited_time"] == "2026-06-20T14:30:00+08:00"
    assert entry["source"] == "folder:fldcnFAKE"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_scanner_list_changed.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Add cli.py helper methods**

In `scripts/cli.py`, after `fetch_folder_files`, add:

```python
def fetch_folder_files_since(self, folder_token, since):
    """Search files edited since `since` in the folder."""
    args = ["drive", "+search", "--folder-tokens", folder_token,
            "--edited-since", since, "--page-all"]
    output = self.run(args, as_json=False)
    try:
        data = json.loads(output)
        return data.get("data", {}).get("files", [])
    except Exception:
        return []


def fetch_wiki_changed_since(self, space_id, since):
    """Fetch wiki nodes whose underlying docs changed since `since`."""
    nodes = self.fetch_wiki_tree(space_id)
    changed = []
    for n in nodes:
        edit_time = n.get("obj_edit_time") or n.get("edit_time", "")
        if edit_time and edit_time >= since:
            changed.append({
                "token": n.get("obj_token", ""),
                "url": f"https://feishu.cn/wiki/{n.get('node_token', '')}",
                "name": n.get("title", ""),
                "edit_time": edit_time,
            })
    return changed


def fetch_doc_metadata(self, doc_token):
    """Fetch doc metadata (title, edited_time, etc.)."""
    output = self.run(["docs", "+fetch", "--doc", doc_token,
                       "--doc-format", "markdown"], as_json=False)
    try:
        data = json.loads(output)
        doc = data.get("data", {}).get("document", {})
        return {
            "title": doc.get("title", doc_token),
            "edited_time": doc.get("revision_id_iso") or doc.get("updated_time") or "",
        }
    except Exception:
        return {"title": doc_token, "edited_time": ""}
```

- [ ] **Step 4: Implement list_changed in scanner.py**

In `scripts/scanner.py`, add:

```python
def _normalize_changed_entry(raw, source_key):
    """Normalize a raw Feishu file/doc dict into the changed-entry shape."""
    doc_token = raw.get("token") or raw.get("obj_token") or raw.get("doc_token", "")
    url = raw.get("url") or f"https://feishu.cn/docx/{doc_token}"
    title = raw.get("name") or raw.get("title") or doc_token
    edited_time = raw.get("edit_time") or raw.get("edited_time") or raw.get("modified_time", "")
    return {
        "doc_token": doc_token,
        "url": url,
        "title": title,
        "edited_time": edited_time,
        "source": source_key,
    }


def list_changed(sources, since):
    """List documents that changed across all configured sources since `since`."""
    cli = LarkCLI()
    changed = []
    source_results = {}
    for src in sources:
        src_type = src.get("type")
        token = src.get("token", "")
        source_key = f"{src_type}:{token}"
        try:
            if src_type == "folder":
                files = cli.fetch_folder_files_since(token, since)
            elif src_type == "wiki":
                files = cli.fetch_wiki_changed_since(token, since)
            elif src_type == "bitable":
                files = [{"token": token, "name": src.get("name", token),
                          "edit_time": since}]
            else:
                files = []
            for f in files:
                changed.append(_normalize_changed_entry(f, source_key))
            source_results[source_key] = {"ok": True, "error": None}
        except Exception as e:
            source_results[source_key] = {"ok": False, "error": str(e)}
    return {"changed": changed, "source_results": source_results}
```

- [ ] **Step 5: Add --list-changed CLI flag**

In `scripts/scanner.py` `main()` (or `if __name__ == "__main__"` block), add argparse args:

```python
parser.add_argument('--list-changed', action='store_true',
                    help='List changed documents since --since')
parser.add_argument('--since', help='ISO 8601 timestamp for incremental scan')
```

And handle it:

```python
if args.list_changed:
    config_path = os.path.join(os.path.dirname(__file__), "scan_config.json")
    if not os.path.exists(config_path):
        print(json.dumps({"changed": [], "source_results": {},
                          "error": "scan_config.json missing"}, ensure_ascii=False))
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    sources = cfg.get("sources", [])
    result = list_changed(sources, since=args.since or "2000-01-01T00:00:00+08:00")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return
```

- [ ] **Step 6: Wire real edited_time into scan_single_doc**

In `scan_single_doc()` return dict, ensure `edited_time` uses `cli.fetch_doc_metadata(doc_token).get("edited_time")` as primary source, fallback to current behavior.

- [ ] **Step 7: Run tests to verify**

Run: `python -m pytest tests/test_scanner_list_changed.py -v`
Expected: both tests PASS

- [ ] **Step 8: Commit**

```bash
git add scripts/scanner.py scripts/cli.py tests/test_scanner_list_changed.py
git commit -m "feat(scanner): add --list-changed incremental mode and real edited_time"
```

---

## Task 4: OKF Writer Cross-Links (mentions + # Related)

**Files:**
- Modify: `scripts/okf_writer.py`
- Create: `tests/test_okf_writer_crosslinks.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_okf_writer_crosslinks.py`:

```python
"""Tests for cross-link generation in okf_writer."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))


def test_generate_mentions_includes_people_concepts_project():
    from okf_writer import generate_mentions
    classified = {
        "project": "demo",
        "people": ["Alice", "张三"],
        "concepts": ["OKF", "Pipeline 架构"],
    }
    mentions = generate_mentions(classified)
    assert "/people/Alice.md" in mentions
    assert "/people/张三.md" in mentions
    assert "/concepts/OKF.md" in mentions
    assert "/concepts/Pipeline 架构.md" in mentions
    assert "/projects/demo/index.md" in mentions


def test_generate_related_section_format():
    from okf_writer import generate_related_section
    classified = {
        "project": "demo",
        "people": ["Alice"],
        "concepts": ["OKF"],
    }
    section = generate_related_section(classified)
    assert "# Related" in section
    assert "[Alice](/people/Alice.md)" in section
    assert "[OKF](/concepts/OKF.md)" in section
    assert "[demo](/projects/demo/index.md)" in section


def test_generate_mentions_project_only():
    from okf_writer import generate_mentions
    mentions = generate_mentions({"project": "demo"})
    assert mentions == ["/projects/demo/index.md"]


def test_generate_related_skips_empty_groups():
    from okf_writer import generate_related_section
    section = generate_related_section({"project": "demo", "people": [], "concepts": []})
    assert "Project:" in section
    assert "People:" not in section
    assert "Concepts:" not in section
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_okf_writer_crosslinks.py -v`
Expected: all 4 tests FAIL with ImportError

- [ ] **Step 3: Implement generate_mentions and generate_related_section**

In `scripts/okf_writer.py`, add:

```python
def generate_mentions(classified):
    """Build the `mentions` frontmatter array from classified_json."""
    mentions = []
    for person in classified.get("people") or []:
        if person:
            mentions.append(f"/people/{person}.md")
    for concept in classified.get("concepts") or []:
        if concept:
            mentions.append(f"/concepts/{concept}.md")
    project = classified.get("project")
    if project:
        mentions.append(f"/projects/{project}/index.md")
    return mentions


def generate_related_section(classified):
    """Build the '# Related' markdown section using absolute paths."""
    lines = ["# Related", ""]
    people = [p for p in (classified.get("people") or []) if p]
    concepts = [c for c in (classified.get("concepts") or []) if c]
    project = classified.get("project")
    if people:
        links = ", ".join(f"[{p}](/people/{p}.md)" for p in people)
        lines.append(f"* People: {links}")
    if concepts:
        links = ", ".join(f"[{c}](/concepts/{c}.md)" for c in concepts)
        lines.append(f"* Concepts: {links}")
    if project:
        lines.append(f"* Project: [{project}](/projects/{project}/index.md)")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify**

Run: `python -m pytest tests/test_okf_writer_crosslinks.py -v`
Expected: all 4 PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/okf_writer.py tests/test_okf_writer_crosslinks.py
git commit -m "feat(okf_writer): generate mentions and # Related for cross-linking"
```

---

## Task 5: OKF Writer Structured Body Template

**Files:**
- Modify: `scripts/okf_writer.py`
- Create: `tests/test_okf_writer_body.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_okf_writer_body.py`:

```python
"""Tests for structured body template generation."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))


def test_body_includes_summary_and_keypoints():
    from okf_writer import generate_body
    classified = {
        "type": "Meeting Minutes", "project": "demo",
        "summary": "测试摘要。",
        "key_points": ["要点1", "要点2"],
        "resource": "https://feishu.cn/docx/X",
    }
    body = generate_body(classified, raw_content="原始内容")
    assert "# Summary\n测试摘要。" in body
    assert "# Key Points" in body
    assert "- 要点1" in body
    assert "- 要点2" in body


def test_body_includes_decisions_for_meeting():
    from okf_writer import generate_body
    classified = {
        "type": "Meeting Minutes", "project": "demo", "summary": "S",
        "decisions": [{"decision": "决策A", "owner": "Alice", "deadline": "2026-07-01"}],
        "resource": "https://feishu.cn/docx/X",
    }
    body = generate_body(classified, raw_content="X")
    assert "# Decisions" in body
    assert "决策A" in body
    assert "Alice" in body


def test_body_skips_decisions_for_reference():
    from okf_writer import generate_body
    classified = {
        "type": "Reference", "project": "demo", "summary": "S",
        "decisions": [{"decision": "决策A", "owner": "Alice", "deadline": "2026-07-01"}],
        "resource": "https://feishu.cn/docx/X",
    }
    body = generate_body(classified, raw_content="X")
    assert "# Decisions" not in body


def test_body_includes_source_and_citations():
    from okf_writer import generate_body
    classified = {
        "type": "Reference", "project": "demo", "summary": "S",
        "resource": "https://feishu.cn/docx/X",
    }
    body = generate_body(classified, raw_content="原始内容")
    assert "# Source Content" in body
    assert "原始内容" in body
    assert "# Citations" in body
    assert "https://feishu.cn/docx/X" in body


def test_body_includes_related_when_entities_present():
    from okf_writer import generate_body
    classified = {
        "type": "Reference", "project": "demo", "summary": "S",
        "people": ["Alice"], "resource": "https://feishu.cn/docx/X",
    }
    body = generate_body(classified, raw_content="X")
    assert "# Related" in body
    assert "[Alice](/people/Alice.md)" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_okf_writer_body.py -v`
Expected: FAIL because existing `generate_body` doesn't include new sections

- [ ] **Step 3: Rewrite generate_body**

In `scripts/okf_writer.py`, replace the existing `generate_body` function with:

```python
TYPES_WITH_DECISIONS = {"Meeting Minutes", "Review Report"}
TYPES_WITH_ACTION_ITEMS = {"Meeting Minutes", "Requirement Doc"}


def generate_body(classified, raw_content):
    """Build the OKF-structured markdown body."""
    sections = []

    summary = (classified.get("summary") or "").strip()
    if summary:
        sections.append(f"# Summary\n{summary}")

    key_points = classified.get("key_points") or []
    if key_points:
        kp = ["# Key Points"] + [f"- {p}" for p in key_points if p]
        sections.append("\n".join(kp))

    doc_type = classified.get("type", "")
    decisions = classified.get("decisions") or []
    if decisions and doc_type in TYPES_WITH_DECISIONS:
        dec = ["# Decisions"]
        for d in decisions:
            dec.append(
                f"- **决策**: {d.get('decision', '')} "
                f"**负责人**: {d.get('owner', '')} "
                f"**截止**: {d.get('deadline', '')}"
            )
        sections.append("\n".join(dec))

    action_items = classified.get("action_items") or []
    if action_items and doc_type in TYPES_WITH_ACTION_ITEMS:
        ai = ["# Action Items"]
        for a in action_items:
            owner = f" — @{a.get('owner', '')}" if a.get('owner') else ""
            due = f" — {a.get('due', '')}" if a.get('due') else ""
            ai.append(f"- [ ] {a.get('task', '')}{owner}{due}")
        sections.append("\n".join(ai))

    if raw_content and raw_content.strip():
        sections.append(f"# Source Content\n{raw_content.strip()}")

    has_entities = (
        bool(classified.get("people") or [])
        or bool(classified.get("concepts") or [])
        or bool(classified.get("project"))
    )
    if has_entities:
        sections.append(generate_related_section(classified))

    citations = ["# Citations"]
    resource = classified.get("resource", "")
    if resource:
        citations.append(f"[1] [飞书原文]({resource})")
    sections.append("\n".join(citations))

    return "\n\n".join(sections) + "\n"
```

- [ ] **Step 4: Run tests to verify**

Run: `python -m pytest tests/test_okf_writer_body.py -v`
Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/okf_writer.py tests/test_okf_writer_body.py
git commit -m "feat(okf_writer): structured body with Summary/Key Points/Decisions/Action Items"
```

---

## Task 6: OKF Writer Frontmatter Upgrade (mentions, edited_time, desc validation)

**Files:**
- Modify: `scripts/okf_writer.py`
- Create: `tests/test_okf_writer_frontmatter.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_okf_writer_frontmatter.py`:

```python
"""Tests for upgraded frontmatter."""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))


def test_frontmatter_includes_mentions():
    from okf_writer import generate_frontmatter
    classified = {
        "project": "demo", "type": "Meeting Minutes", "title": "T",
        "description": "测试会议讨论核心议题", "tags": [],
        "people": ["Alice"], "concepts": ["OKF"],
        "resource": "https://x", "edited_time": "2026-06-20T14:30:00+08:00",
    }
    fm = generate_frontmatter(classified)
    assert "mentions:" in fm
    assert "/people/Alice.md" in fm
    assert "/concepts/OKF.md" in fm
    assert "/projects/demo/index.md" in fm


def test_frontmatter_uses_edited_time_as_timestamp():
    from okf_writer import generate_frontmatter
    classified = {
        "project": "demo", "type": "Reference", "title": "T",
        "description": "Test description with meaningful content",
        "tags": [], "resource": "https://x",
        "edited_time": "2026-06-20T14:30:00+08:00",
    }
    fm = generate_frontmatter(classified)
    assert "timestamp: 2026-06-20T14:30:00+08:00" in fm


def test_description_validation_rejects_mechanical():
    from okf_writer import validate_description
    with pytest.raises(ValueError):
        validate_description("Meeting Minutes - 某文档标题")


def test_description_validation_truncates_too_long():
    from okf_writer import validate_description
    long = "这是一段非常非常非常非常非常非常非常非常非常非常非常非常非常非常非常非常非常非常非常非常非常非常非常非常非常长的描述"
    result = validate_description(long)
    assert len(result) <= 100
    assert result.endswith("…")


def test_description_validation_accepts_normal():
    from okf_writer import validate_description
    desc = "本次会议讨论了 OKF 重构方向，确定采用 Pipeline 架构。"
    assert validate_description(desc) == desc
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_okf_writer_frontmatter.py -v`
Expected: FAIL

- [ ] **Step 3: Implement validate_description and upgrade generate_frontmatter**

In `scripts/okf_writer.py`, add:

```python
def validate_description(desc):
    """Validate description per OKF SHOULD: meaningful sentence, ≤100 chars."""
    import re as _re
    if not desc:
        raise ValueError("description is required and must be non-empty")
    desc = desc.strip()
    if _re.match(r"^[A-Za-z][A-Za-z ]+ - .+$", desc):
        raise ValueError(
            f"description appears to be mechanical '{{type}} - {{title}}' pattern: {desc!r}. "
            "Provide a meaningful one-sentence summary."
        )
    if len(desc) > 100:
        desc = desc[:97] + "…"
    return desc


def _now_iso():
    from datetime import datetime
    return datetime.now().astimezone().isoformat()
```

Then replace the existing `generate_frontmatter` with:

```python
def generate_frontmatter(classified):
    """Build YAML frontmatter from classified_json."""
    desc = validate_description(classified.get("description", ""))
    tags = classified.get("tags") or []
    people = classified.get("people") or []
    concepts = classified.get("concepts") or []
    timestamp = (
        classified.get("edited_time")
        or classified.get("timestamp")
        or _now_iso()
    )
    mentions = generate_mentions(classified)

    title = classified.get("title", "").replace('"', "'")
    lines = [
        "---",
        f"type: {classified.get('type', 'Other')}",
        f'title: "{title}"',
        f"description: {desc}",
    ]
    if classified.get("resource"):
        lines.append(f"resource: {classified['resource']}")
    if tags:
        lines.append(f"tags: [{', '.join(tags)}]")
    lines.append(f"timestamp: {timestamp}")
    if classified.get("project"):
        lines.append(f"project: {classified['project']}")
    if people:
        lines.append(f"people: [{', '.join(people)}]")
    if concepts:
        lines.append(f"concepts: [{', '.join(concepts)}]")
    if mentions:
        lines.append("mentions:")
        for m in mentions:
            lines.append(f"  - {m}")
    lines.append("---")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify**

Run: `python -m pytest tests/test_okf_writer_frontmatter.py -v`
Expected: all 5 PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/okf_writer.py tests/test_okf_writer_frontmatter.py
git commit -m "feat(okf_writer): frontmatter mentions, real edited_time, desc validation"
```

---

## Task 7: OKF Writer People/Concept Upsert with Preserved Regions

**Files:**
- Modify: `scripts/okf_writer.py`
- Create: `tests/test_okf_writer_upsert.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_okf_writer_upsert.py`:

```python
"""Tests for people/concept upsert with preserved user-edited regions."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))


def test_upsert_creates_new_person_file(tmp_bundle):
    from okf_writer import upsert_person
    upsert_person(
        str(tmp_bundle), name="Alice",
        mentioned_concept_id="projects/demo/meetings/2026-06-01-test",
        mentioned_title="2026-06-01 Test",
        mentioned_description="测试摘要",
        project="demo",
        timestamp="2026-06-01T14:30:00+08:00",
    )
    p = tmp_bundle / "people" / "Alice.md"
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    assert "type: Person" in text
    assert "title: Alice" in text
    assert "# Profile" in text
    assert "# Mentioned In" in text
    assert "[2026-06-01 Test]" in text


def test_upsert_preserves_profile_region(tmp_bundle):
    from okf_writer import upsert_person
    upsert_person(
        str(tmp_bundle), name="Alice",
        mentioned_concept_id="projects/demo/meetings/A",
        mentioned_title="A", mentioned_description="A desc",
        project="demo", timestamp="2026-06-01T00:00:00+08:00",
    )
    p = tmp_bundle / "people" / "Alice.md"
    text = p.read_text(encoding="utf-8")
    text = text.replace("# Profile\n", "# Profile\nAlice是 lark-autocontext 的核心维护者。\n")
    p.write_text(text, encoding="utf-8")

    upsert_person(
        str(tmp_bundle), name="Alice",
        mentioned_concept_id="projects/demo/meetings/B",
        mentioned_title="B", mentioned_description="B desc",
        project="demo", timestamp="2026-06-02T00:00:00+08:00",
    )
    text = p.read_text(encoding="utf-8")
    assert "Alice是 lark-autocontext 的核心维护者。" in text
    assert "[B]" in text
    assert "[A]" in text


def test_upsert_idempotent_for_same_mention(tmp_bundle):
    from okf_writer import upsert_person
    for _ in range(3):
        upsert_person(
            str(tmp_bundle), name="Alice",
            mentioned_concept_id="projects/demo/meetings/A",
            mentioned_title="A", mentioned_description="A desc",
            project="demo", timestamp="2026-06-01T00:00:00+08:00",
        )
    p = tmp_bundle / "people" / "Alice.md"
    text = p.read_text(encoding="utf-8")
    assert text.count("[A]") == 1


def test_upsert_concept_same_logic(tmp_bundle):
    from okf_writer import upsert_concept
    upsert_concept(
        str(tmp_bundle), name="OKF",
        mentioned_concept_id="projects/demo/meetings/A",
        mentioned_title="A", mentioned_description="A desc",
        project="demo", timestamp="2026-06-01T00:00:00+08:00",
    )
    c = tmp_bundle / "concepts" / "OKF.md"
    assert c.exists()
    text = c.read_text(encoding="utf-8")
    assert "type: Concept" in text
    assert "# Definition" in text
    assert "[A]" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_okf_writer_upsert.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Implement upsert helpers**

In `scripts/okf_writer.py`, add:

```python
def _sanitize_filename(name):
    """Strip filesystem-unsafe chars from entity name."""
    import re as _re
    return _re.sub(r'[<>:"/\\|?*]', '', name).strip()


def _parse_existing_mentions(text):
    """Return raw lines under # Mentioned In until next H1."""
    lines = text.splitlines()
    in_section = False
    out = []
    for line in lines:
        if line.startswith("# Mentioned In"):
            in_section = True
            continue
        if in_section:
            if line.startswith("# "):
                break
            if line.startswith("* ") or line.startswith("- "):
                out.append(line)
    return out


def _extract_section(text, heading):
    """Extract content under a specific H1 heading (exclusive of next H1)."""
    lines = text.splitlines()
    capture = False
    captured = []
    for line in lines:
        if line.strip() == heading:
            capture = True
            continue
        if capture and line.startswith("# "):
            break
        if capture:
            captured.append(line)
    return "\n".join(captured).strip()


def _upsert_entity(bundle_path, entity_type, name, mentioned_concept_id,
                   mentioned_title, mentioned_description, project, timestamp):
    """Shared upsert logic for Person and Concept entities."""
    import os as _os
    if entity_type == "Person":
        subdir = "people"
        profile_heading = "# Profile"
        desc_default = "在 lark-autocontext 知识库中出现的人物档案"
    else:
        subdir = "concepts"
        profile_heading = "# Definition"
        desc_default = "业务概念档案"

    safe_name = _sanitize_filename(name)
    if not safe_name:
        return None

    entity_dir = _os.path.join(bundle_path, subdir)
    _os.makedirs(entity_dir, exist_ok=True)
    entity_path = _os.path.join(entity_dir, f"{safe_name}.md")

    # Read existing
    profile_content = ""
    existing_mentions = []
    existing_tags = set()
    existing_timestamp = ""
    if _os.path.exists(entity_path):
        with open(entity_path, "r", encoding="utf-8") as f:
            existing_text = f.read()
        profile_content = _extract_section(existing_text, profile_heading)
        existing_mentions = _parse_existing_mentions(existing_text)
        import re as _re
        tag_match = _re.search(r'tags:\s*\[(.*?)\]', existing_text)
        if tag_match:
            existing_tags = {t.strip() for t in tag_match.group(1).split(",") if t.strip()}
        ts_match = _re.search(r'timestamp:\s*(\S+)', existing_text)
        if ts_match:
            existing_timestamp = ts_match.group(1)

    if project:
        existing_tags.add(project)
    if timestamp > existing_timestamp:
        new_timestamp = timestamp
    else:
        new_timestamp = existing_timestamp or timestamp

    # New mention line
    new_mention_line = (
        f"* [{mentioned_title}](/{mentioned_concept_id}.md) - {mentioned_description}"
    )

    # Dedupe by concept_id link
    link_marker = f"](/{mentioned_concept_id}.md)"
    deduped = [m for m in existing_mentions if link_marker not in m]
    deduped.insert(0, new_mention_line)

    # Build frontmatter
    tags_str = ", ".join(sorted(existing_tags))
    fm_lines = [
        "---",
        f"type: {entity_type}",
        f"title: {name}",
        f"description: {desc_default}",
    ]
    if tags_str:
        fm_lines.append(f"tags: [{tags_str}]")
    fm_lines.append(f"timestamp: {new_timestamp}")
    fm_lines.append("---")

    body_parts = [
        "\n".join(fm_lines),
        "",
        profile_heading,
        "",
        profile_content if profile_content else "<!-- 占位区，供后续人工补充，脚本永不覆盖 -->",
        "",
        "# Mentioned In",
        "",
        "\n".join(deduped),
        "",
    ]
    with open(entity_path, "w", encoding="utf-8") as f:
        f.write("\n".join(body_parts))
    return entity_path


def upsert_person(bundle_path, name, mentioned_concept_id, mentioned_title,
                  mentioned_description, project, timestamp):
    return _upsert_entity(bundle_path, "Person", name, mentioned_concept_id,
                          mentioned_title, mentioned_description, project, timestamp)


def upsert_concept(bundle_path, name, mentioned_concept_id, mentioned_title,
                   mentioned_description, project, timestamp):
    return _upsert_entity(bundle_path, "Concept", name, mentioned_concept_id,
                          mentioned_title, mentioned_description, project, timestamp)
```

- [ ] **Step 4: Run tests to verify**

Run: `python -m pytest tests/test_okf_writer_upsert.py -v`
Expected: all 4 PASS

- [ ] **Step 5: Wire upsert into write_okf_document**

Find existing `write_okf_document` in `scripts/okf_writer.py`. Right after writing the main concept file (but before updating index/log), add:

```python
# Auto-upsert entities
concept_id_for_link = os.path.relpath(file_path, bundle_path).replace(os.sep, "/").replace(".md", "")
for person in classified.get("people") or []:
    upsert_person(bundle_path, person, concept_id_for_link,
                  classified.get("title", ""), classified.get("description", ""),
                  classified.get("project", ""),
                  classified.get("edited_time") or classified.get("timestamp", ""))
for concept_name in classified.get("concepts") or []:
    upsert_concept(bundle_path, concept_name, concept_id_for_link,
                   classified.get("title", ""), classified.get("description", ""),
                   classified.get("project", ""),
                   classified.get("edited_time") or classified.get("timestamp", ""))
```

- [ ] **Step 6: Commit**

```bash
git add scripts/okf_writer.py tests/test_okf_writer_upsert.py
git commit -m "feat(okf_writer): auto-upsert people/concepts with preserved profile region"
```

---

## Task 8: Init Bundle okf_version Frontmatter + index.md description aggregation

**Files:**
- Modify: `scripts/init_bundle.py`, `scripts/okf_writer.py`
- Create: `tests/test_init_bundle.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_init_bundle.py`:

```python
"""Tests for init_bundle okf_version frontmatter and index aggregation."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))


def test_init_bundle_root_index_has_okf_version(tmp_path):
    from init_bundle import init_bundle
    bundle = tmp_path / "newbundle"
    init_bundle(str(bundle))
    root_index = (bundle / "index.md").read_text(encoding="utf-8")
    assert 'okf_version: "0.1"' in root_index
    assert root_index.startswith("---")


def test_index_aggregation_includes_descriptions(tmp_path):
    from okf_writer import generate_directory_index
    proj_dir = tmp_path / "projects" / "demo" / "meetings"
    proj_dir.mkdir(parents=True)
    (proj_dir / "2026-06-01-test.md").write_text(
        '---\ntitle: 2026-06-01 Test\ndescription: 测试会议讨论OKF\n---\n\nbody',
        encoding="utf-8"
    )
    index_text = generate_directory_index(str(proj_dir), heading="meetings")
    assert "[2026-06-01 Test]" in index_text
    assert "测试会议讨论OKF" in index_text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_init_bundle.py -v`
Expected: FAIL

- [ ] **Step 3: Upgrade init_bundle.py**

In `scripts/init_bundle.py`, find where root `index.md` is written. Replace the write call so the content begins with frontmatter:

```python
ROOT_INDEX_CONTENT = '''---
okf_version: "0.1"
title: Lark AutoContext OKF Bundle
description: 飞书业务上下文的 OKF 标准知识库
---

# Lark AutoContext OKF Bundle

## Projects
<!-- 自动填充 -->

## People
<!-- 自动填充 -->

## Concepts
<!-- 自动填充 -->
'''
```

And ensure `init_bundle()` writes that content to `index.md`.

- [ ] **Step 4: Implement generate_directory_index in okf_writer.py**

In `scripts/okf_writer.py`, add:

```python
def generate_directory_index(dir_path, heading=None):
    """Generate an OKF-conformant index.md for a directory by reading
    each child .md file's frontmatter description."""
    import os as _os
    import re as _re

    if heading is None:
        heading = _os.path.basename(dir_path) or "Items"

    entries = []
    for name in sorted(_os.listdir(dir_path)):
        if not name.endswith(".md") or name in ("index.md", "log.md"):
            continue
        path = _os.path.join(dir_path, name)
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        title_match = _re.search(r'^title:\s*"?([^"\n]+)"?\s*$', text, _re.MULTILINE)
        desc_match = _re.search(r'^description:\s*(.+)$', text, _re.MULTILINE)
        title = title_match.group(1).strip() if title_match else name.replace(".md", "")
        desc = desc_match.group(1).strip() if desc_match else ""
        entries.append(f"* [{title}]({name}) - {desc}")

    lines = [f"# {heading}", ""] + entries + [""]
    return "\n".join(lines)
```

- [ ] **Step 5: Run tests to verify**

Run: `python -m pytest tests/test_init_bundle.py -v`
Expected: both tests PASS

- [ ] **Step 6: Wire generate_directory_index into okf_writer write path**

Locate `update_index_md` in `scripts/okf_writer.py`. Replace its content with a call to `generate_directory_index` for the directory containing the just-written concept file.

- [ ] **Step 7: Commit**

```bash
git add scripts/init_bundle.py scripts/okf_writer.py tests/test_init_bundle.py
git commit -m "feat(okf): root index okf_version frontmatter + directory index with descriptions"
```

---

## Task 9: auto_sync.py State Management

**Files:**
- Create: `scripts/auto_sync.py`
- Modify: `.gitignore`
- Create: `tests/test_auto_sync_state.py`

- [ ] **Step 1: Update .gitignore**

Append to `.gitignore`:

```
bundle/.state.json
bundle/.failed/
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_auto_sync_state.py`:

```python
"""Tests for auto_sync state.json management."""
import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))


def test_load_state_returns_default_when_missing(tmp_path):
    from auto_sync import load_state
    state = load_state(str(tmp_path / "missing.json"))
    assert state["last_scan_at"] == ""
    assert state["sources"] == {}


def test_save_state_writes_atomically(tmp_path):
    from auto_sync import save_state
    state_path = tmp_path / "state.json"
    save_state(str(state_path), {
        "last_scan_at": "2026-06-21T08:00:00+08:00",
        "sources": {"folder:X": {"last_scan_at": "2026-06-21T08:00:00+08:00",
                                  "last_success": True, "last_error": None,
                                  "consecutive_failures": 0}},
        "stats": {}
    })
    assert state_path.exists()
    loaded = json.loads(state_path.read_text(encoding="utf-8"))
    assert loaded["last_scan_at"] == "2026-06-21T08:00:00+08:00"


def test_update_source_state_on_success(tmp_path):
    from auto_sync import update_source_state
    state = {"sources": {}}
    update_source_state(state, "folder:X", success=True,
                        scan_at="2026-06-21T08:00:00+08:00")
    assert state["sources"]["folder:X"]["last_success"] is True
    assert state["sources"]["folder:X"]["consecutive_failures"] == 0


def test_update_source_state_increments_failures(tmp_path):
    from auto_sync import update_source_state
    state = {"sources": {"folder:X": {"consecutive_failures": 1,
                                       "last_success": False,
                                       "last_scan_at": "2026-06-20T08:00:00+08:00",
                                       "last_error": "old"}}}
    update_source_state(state, "folder:X", success=False,
                        scan_at="2026-06-21T08:00:00+08:00", error="new error")
    assert state["sources"]["folder:X"]["consecutive_failures"] == 2
    assert state["sources"]["folder:X"]["last_error"] == "new error"
    # last_scan_at should NOT advance on failure
    assert state["sources"]["folder:X"]["last_scan_at"] == "2026-06-20T08:00:00+08:00"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_auto_sync_state.py -v`
Expected: FAIL with ImportError

- [ ] **Step 4: Create scripts/auto_sync.py (state functions only)**

Create `scripts/auto_sync.py`:

```python
"""Auto-Sync coordinator: state management + workflow steps."""
import os
import sys
import json
from datetime import datetime


if sys.platform == "win32" and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


def load_state(state_path):
    """Load .state.json, returning a default skeleton when missing."""
    if not os.path.exists(state_path):
        return {"last_scan_at": "", "sources": {}, "stats": {}}
    with open(state_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state_path, state):
    """Atomically write state.json (write to .tmp, rename)."""
    os.makedirs(os.path.dirname(state_path) or ".", exist_ok=True)
    tmp = state_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, state_path)


def update_source_state(state, source_key, success, scan_at, error=None):
    """Update a single source's state entry."""
    src = state.setdefault("sources", {}).setdefault(source_key, {
        "last_scan_at": "",
        "last_success": True,
        "last_error": None,
        "consecutive_failures": 0,
    })
    if success:
        src["last_scan_at"] = scan_at
        src["last_success"] = True
        src["last_error"] = None
        src["consecutive_failures"] = 0
    else:
        src["last_success"] = False
        src["last_error"] = error or "unknown"
        src["consecutive_failures"] = src.get("consecutive_failures", 0) + 1
```

- [ ] **Step 5: Run tests to verify**

Run: `python -m pytest tests/test_auto_sync_state.py -v`
Expected: all 4 PASS

- [ ] **Step 6: Commit**

```bash
git add scripts/auto_sync.py tests/test_auto_sync_state.py .gitignore
git commit -m "feat(auto_sync): state management with per-source watermarks"
```

---

## Task 10: auto_sync.py 主协调器（list-only + finalize 模式）

**Goal:** 把 Step 9 的 state 管理 + 已有的 scanner / cli / okf_writer 串成一条命令：
- `python scripts/auto_sync.py --list-only` → 只产出 `.auto_sync/pending_changes.json`，供 Agent 走分类→写入流程
- `python scripts/auto_sync.py --finalize` → Agent 完成分类、写入后调用，更新 watermark + git commit

> 设计依据：spec §2.3 (auto_sync.py 4 步契约) 与 §6 (Agent Cron 接入)。
> auto_sync.py 本身不调用 LLM；分类那步交给 Agent（SKILL Workflow D），保持薄壳。

- [ ] **Step 1: 写测试 `tests/test_auto_sync_flow.py`**

覆盖 4 个最小行为：

1. `--list-only` 首次运行：读取 `config.json` 的 `sources`，对每个 source 调用 `scanner.list_changed(since=None)`（mock 之），把合并后的 changes 写到 `.auto_sync/pending_changes.json`，并把 `state.json` 里每个 source 的 `last_scan_at` 暂存到 pending 里（**不**直接写入 state.json，等 finalize）。
2. `--list-only` 增量运行：每个 source 用上一轮 `state.json` 里的 `last_scan_at` 作为 `since`。
3. `--list-only` 无变化：pending_changes.json 中 `changes: []`，退出码 0。
4. `--finalize`：读取 `.auto_sync/pending_changes.json` 中的 `scan_at`，调用 `update_source_state(success=True)`，写回 state.json，删除 pending 文件。

测试用 monkeypatch 替换 `scanner.list_changed` 与 git 调用。

- [ ] **Step 2: 实现 `cmd_list_only(args)` 与 `cmd_finalize(args)`**

`scripts/auto_sync.py` 追加：

```python
import subprocess
from datetime import datetime, timezone
from scripts import scanner

PENDING_PATH = ".auto_sync/pending_changes.json"
STATE_PATH = ".auto_sync/state.json"

def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def cmd_list_only(args):
    state = load_state(STATE_PATH)
    config = json.load(open(args.config, "r", encoding="utf-8"))
    scan_at = _now_iso()
    all_changes = []
    source_scans = {}
    for src in config.get("sources", []):
        key = src["key"]
        since = state.get("sources", {}).get(key, {}).get("last_scan_at") or None
        changes = scanner.list_changed(src, since=since)
        all_changes.extend(changes)
        source_scans[key] = scan_at
    os.makedirs(os.path.dirname(PENDING_PATH), exist_ok=True)
    with open(PENDING_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "scan_at": scan_at,
            "source_scans": source_scans,
            "changes": all_changes,
        }, f, ensure_ascii=False, indent=2)
    print(f"[auto_sync] {len(all_changes)} change(s) listed -> {PENDING_PATH}")
    return 0

def cmd_finalize(args):
    if not os.path.exists(PENDING_PATH):
        print("[auto_sync] no pending file; nothing to finalize")
        return 0
    pending = json.load(open(PENDING_PATH, "r", encoding="utf-8"))
    state = load_state(STATE_PATH)
    for key, scan_at in pending.get("source_scans", {}).items():
        update_source_state(state, key, success=True, scan_at=scan_at)
    state["last_scan_at"] = pending.get("scan_at", _now_iso())
    save_state(STATE_PATH, state)
    os.remove(PENDING_PATH)
    if args.commit:
        subprocess.run(["git", "add", "bundle/", STATE_PATH], check=False)
        subprocess.run(
            ["git", "commit", "-m", f"chore(auto_sync): sync @ {state['last_scan_at']}"],
            check=False,
        )
    print("[auto_sync] finalized")
    return 0
```

- [ ] **Step 3: argparse 入口**

```python
def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="mode", required=True)
    sub.add_parser("list-only").set_defaults(func=cmd_list_only)
    fin = sub.add_parser("finalize")
    fin.add_argument("--commit", action="store_true")
    fin.set_defaults(func=cmd_finalize)
    p.add_argument("--config", default="config.json")
    args = p.parse_args()
    sys.exit(args.func(args))
```

- [ ] **Step 4: 跑测试**

`python -m pytest tests/test_auto_sync_flow.py -v` → 4 PASS

- [ ] **Step 5: 手动 smoke**

```bash
python scripts/auto_sync.py list-only --config config.json
cat .auto_sync/pending_changes.json
python scripts/auto_sync.py finalize --commit
```

- [ ] **Step 6: Commit**

```bash
git add scripts/auto_sync.py tests/test_auto_sync_flow.py
git commit -m "feat(auto_sync): list-only + finalize coordinator"
```

---

## Task 11: visualize.py — 自研 OKF 可视化器

**Goal:** `python scripts/visualize.py --bundle bundle/ --out viz.html`
扫描 bundle，生成 **单文件 HTML**：左侧 Cytoscape 力导向图，右侧 marked.js 渲染的节点详情。无构建步骤，CDN 加载。

> 设计依据：spec §4 (Visualizer)。约束：单文件 / 离线可看（CDN 即可）/ 节点类型按 frontmatter `type` 着色 / 链接来自 `mentions:` + `# Related` 节 + 反向引用 / 支持搜索过滤。

- [ ] **Step 1: 写测试 `tests/test_visualize.py`**

针对纯函数部分（不验证 HTML 渲染本身），覆盖：

1. `extract_links(md_text)` → 抽取 markdown 中所有 `[text](path.md)` 中的相对路径与绝对路径（bundle 内）。
2. `scan_bundle_to_graph(bundle_dir)` → 返回 `{"nodes": [...], "edges": [...]}`：
   - nodes：每个 `.md` 文件一个；属性 `id`(相对 bundle 路径)、`label`(frontmatter `title` 或 H1)、`type`(frontmatter `type`)、`path`(绝对路径)。
   - edges：来自 frontmatter `mentions:` 列表 + body 中 markdown 链接（去重）。
3. `compute_cited_by(graph)` → 在每个 node 上加 `cited_by: [id...]`（边的反向）。
4. 三种类型混合的迷你 bundle（people / meeting / decision）应产生正确的边数。

- [ ] **Step 2: 实现 `scripts/visualize.py` 扫描部分**

```python
import argparse, json, os, re, sys
from pathlib import Path
import yaml

LINK_RE = re.compile(r'\[([^\]]+)\]\(([^)]+\.md)(?:#[^)]*)?\)')
FM_RE = re.compile(r'^---\s*\n(.*?)\n---\s*\n', re.DOTALL)

def _parse_frontmatter(text):
    m = FM_RE.match(text)
    if not m:
        return {}, text
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        fm = {}
    return fm, text[m.end():]

def extract_links(md_text):
    return [m.group(2) for m in LINK_RE.finditer(md_text)]

def _norm_id(bundle_dir, abs_path):
    return str(Path(abs_path).resolve().relative_to(Path(bundle_dir).resolve())).replace("\\", "/")

def _resolve_link(bundle_dir, src_id, link):
    p = (Path(bundle_dir) / src_id).parent / link
    try:
        return _norm_id(bundle_dir, p)
    except ValueError:
        return None

def scan_bundle_to_graph(bundle_dir):
    nodes, edges, seen_edges = [], [], set()
    for md_path in Path(bundle_dir).rglob("*.md"):
        raw = md_path.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(raw)
        nid = _norm_id(bundle_dir, md_path)
        title = fm.get("title") or md_path.stem
        ntype = fm.get("type") or "doc"
        nodes.append({"id": nid, "label": title, "type": ntype,
                      "path": str(md_path), "body": body})
        targets = set()
        for ref in fm.get("mentions", []) or []:
            targets.add(ref if not ref.endswith(".md") else ref)
        for link in extract_links(body):
            r = _resolve_link(bundle_dir, nid, link)
            if r:
                targets.add(r)
        for t in targets:
            key = (nid, t)
            if key in seen_edges:
                continue
            seen_edges.add(key)
            edges.append({"source": nid, "target": t})
    return {"nodes": nodes, "edges": edges}

def compute_cited_by(graph):
    rev = {}
    for e in graph["edges"]:
        rev.setdefault(e["target"], []).append(e["source"])
    for n in graph["nodes"]:
        n["cited_by"] = rev.get(n["id"], [])
    return graph
```

- [ ] **Step 3: HTML 模板与渲染**

```python
HTML_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><title>OKF Bundle Visualizer</title>
<script src="https://unpkg.com/cytoscape@3.28.1/dist/cytoscape.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
  body{margin:0;font-family:system-ui,sans-serif;display:flex;height:100vh}
  #cy{flex:2;border-right:1px solid #ddd}
  #side{flex:1;overflow:auto;padding:16px}
  #search{width:100%;padding:6px;margin-bottom:8px}
  .type-tag{display:inline-block;padding:2px 6px;border-radius:4px;
            background:#eef;font-size:12px;margin-left:6px}
</style></head>
<body>
<div id="cy"></div>
<div id="side">
  <input id="search" placeholder="搜索节点（label / id）...">
  <div id="detail"><em>点击节点查看详情</em></div>
</div>
<script>
const DATA = __DATA__;
const COLORS = {meeting:"#4a90e2", decision:"#e25c4a", "action-item":"#f5a623",
                requirement:"#7ed321", review:"#9013fe", person:"#50e3c2",
                concept:"#bd10e0", doc:"#9b9b9b"};
const elements = [
  ...DATA.nodes.map(n => ({data:{id:n.id, label:n.label, type:n.type}})),
  ...DATA.edges.map(e => ({data:{source:e.source, target:e.target}})),
];
const cy = cytoscape({
  container: document.getElementById("cy"),
  elements,
  layout: {name:"cose", animate:false, idealEdgeLength:120},
  style:[
    {selector:"node", style:{
      "background-color":(ele)=>COLORS[ele.data("type")]||"#9b9b9b",
      "label":"data(label)","font-size":10,"text-wrap":"wrap","text-max-width":80}},
    {selector:"edge", style:{
      "width":1,"line-color":"#bbb","target-arrow-color":"#bbb",
      "target-arrow-shape":"triangle","curve-style":"bezier"}},
    {selector:".faded", style:{"opacity":0.15}},
  ],
});
const byId = Object.fromEntries(DATA.nodes.map(n=>[n.id,n]));
cy.on("tap","node",(e)=>{
  const n = byId[e.target.id()];
  const cited = (n.cited_by||[]).map(c=>`- ${c}`).join("\\n") || "（无）";
  document.getElementById("detail").innerHTML =
    `<h2>${n.label} <span class="type-tag">${n.type}</span></h2>`+
    `<p><code>${n.id}</code></p>`+
    marked.parse(n.body || "")+
    `<hr><h3>被引用</h3><pre>${cited}</pre>`;
});
document.getElementById("search").addEventListener("input",(ev)=>{
  const q = ev.target.value.trim().toLowerCase();
  cy.nodes().removeClass("faded");
  if(!q) return;
  cy.nodes().forEach(n=>{
    const d = n.data();
    if(!(d.label.toLowerCase().includes(q) || d.id.toLowerCase().includes(q))){
      n.addClass("faded");
    }
  });
});
</script></body></html>"""

def render_html(graph):
    return HTML_TEMPLATE.replace("__DATA__", json.dumps(graph, ensure_ascii=False))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", default="bundle")
    ap.add_argument("--out", default="viz.html")
    args = ap.parse_args()
    g = compute_cited_by(scan_bundle_to_graph(args.bundle))
    Path(args.out).write_text(render_html(g), encoding="utf-8")
    print(f"[visualize] wrote {args.out} ({len(g['nodes'])} nodes, {len(g['edges'])} edges)")

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑测试**

`python -m pytest tests/test_visualize.py -v` → 4 PASS

- [ ] **Step 5: 手动 smoke**

```bash
python scripts/visualize.py --bundle bundle/ --out viz.html
# 用浏览器打开 viz.html，确认：
#  1) 力导向图正常布局
#  2) 点击节点右侧渲染 markdown
#  3) 搜索框过滤生效
#  4) 节点颜色按 type 区分
```

- [ ] **Step 6: Commit**

```bash
git add scripts/visualize.py tests/test_visualize.py
git commit -m "feat(visualize): self-contained OKF bundle visualizer"
```

---

## Task 12: SKILL.md 升级 — Workflow D + Agent Cron 接入专章

**Goal:** 升级 `SKILL.md`：
1. 新增 **Workflow D: 自动同步（auto-sync）**：读 pending_changes.json → 逐条分类（沿用 Workflow B 分类规则）→ okf_writer 写入 → 调 `auto_sync.py finalize --commit`。
2. 新增 **Agent Cron Setup 专章**：给出 TRAE Schedule / Cursor / Claude Code 三套接入示例。
3. 升级 **Classification Guide**：把 spec §3 新增的 people/concept 类型补进去；body 模板要求按 §3.2 的 7 节结构。

> 这是文档任务，不写测试，靠人工 review。

- [ ] **Step 1: 在 SKILL.md "Workflows" 节末尾追加 Workflow D**

```markdown
### Workflow D: 自动同步（auto-sync）

**触发**：Agent 定时任务调用 / 用户说"扫一遍飞书"。

1. `python scripts/auto_sync.py list-only --config config.json`
2. 读 `.auto_sync/pending_changes.json`，对每条 change：
   - 调 `cli.fetch_doc(token)` 拉正文（带 `clean_feishu_content`）
   - 按 Classification Guide 决定 `type` + 目标 bundle 路径
   - 调 `okf_writer.upsert_*` 写入；对 people / concept 走 `upsert_person` / `upsert_concept`
3. `python scripts/auto_sync.py finalize --commit`
4. 输出一行人话总结：「同步 N 篇 → bundle/...」

**幂等保证**：同一 `resource`（doc_token）重跑不会产生重复条目；人工编辑过的 `# Profile` / `# Definition` 区段不会被覆盖。
```

- [ ] **Step 2: 追加 Agent Cron Setup 专章**

```markdown
## Agent Cron Setup

本项目不内置守护进程，定时由 Agent 侧承担。

**TRAE Schedule**：使用 Schedule 工具，cron `0 9 * * *`，message：
> 在 lark-autocontext 项目下执行 Workflow D（自动同步飞书到 bundle）。完成后只输出一句"同步 N 篇"或"无变化"。

**Cursor Tasks**：在项目根 `.cursor/tasks.json` 里登记同样的命令序列。

**Claude Code cron**：通过用户自己的 crontab：
`0 9 * * * cd ~/projects/lark-autocontext && claude --workflow=auto-sync`
```

- [ ] **Step 3: 升级 Classification Guide**

在现有分类表中追加两行：

| type | 路径模式 | 触发条件 |
|---|---|---|
| `person` | `bundle/people/<slug>.md` | 文档作者 / 频繁被 `@` 的人 |
| `concept` | `bundle/concepts/<slug>.md` | 反复出现的术语 / 缩写 / 项目代号 |

在 body 模板说明里写明：所有 type 都按 7 节结构 `# Summary / # Key Points / # Decisions(可选) / # Action Items(可选) / # Source Content / # Related / # Citations`。

- [ ] **Step 4: 人工通读 SKILL.md**

确认：
- 三个 Workflow（A 单文档 / B 批量扫描 / C 跨项目问答）原有内容未损
- Workflow D 与其余衔接自然
- Cron 示例可直接复制粘贴
- Classification Guide 没有遗漏类型

- [ ] **Step 5: Commit**

```bash
git add SKILL.md
git commit -m "docs(skill): add Workflow D auto-sync + Agent cron setup"
```

---

## Task 13: onboarding.py `--quiet` 模式 + README 更新

**Goal:**
1. 给 `scripts/onboarding.py` 加 `--quiet` 开关：跳过所有交互，从 `config.json` 默认值起步（用于 Agent 自动化首跑）。
2. 更新 `README.md`：在"快速开始"后面追加 **Auto-Sync 工作流** + **可视化** 两节，并把项目定位段落明确写成"飞书 → OKF bundle 的自动化管道"。

- [ ] **Step 1: 写测试 `tests/test_onboarding_quiet.py`**

1 个测试足够：用 monkeypatch 把 `input` 替换成一个会 raise 的桩。调用 `onboarding.run(quiet=True, config={...})`，应**不触发 input**就完成、并产出预期文件（用 tmp_path）。

- [ ] **Step 2: 改造 `scripts/onboarding.py`**

把所有 `input(...)` 包装在 `_prompt(default)` 里：

```python
def _prompt(question, default, quiet=False):
    if quiet:
        return default
    ans = input(f"{question} [{default}]: ").strip()
    return ans or default

def run(quiet=False, config=None):
    cfg = config or {}
    workspace = _prompt("workspace name", cfg.get("workspace", "main"), quiet)
    ...
```

CLI 入口加 `--quiet` flag。

- [ ] **Step 3: 跑测试**

`python -m pytest tests/test_onboarding_quiet.py -v` → 1 PASS
也跑一遍既有 onboarding 测试，确认未回归。

- [ ] **Step 4: 更新 `README.md`**

在"快速开始"之后追加：

```markdown
## Auto-Sync 工作流

```bash
# 首次（让 Agent 跑）
python scripts/onboarding.py --quiet

# 之后每次扫描（或交给 Agent 定时任务）
python scripts/auto_sync.py list-only
# Agent 会按 SKILL.md Workflow D 分类并写入 bundle
python scripts/auto_sync.py finalize --commit
```

详见 [SKILL.md](./SKILL.md) Workflow D 与 Agent Cron Setup。

## 可视化

```bash
python scripts/visualize.py --bundle bundle/ --out viz.html
# 浏览器打开 viz.html
```
```

把项目顶部一句话定位改成：
> **lark-autocontext** ——把飞书（Lark）文档自动转成 OKF 标准的项目知识 bundle，供 AI Agent 长期上下文使用。

- [ ] **Step 5: Commit**

```bash
git add scripts/onboarding.py tests/test_onboarding_quiet.py README.md
git commit -m "feat(onboarding): --quiet mode; docs: README auto-sync + visualize sections"
```

---

## Task 14: 端到端集成测试 — 对齐 Acceptance Criteria

**Goal:** 不再单元测试，而是端到端验证 spec §10 的 8 条 Acceptance Criteria 全部满足。

- [ ] **Step 1: 准备"干净一份"环境**

```bash
git status   # 应 clean
rm -rf .auto_sync viz.html
```

- [ ] **Step 2: 跑首次 Auto-Sync（含真实飞书 source）**

```bash
python scripts/onboarding.py --quiet
python scripts/auto_sync.py list-only --config config.json
# 走 Workflow D（人工或 Agent 都行；这一步关键是验证 SKILL 引导可执行）
python scripts/auto_sync.py finalize --commit
```

观察 / 校验：
- **AC1（Auto 闭环）**：一条命令链完成；commit 出现在 git log。
- **AC2（OKF v0.1 合规）**：抽 3 篇新写入的文档，frontmatter 含 `type / okf_version / mentions / edited_time`；body 有 7 节结构。
- **AC3（增量）**：再跑一次 list-only，pending_changes.json 中 `changes: []`。
- **AC4（幂等）**：把一份 people 文档的 `# Profile` 人工改一行，再跑一次 sync；这段不应被覆盖。
- **AC5（state）**：`.auto_sync/state.json` 中每个 source 的 `last_scan_at` 都已更新；`consecutive_failures: 0`。

- [ ] **Step 3: 跑 Visualizer**

```bash
python scripts/visualize.py --bundle bundle/ --out viz.html
```

校验：
- **AC6（可视化）**：浏览器打开后能看到图、点击节点显示详情、搜索框过滤生效；节点颜色按 type 区分。

- [ ] **Step 4: 故障回放**

把 `config.json` 里某个 source 的 token 改成无效值，跑一次 list-only：

- **AC7（失败隔离）**：那个 source 在 state.json 里 `last_success: false / consecutive_failures: 1`，其他 source 正常完成；整命令退出码 0（spec §5 决策）。

- [ ] **Step 5: SKILL.md 自检**

把 SKILL.md 给一个全新 Agent session（或自己冷读），按 Workflow D 操作。

- **AC8（SKILL 可执行）**：无需阅读源码就能跑通 list-only → 分类写入 → finalize。

- [ ] **Step 6: 全量回归**

```bash
python -m pytest -v
```

确认所有单元测试仍然 PASS。

- [ ] **Step 7: 最终 commit + push**

```bash
git add -A
git commit -m "test(e2e): auto-sync + okf conformance + visualize integration validated"
git push origin main
```

- [ ] **Step 8: 在 plan 末尾打勾 / 更新 spec 的"Acceptance"段**

如果有 AC 未达，回到对应 Task 修复后再跑 Step 2 起。

---

## 整体收尾

完成 Task 1–14 后：
- 14 次以上 commit，每次原子可回滚
- `bundle/` 内文档全部符合 OKF v0.1
- `scripts/auto_sync.py + scripts/visualize.py + SKILL.md Workflow D` 闭环了 spec 的"Auto + OKF 合规"双目标
- README & SKILL.md 让 Agent 与人都能零摩擦上手

下一步建议：把 `viz.html` 截图贴到 README，作为项目门面。