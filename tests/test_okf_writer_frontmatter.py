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
    long = "这是一段描述" + "测试" * 50
    result = validate_description(long)
    assert len(result) <= 100
    assert result.endswith("…")


def test_description_validation_accepts_normal():
    from okf_writer import validate_description
    desc = "本次会议讨论了 OKF 重构方向，确定采用 Pipeline 架构。"
    assert validate_description(desc) == desc
