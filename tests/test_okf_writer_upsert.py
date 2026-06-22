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
