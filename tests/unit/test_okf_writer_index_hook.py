"""Tests for index hook in write_okf_document and generate_index_pages.

Task 13: After the OKF file is written to disk, write_okf_document makes a
best-effort call to QueryEngine.update_index so the FTS5 search index stays
in sync without a separate rebuild step. generate_index_pages scans the
bundle and writes a navigation index.md grouped by project, with People and
Concepts sections.

Tests:
  1. test_index_hook_called_after_write -- mock QueryEngine.update_index,
     call write_okf_document, assert update_index called with output path.
  2. test_index_hook_failure_doesnt_break_write -- mock update_index to
     raise RuntimeError; assert file still written, no exception propagated.
  3. test_generate_index_pages_basic -- 3 OKF files in different
     project/type combos; assert projects/index.md created with all titles
     and project section headers.
  4. test_generate_index_pages_people_concepts -- OKF files with people and
     concepts frontmatter; assert People and Concepts sections in index.
  5. test_generate_index_pages_skips_index_log -- index.md and log.md in
     bundle; assert they are not listed in the generated index.
"""
import sys
import os
from unittest.mock import patch

import pytest

# Make scripts/ importable when running from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))


# ---------------------------------------------------------------------------
# Helper: create an OKF .md file with frontmatter
# ---------------------------------------------------------------------------

