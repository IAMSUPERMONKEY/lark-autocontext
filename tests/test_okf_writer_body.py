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
        "decisions": [{"decision": "决策A", "owner": "刻奇", "deadline": "2026-07-01"}],
        "resource": "https://feishu.cn/docx/X",
    }
    body = generate_body(classified, raw_content="X")
    assert "# Decisions" in body
    assert "决策A" in body
    assert "刻奇" in body


def test_body_skips_decisions_for_reference():
    from okf_writer import generate_body
    classified = {
        "type": "Reference", "project": "demo", "summary": "S",
        "decisions": [{"decision": "决策A", "owner": "刻奇", "deadline": "2026-07-01"}],
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
        "people": ["刻奇"], "resource": "https://feishu.cn/docx/X",
    }
    body = generate_body(classified, raw_content="X")
    assert "# Related" in body
    assert "[刻奇](/people/刻奇.md)" in body
