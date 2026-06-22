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