def _make_okf_file(bundle_path, project, doc_type, title, filename,
                   people=None, concepts=None):
    """Create an OKF .md file under projects/{project}/{type_slug}/{filename}.

    Returns the absolute path to the created file.
    """
    type_slug = doc_type.lower().replace(" ", "-")
    project_dir = os.path.join(bundle_path, "projects", project, type_slug)
    os.makedirs(project_dir, exist_ok=True)
    fpath = os.path.join(project_dir, filename)
    lines = [
        "---",
        f"type: {doc_type}",
        f'title: "{title}"',
        f"description: {title} description",
        "timestamp: 2026-07-01T10:00:00+08:00",
        f"project: {project}",
    ]
    if people:
        lines.append(f"people: [{', '.join(people)}]")
    if concepts:
        lines.append(f"concepts: [{', '.join(concepts)}]")
    lines.append("---")
    lines.append("")
    lines.append("# Summary")
    lines.append("")
    lines.append(f"Content for {title}.")
    with open(fpath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return fpath


# ---------------------------------------------------------------------------
# 1. test_index_hook_called_after_write
# ---------------------------------------------------------------------------

def test_index_hook_called_after_write(tmp_bundle, sample_classified_json):
    """write_okf_document calls QueryEngine.update_index after writing the file.

    Mocks QueryEngine so no real SQLite index is created. Asserts update_index
    is called exactly once with the absolute path of the written OKF file.
    """
    from okf_writer import write_okf_document

    classified = dict(sample_classified_json)
    classified["_classified_by"] = "subagent"

    with patch("okf_writer.get_bundle_path", return_value=str(tmp_bundle)), \
         patch("query_engine.QueryEngine") as mock_QE:
        result = write_okf_document(classified, skip_visualize=True)

    assert "error" not in result, f"write failed: {result}"
    assert "absolute_path" in result
    output_path = result["absolute_path"]
    assert os.path.exists(output_path), "OKF file should exist on disk"

    # The index hook should have called update_index with the output path.
    mock_QE.return_value.update_index.assert_called_once_with(output_path)


# ---------------------------------------------------------------------------
# 2. test_index_hook_failure_doesnt_break_write
# ---------------------------------------------------------------------------

def test_index_hook_failure_doesnt_break_write(tmp_bundle, sample_classified_json):
    """Index hook failure (RuntimeError) does not break the main write flow.

    Mocks QueryEngine.update_index to raise RuntimeError. Asserts:
    - No exception propagates from write_okf_document.
    - The OKF file is still written to disk.
    - update_index was still invoked (the hook fired).
    """
    from okf_writer import write_okf_document

    classified = dict(sample_classified_json)
    classified["_classified_by"] = "subagent"

    with patch("okf_writer.get_bundle_path", return_value=str(tmp_bundle)), \
         patch("query_engine.QueryEngine") as mock_QE:
        mock_QE.return_value.update_index.side_effect = RuntimeError("boom")
        # Should NOT raise -- the hook wraps update_index in try/except.
        result = write_okf_document(classified, skip_visualize=True)

    assert "error" not in result, f"write failed: {result}"
    output_path = result["absolute_path"]
    assert os.path.exists(output_path), \
        "OKF file must exist even if index hook failed"
    # The hook was invoked (and raised), proving it fired.
    mock_QE.return_value.update_index.assert_called_once_with(output_path)


# ---------------------------------------------------------------------------
# 3. test_generate_index_pages_basic
# ---------------------------------------------------------------------------

def test_generate_index_pages_basic(tmp_path):
    """generate_index_pages creates projects/index.md with all docs grouped.

    Creates 3 OKF files across 2 projects and 3 types. Asserts:
    - projects/index.md file is created.
    - Content contains all 3 document titles.
    - Content has project section headers (## project-a, ## project-b).
    - Returns total_docs == 3.
    """
    from okf_writer import generate_index_pages

    _make_okf_file(str(tmp_path), "project-a", "Meeting Minutes",
                   "Meeting Doc A", "meeting-a.md")
    _make_okf_file(str(tmp_path), "project-b", "Requirement",
                   "Requirement Doc B", "req-b.md")
    _make_okf_file(str(tmp_path), "project-a", "Reference",
                   "Reference Doc C", "ref-c.md")

    result = generate_index_pages(bundle_path=str(tmp_path))

    assert "projects_index" in result
    projects_index = result["projects_index"]
    assert os.path.exists(projects_index), "projects/index.md should exist"

    with open(projects_index, "r", encoding="utf-8") as f:
        content = f.read()

    # All 3 titles appear.
    assert "Meeting Doc A" in content
    assert "Requirement Doc B" in content
    assert "Reference Doc C" in content

    # Project section headers.
    assert "## project-a" in content
    assert "## project-b" in content

    # Total docs count.
    assert result["total_docs"] == 3


# ---------------------------------------------------------------------------
# 4. test_generate_index_pages_people_concepts
# ---------------------------------------------------------------------------

def test_generate_index_pages_people_concepts(tmp_path):
    """generate_index_pages includes People and Concepts sections.

    Creates OKF files with people and concepts frontmatter. Asserts the
    generated index has ## People and ## Concepts sections listing them.
    """
    from okf_writer import generate_index_pages

    _make_okf_file(str(tmp_path), "demo", "Meeting Minutes",
                   "Meeting with People", "meeting.md",
                   people=["Alice", "Bob"], concepts=["OKF", "Pipeline"])

    result = generate_index_pages(bundle_path=str(tmp_path))

    with open(result["projects_index"], "r", encoding="utf-8") as f:
        content = f.read()

    assert "## People" in content
    assert "## Concepts" in content
    assert "Alice" in content
    assert "Bob" in content
    assert "OKF" in content
    assert "Pipeline" in content


# ---------------------------------------------------------------------------
# 5. test_generate_index_pages_skips_index_log
# ---------------------------------------------------------------------------

def test_generate_index_pages_skips_index_log(tmp_path):
    """generate_index_pages skips index.md and log.md files.

    Creates index.md and log.md with distinctive titles alongside a real
    OKF doc. Asserts the navigation index does NOT list index.md or log.md
    entries, and only includes the real doc.
    """
    from okf_writer import generate_index_pages

    # Real OKF doc.
    _make_okf_file(str(tmp_path), "demo", "Reference",
                   "Real Doc Title", "real-doc.md")

    # index.md and log.md with distinctive titles that should be skipped.
    index_md = tmp_path / "index.md"
    index_md.write_text(
        '---\ntype: Reference\ntitle: "Root Index Title"\n---\n\n# Index\n',
        encoding="utf-8")
    log_md = tmp_path / "log.md"
    log_md.write_text(
        '---\ntype: Reference\ntitle: "Root Log Title"\n---\n\n# Log\n',
        encoding="utf-8")

    result = generate_index_pages(bundle_path=str(tmp_path))

    with open(result["projects_index"], "r", encoding="utf-8") as f:
        content = f.read()

    # Real doc is listed.
    assert "Real Doc Title" in content
    # index.md and log.md titles are NOT listed.
    assert "Root Index Title" not in content
    assert "Root Log Title" not in content
    # Only 1 real doc was indexed.
    assert result["total_docs"] == 1
