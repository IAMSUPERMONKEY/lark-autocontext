"""Unit tests for OKF ↔ Feishu docx conversion (Task 4).

Covers the five module-level functions added to ``wiki_connector``:

- ``_parse_frontmatter`` -- split YAML frontmatter from the Markdown body.
- ``generate_metadata_header`` -- build the emoji metadata header (spec 3.1).
- ``strip_metadata_header`` -- remove the emoji header from Feishu content.
- ``okf_to_feishu_content`` -- full OKF → Feishu content conversion.
- ``feishu_to_okf_body`` -- Feishu content → OKF body (header stripped +
  ``scanner.clean_feishu_content`` applied).

These are pure string transformations; no lark-cli / subprocess interaction
is involved. ``clean_feishu_content`` is mocked in the round-trip test so the
assertions stay deterministic.
"""
import sys
import os
from unittest.mock import patch

# Make scripts/ importable when running from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))


# ---------------------------------------------------------------------------
# _parse_frontmatter
# ---------------------------------------------------------------------------

def test_parse_frontmatter_basic():
    """OKF content with frontmatter: dict parsed, body preserved."""
    from wiki_connector import _parse_frontmatter
    content = (
        "---\n"
        "type: Meeting Minutes\n"
        'title: "Test Doc"\n'
        "description: A meaningful description\n"
        "tags: [重构, OKF]\n"
        "timestamp: 2026-06-20T14:30:00+08:00\n"
        "---\n"
        "# Body Heading\n\nSome content."
    )
    fm, body = _parse_frontmatter(content)
    assert fm["type"] == "Meeting Minutes"
    assert fm["title"] == "Test Doc"
    assert fm["tags"] == ["重构", "OKF"]
    assert body == "# Body Heading\n\nSome content."


def test_parse_frontmatter_no_frontmatter():
    """Plain Markdown without ``---`` returns ({}, content)."""
    from wiki_connector import _parse_frontmatter
    content = "# Just a heading\n\nSome text without frontmatter."
    fm, body = _parse_frontmatter(content)
    assert fm == {}
    assert body == content


def test_parse_frontmatter_empty():
    """Content starting with ``---\\n---`` returns ({}, \"\")."""
    from wiki_connector import _parse_frontmatter
    content = "---\n---"
    fm, body = _parse_frontmatter(content)
    assert fm == {}
    assert body == ""


# ---------------------------------------------------------------------------
# generate_metadata_header
# ---------------------------------------------------------------------------

def test_generate_metadata_header_full():
    """Frontmatter with all fields produces all three emoji lines + ---."""
    from wiki_connector import generate_metadata_header
    fm = {
        "type": "Meeting Minutes",
        "project": "lark-autocontext",
        "tags": ["重构", "OKF"],
        "people": ["张三", "李四"],
        "timestamp": "2026-06-20T14:30:00+08:00",
        "resource": "https://feishu.cn/docx/abc123",
    }
    header = generate_metadata_header(fm)
    lines = header.split("\n")
    # Line 1: type | project | tags
    assert "📝 类型：Meeting Minutes" in lines[0]
    assert "项目：lark-autocontext" in lines[0]
    assert "标签：重构, OKF" in lines[0]
    # Line 2: people | date
    assert "👥 相关人员：张三, 李四" in lines[1]
    assert "📅 2026-06-20" in lines[1]
    # Line 3: resource
    assert "🔗" in lines[2]
    assert "https://feishu.cn/docx/abc123" in lines[2]
    # Ends with --- (no trailing newline)
    assert lines[-1] == "---"
    assert not header.endswith("\n")


def test_generate_metadata_header_minimal():
    """Frontmatter with only type yields just the 📝 line and ---."""
    from wiki_connector import generate_metadata_header
    fm = {"type": "Reference"}
    header = generate_metadata_header(fm)
    lines = header.split("\n")
    assert lines[0] == "📝 类型：Reference"
    assert lines[-1] == "---"
    # Only the type line + the closing ---
    assert len(lines) == 2


def test_generate_metadata_header_tags_list():
    """tags as a YAML list are joined with \", \"."""
    from wiki_connector import generate_metadata_header
    fm = {"type": "Other", "tags": ["alpha", "beta", "gamma"]}
    header = generate_metadata_header(fm)
    assert "标签：alpha, beta, gamma" in header


# ---------------------------------------------------------------------------
# strip_metadata_header
# ---------------------------------------------------------------------------

def test_strip_metadata_header():
    """Content with emoji header: header removed, body preserved."""
    from wiki_connector import strip_metadata_header
    content = (
        "📝 类型：Meeting Minutes\n"
        "👥 相关人员：张三 | 📅 2026-06-20\n"
        "---\n"
        "# Real Body\n\nSome content here."
    )
    result = strip_metadata_header(content)
    assert "📝" not in result
    assert result.startswith("# Real Body")
    assert "Some content here." in result


def test_strip_metadata_header_no_header():
    """Content without 📝 is returned unchanged."""
    from wiki_connector import strip_metadata_header
    content = "# Just a heading\n\nNo metadata header here."
    result = strip_metadata_header(content)
    assert result == content


# ---------------------------------------------------------------------------
# okf_to_feishu_content
# ---------------------------------------------------------------------------

def test_okf_to_feishu_content():
    """Full OKF content: frontmatter stripped, metadata header added, body kept."""
    from wiki_connector import okf_to_feishu_content
    okf = (
        "---\n"
        "type: Meeting Minutes\n"
        'title: "Test Doc"\n'
        "description: A meaningful description\n"
        "tags: [重构, OKF]\n"
        "timestamp: 2026-06-20T14:30:00+08:00\n"
        "project: demo\n"
        "---\n"
        "# Body Heading\n\nSome content."
    )
    result = okf_to_feishu_content(okf)
    # Frontmatter YAML block is gone; the result starts with the emoji header.
    assert result.startswith("📝")
    assert not result.startswith("---\ntype:")
    # Metadata header fields are present.
    assert "📝 类型：Meeting Minutes" in result
    assert "项目：demo" in result
    assert "标签：重构, OKF" in result
    assert "📅 2026-06-20" in result
    # Body is preserved verbatim.
    assert "# Body Heading" in result
    assert "Some content." in result


# ---------------------------------------------------------------------------
# feishu_to_okf_body
# ---------------------------------------------------------------------------

def test_feishu_to_okf_body():
    """Feishu content with metadata header: header stripped, content cleaned."""
    from wiki_connector import feishu_to_okf_body
    feishu = (
        "📝 类型：Meeting Minutes\n"
        "👥 相关人员：张三 | 📅 2026-06-20\n"
        "---\n"
        "# Body\n\nSome <p>html</p> content."
    )
    # Mock clean_feishu_content to return its input unchanged so we can
    # assert the header was stripped and the body survived the pipeline.
    with patch("wiki_connector.clean_feishu_content", side_effect=lambda x: x) as mock_clean:
        result = feishu_to_okf_body(feishu)
    assert mock_clean.called
    assert "📝" not in result
    assert "# Body" in result
    assert "Some <p>html</p> content." in result
