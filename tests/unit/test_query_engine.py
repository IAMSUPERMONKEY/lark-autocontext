"""Unit tests for QueryEngine: FTS5 schema creation (Task 8) and index
build/update operations (Task 9).

Task 8 (schema-only slice):

- ``SearchFilters`` / ``DocMatch`` / ``SearchResult`` dataclasses exist and
  can be instantiated with the fields specified in the design spec.
- ``SearchFilters`` defaults every field to ``None``.
- ``QueryEngine.ensure_index`` creates the ``.index/`` directory and the
  ``search.db`` SQLite database file.
- ``ensure_index`` creates the ``documents`` table, the ``documents_fts``
  FTS5 virtual table, and the three sync triggers (``documents_ai``,
  ``documents_ad``, ``documents_au``).
- ``ensure_index`` is idempotent (safe to call twice).
- The FTS5 tokenizer is ``unicode61`` (verified via the
  ``documents_fts_config`` table).

Task 9 (index build & update):

- ``update_index`` inserts a single OKF document with parsed frontmatter.
- ``update_index`` skips re-indexing when ``content_hash`` is unchanged.
- ``update_index`` applies CJK character spacing to ``body_text`` so that
  FTS5 ``unicode61`` can match single Chinese characters.
- ``remove_from_index`` deletes a document from the index.
- ``rebuild_index`` walks the bundle, skipping ``index.md`` / ``log.md`` and
  hidden directories, and returns the count of indexed documents.
- ``_extract_body_text`` strips markdown formatting and applies CJK spacing.

All tests use the ``tmp_path`` pytest fixture and exercise real SQLite
operations -- nothing is mocked.
"""
import os
import sys
import sqlite3
from dataclasses import fields as dc_fields

import pytest

# Make scripts/ importable when running from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))

from query_engine import QueryEngine, SearchFilters, DocMatch, SearchResult  # noqa: E402


# ---------------------------------------------------------------------------
# Shared OKF sample (Task 9 tests)
# ---------------------------------------------------------------------------

SAMPLE_OKF = '''---
type: Meeting Minutes
title: "重构讨论"
description: 讨论OKF重构方向
tags: [重构, OKF]
timestamp: 2026-06-20T14:30:00+08:00
project: demo
people: [张三, 李四]
---

# Summary

本次会议讨论了重构方向，确定采用Pipeline架构。

## Key Points

- **模块化**: 拆分为三个核心模块
- `代码块`: 需要清洗
- [链接文档](https://example.com)
'''


# ---------------------------------------------------------------------------
# Dataclass existence & shape
# ---------------------------------------------------------------------------

def test_dataclasses_exist():
    """SearchFilters, DocMatch, SearchResult can be instantiated with their fields."""
    sf = SearchFilters(project="demo", doc_type="Meeting Minutes",
                       tags=["测试"], people="Alice",
                       date_from="2026-01-01", date_to="2026-12-31")
    assert sf.project == "demo"
    assert sf.doc_type == "Meeting Minutes"
    assert sf.tags == ["测试"]
    assert sf.people == "Alice"
    assert sf.date_from == "2026-01-01"
    assert sf.date_to == "2026-12-31"

    dm = DocMatch(local_path="/bundle/x.md", title="标题", doc_type="Note",
                  score=1.5, snippet="命中片段",
                  full_content="全文", related_docs=["/bundle/y.md"])
    assert dm.local_path == "/bundle/x.md"
    assert dm.title == "标题"
    assert dm.doc_type == "Note"
    assert dm.score == 1.5
    assert dm.snippet == "命中片段"
    assert dm.full_content == "全文"
    assert dm.related_docs == ["/bundle/y.md"]

    sr = SearchResult(matches=[dm], context="上下文", total_found=1)
    assert sr.matches == [dm]
    assert sr.context == "上下文"
    assert sr.total_found == 1


def test_search_filters_defaults():
    """All SearchFilters fields default to None."""
    sf = SearchFilters()
    assert sf.project is None
    assert sf.doc_type is None
    assert sf.tags is None
    assert sf.people is None
    assert sf.date_from is None
    assert sf.date_to is None


# ---------------------------------------------------------------------------
# QueryEngine.ensure_index -- directory & db file
# ---------------------------------------------------------------------------

def test_ensure_index_creates_dir_and_db(tmp_path):
    """ensure_index() creates .index/ directory and search.db file."""
    engine = QueryEngine(str(tmp_path))
    engine.ensure_index()

    index_dir = tmp_path / ".index"
    db_file = index_dir / "search.db"
    assert index_dir.is_dir(), ".index/ directory should exist"
    assert db_file.is_file(), ".index/search.db file should exist"


# ---------------------------------------------------------------------------
# QueryEngine.ensure_index -- schema (tables & triggers)
# ---------------------------------------------------------------------------

def test_ensure_index_creates_fts5_table(tmp_path):
    """ensure_index() creates documents table, documents_fts virtual table, and 3 triggers."""
    engine = QueryEngine(str(tmp_path))
    engine.ensure_index()

    conn = sqlite3.connect(engine.db_path)
    try:
        # Gather all schema objects of interest.
        rows = conn.execute(
            "SELECT type, name FROM sqlite_master "
            "WHERE name IN ('documents', 'documents_fts', "
            "'documents_ai', 'documents_ad', 'documents_au')"
        ).fetchall()
    finally:
        conn.close()

    names_by_type = {name: obj_type for obj_type, name in rows}

    # documents main table.
    assert names_by_type.get("documents") == "table", \
        "documents table must exist"
    # documents_fts virtual table.
    assert names_by_type.get("documents_fts") == "table", \
        "documents_fts virtual table must exist"
    # Three sync triggers.
    assert names_by_type.get("documents_ai") == "trigger", \
        "documents_ai trigger must exist"
    assert names_by_type.get("documents_ad") == "trigger", \
        "documents_ad trigger must exist"
    assert names_by_type.get("documents_au") == "trigger", \
        "documents_au trigger must exist"


# ---------------------------------------------------------------------------
# QueryEngine.ensure_index -- idempotency
# ---------------------------------------------------------------------------

def test_ensure_index_idempotent(tmp_path):
    """ensure_index() can be called twice without error (CREATE IF NOT EXISTS)."""
    engine = QueryEngine(str(tmp_path))
    engine.ensure_index()
    # Second call should be a no-op and not raise.
    engine.ensure_index()

    db_file = tmp_path / ".index" / "search.db"
    assert db_file.is_file(), "search.db should still exist after second call"


# ---------------------------------------------------------------------------
# QueryEngine.ensure_index -- FTS5 tokenizer configuration
# ---------------------------------------------------------------------------

def test_fts5_tokenizer_is_unicode61(tmp_path):
    """The documents_fts table uses the unicode61 tokenizer.

    Verified two ways:

    1. The ``tokenize='unicode61'`` clause is present in the table's CREATE
       VIRTUAL TABLE SQL (pulled from ``sqlite_master``). This directly
       asserts the configured tokenizer.

    2. A functional round-trip proves the FTS5 index + sync triggers work
       end-to-end: insert documents into ``documents`` (the AFTER INSERT
       trigger syncs ``documents_fts``) and issue ``MATCH`` queries that
       return matches. Both an English keyword and a full Chinese phrase
       are exercised.

    Note: unicode61 tokenizes unbroken runs of CJK characters as a single
    token, so single-character Chinese MATCH queries do not hit -- the
    full phrase must be used. Per-character CJK splitting is a concern for
    the indexing layer (Tasks 9-10), not the schema (Task 8).
    """
    engine = QueryEngine(str(tmp_path))
    engine.ensure_index()

    conn = sqlite3.connect(engine.db_path)
    try:
        # 1) Inspect the CREATE VIRTUAL TABLE SQL for the tokenize clause.
        sql_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='documents_fts'"
        ).fetchone()
        assert sql_row is not None, "documents_fts should exist in sqlite_master"
        create_sql = sql_row[0].lower()
        assert "tokenize" in create_sql, \
            "documents_fts SQL should declare a tokenize clause"
        assert "unicode61" in create_sql, \
            f"documents_fts should use unicode61, got: {sql_row[0]!r}"

        # 2) Functional check: insert docs and search them via FTS5 MATCH.
        #    The AFTER INSERT trigger syncs documents_fts automatically.
        conn.execute(
            "INSERT INTO documents (local_path, title, body_text) "
            "VALUES (?, ?, ?)",
            ("/bundle/en.md", "Meeting Notes",
             "Project alpha testing for the search engine"),
        )
        conn.execute(
            "INSERT INTO documents (local_path, title, body_text) "
            "VALUES (?, ?, ?)",
            ("/bundle/cn.md", "测试文档标题",
             "这是一段中文正文内容用于验证分词"),
        )
        conn.commit()

        # English keyword search -> returns the English doc.
        en_matches = conn.execute(
            "SELECT documents.local_path FROM documents_fts "
            "JOIN documents ON documents.rowid = documents_fts.rowid "
            "WHERE documents_fts MATCH ?",
            ("search",),
        ).fetchall()
        assert en_matches, \
            "unicode61 should match the English keyword 'search'"
        assert en_matches[0][0] == "/bundle/en.md"

        # Full Chinese phrase search -> returns the Chinese doc (unicode61
        # tokenizes the unbroken CJK run as a single token).
        cn_matches = conn.execute(
            "SELECT documents.local_path FROM documents_fts "
            "JOIN documents ON documents.rowid = documents_fts.rowid "
            "WHERE documents_fts MATCH ?",
            ("这是一段中文正文内容用于验证分词",),
        ).fetchall()
        assert cn_matches, \
            "unicode61 should match the full Chinese phrase token"
        assert cn_matches[0][0] == "/bundle/cn.md"
    finally:
        conn.close()


# ===========================================================================
# Task 9: index build and update operations
# ===========================================================================


def test_update_index_single_doc(tmp_path):
    """update_index() parses an OKF file and inserts its frontmatter + body.

    Creates a real OKF file in ``tmp_path``, indexes it, then queries the
    SQLite database directly to verify every column was populated from the
    parsed frontmatter.
    """
    engine = QueryEngine(str(tmp_path))
    okf_path = tmp_path / "meeting.md"
    okf_path.write_text(SAMPLE_OKF, encoding="utf-8")

    engine.update_index(str(okf_path))

    conn = sqlite3.connect(engine.db_path)
    try:
        row = conn.execute(
            "SELECT local_path, title, description, doc_type, project, "
            "tags, people, content_hash FROM documents "
            "WHERE local_path = ?",
            ("meeting.md",),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "document should have been inserted"
    local_path, title, description, doc_type, project, tags, people, content_hash = row
    assert local_path == "meeting.md", \
        f"local_path should be the relative path, got {local_path!r}"
    assert title == "重构讨论", f"title mismatch: {title!r}"
    assert description == "讨论OKF重构方向", f"description mismatch: {description!r}"
    assert doc_type == "Meeting Minutes", f"doc_type mismatch: {doc_type!r}"
    assert project == "demo", f"project mismatch: {project!r}"
    assert tags == "重构, OKF", f"tags should be comma-joined list, got {tags!r}"
    assert people == "张三, 李四", f"people should be comma-joined list, got {people!r}"
    assert content_hash.startswith("sha256:"), \
        f"content_hash should be sha256-prefixed, got {content_hash!r}"


def test_update_index_hash_skip(tmp_path):
    """update_index() skips re-indexing when content_hash is unchanged.

    Two documents are indexed first (so the second document holds a higher
    rowid). Then the first document is re-indexed with unchanged content.
    Because the hash matches, the skip path is taken and the first
    document's rowid stays the same. Had the skip NOT been taken,
    ``INSERT OR REPLACE`` would delete + re-insert the row, allocating a
    new rowid above the second document's.
    """
    engine = QueryEngine(str(tmp_path))

    okf_a = tmp_path / "a.md"
    okf_a.write_text(SAMPLE_OKF, encoding="utf-8")
    okf_b = tmp_path / "b.md"
    okf_b.write_text(
        SAMPLE_OKF.replace("重构讨论", "需求评审").replace("讨论OKF重构方向",
                                                         "讨论新需求"),
        encoding="utf-8",
    )

    engine.update_index(str(okf_a))
    engine.update_index(str(okf_b))

    conn = sqlite3.connect(engine.db_path)
    try:
        rowid_a_before = conn.execute(
            "SELECT rowid FROM documents WHERE local_path = ?", ("a.md",)
        ).fetchone()[0]
    finally:
        conn.close()

    # Re-index a.md with NO content change -> should hit the hash-skip path.
    engine.update_index(str(okf_a))

    conn = sqlite3.connect(engine.db_path)
    try:
        rowid_a_after = conn.execute(
            "SELECT rowid FROM documents WHERE local_path = ?", ("a.md",)
        ).fetchone()[0]
        count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    finally:
        conn.close()

    assert rowid_a_before == rowid_a_after, \
        "rowid must be unchanged when content_hash matches (skip path taken)"
    assert count == 2, f"should still have 2 docs, got {count}"


def test_update_index_chinese_body(tmp_path):
    """update_index() applies CJK spacing so single-char FTS5 search works.

    After indexing, the ``body_text`` column must contain spaces between
    consecutive CJK characters (e.g. ``重 构``). A single-character FTS5
    ``MATCH`` query must then return the document -- this is the whole
    point of the CJK-spacing workaround for ``unicode61``.
    """
    engine = QueryEngine(str(tmp_path))
    okf_path = tmp_path / "chinese.md"
    okf_path.write_text(SAMPLE_OKF, encoding="utf-8")

    engine.update_index(str(okf_path))

    conn = sqlite3.connect(engine.db_path)
    try:
        body_text = conn.execute(
            "SELECT body_text FROM documents WHERE local_path = ?",
            ("chinese.md",),
        ).fetchone()[0]

        # CJK spacing applied: consecutive CJK chars are separated by spaces.
        assert "重 构" in body_text, \
            f"body_text should have spaces between CJK chars, got: {body_text!r}"
        assert "本 次 会 议" in body_text, \
            f"body_text should have spaced CJK run, got: {body_text!r}"

        # Single Chinese character search via FTS5 -- only possible because
        # the CJK-spacing preprocessing made each character a separate token.
        single_char_matches = conn.execute(
            "SELECT documents.local_path FROM documents_fts "
            "JOIN documents ON documents.rowid = documents_fts.rowid "
            "WHERE documents_fts MATCH ?",
            ("重",),
        ).fetchall()
        assert single_char_matches, \
            "single CJK char '重' must match after CJK-spacing preprocessing"
        assert single_char_matches[0][0] == "chinese.md"
    finally:
        conn.close()


def test_remove_from_index(tmp_path):
    """remove_from_index() deletes a document from both documents and FTS."""
    engine = QueryEngine(str(tmp_path))
    okf_path = tmp_path / "to_remove.md"
    okf_path.write_text(SAMPLE_OKF, encoding="utf-8")

    engine.update_index(str(okf_path))

    conn = sqlite3.connect(engine.db_path)
    try:
        before = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE local_path = ?",
            ("to_remove.md",),
        ).fetchone()[0]
    finally:
        conn.close()
    assert before == 1, "doc should exist before removal"

    engine.remove_from_index(str(okf_path))

    conn = sqlite3.connect(engine.db_path)
    try:
        after = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE local_path = ?",
            ("to_remove.md",),
        ).fetchone()[0]
        fts_after = conn.execute(
            "SELECT COUNT(*) FROM documents_fts "
            "JOIN documents ON documents.rowid = documents_fts.rowid "
            "WHERE documents.local_path = ?",
            ("to_remove.md",),
        ).fetchone()[0]
    finally:
        conn.close()
    assert after == 0, "doc should be gone from documents table after removal"
    assert fts_after == 0, "doc should be gone from FTS index after removal"


def test_rebuild_index(tmp_path):
    """rebuild_index() scans the bundle, skipping nav files and hidden dirs.

    Layout::

        tmp_path/
          doc1.md            <- indexed
          subdir/doc2.md     <- indexed (subdirectory is not hidden)
          index.md           <- skipped (navigation file)
          log.md             <- skipped (log file)
          .hidden/doc3.md    <- skipped (hidden directory)

    Returns 2 and the database contains exactly 2 documents.
    """
    engine = QueryEngine(str(tmp_path))

    # doc1 at root.
    (tmp_path / "doc1.md").write_text(SAMPLE_OKF, encoding="utf-8")
    # doc2 in a subdirectory (not hidden).
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    (subdir / "doc2.md").write_text(
        SAMPLE_OKF.replace("重构讨论", "子目录文档"), encoding="utf-8"
    )
    # index.md -- navigation file, must be skipped.
    (tmp_path / "index.md").write_text("# Index\n", encoding="utf-8")
    # log.md -- log file, must be skipped.
    (tmp_path / "log.md").write_text("# Log\n", encoding="utf-8")
    # doc3 in a hidden directory -- must be skipped.
    hidden = tmp_path / ".hidden"
    hidden.mkdir()
    (hidden / "doc3.md").write_text(SAMPLE_OKF, encoding="utf-8")

    count = engine.rebuild_index()
    assert count == 2, \
        f"rebuild_index should return 2 (skip index.md, log.md, .hidden/), got {count}"

    conn = sqlite3.connect(engine.db_path)
    try:
        rows = conn.execute(
            "SELECT local_path FROM documents ORDER BY local_path"
        ).fetchall()
    finally:
        conn.close()

    paths = [r[0] for r in rows]
    assert len(paths) == 2, f"expected 2 docs in DB, got {len(paths)}: {paths}"
    assert "doc1.md" in paths, "doc1.md should be indexed"
    assert os.path.join("subdir", "doc2.md") in paths, \
        f"subdir/doc2.md should be indexed, got {paths}"
    assert "index.md" not in paths, "index.md must be skipped"
    assert "log.md" not in paths, "log.md must be skipped"
    assert os.path.join(".hidden", "doc3.md") not in paths, \
        ".hidden/doc3.md must be skipped"


def test_extract_body_text_strips_markdown(tmp_path):
    """_extract_body_text() strips markdown and applies CJK spacing.

    Feeds a markdown body with headers, bold, italic, inline code, a fenced
    code block, a link, an image, and list markers. Asserts all markdown
    syntax is removed, link/image alt text is preserved, and consecutive
    CJK characters are space-separated.
    """
    engine = QueryEngine(str(tmp_path))
    markdown_body = (
        "# Title\n"
        "\n"
        "This is **bold** and *italic* text.\n"
        "\n"
        "## Subsection\n"
        "\n"
        "- Item one\n"
        "- Item two\n"
        "\n"
        "```python\n"
        "code_block()\n"
        "```\n"
        "\n"
        "Inline `code` here.\n"
        "\n"
        "[link text](https://example.com)\n"
        "\n"
        "![image alt](https://example.com/img.png)\n"
        "\n"
        "重构讨论是一个重要的话题。\n"
    )

    result = engine._extract_body_text(markdown_body)

    # Markdown syntax removed.
    assert "#" not in result, f"header markers should be stripped, got: {result!r}"
    assert "**" not in result, f"bold markers should be stripped, got: {result!r}"
    assert "`" not in result, f"code backticks should be stripped, got: {result!r}"
    assert "](https://" not in result, \
        f"link URLs should be stripped, got: {result!r}"
    assert "- Item" not in result, \
        f"list markers should be stripped, got: {result!r}"
    assert "code_block()" not in result, \
        f"fenced code block should be removed, got: {result!r}"

    # Content preserved.
    assert "Title" in result, "header text should be preserved"
    assert "bold" in result, "bold text should be preserved"
    assert "italic" in result, "italic text should be preserved"
    assert "code" in result, "inline code text should be preserved"
    assert "link text" in result, "link text should be preserved"
    assert "image alt" in result, "image alt text should be preserved"
    assert "Item one" in result, "list item text should be preserved"

    # CJK spacing applied.
    assert "重 构 讨 论" in result, \
        f"consecutive CJK chars should be space-separated, got: {result!r}"


# ===========================================================================
# Task 10: search with FTS5 recall and structured filtering
# ===========================================================================


def _make_okf(title, doc_type, project, body, tags=None, people=None,
              mentions=None, timestamp="2026-07-01T10:00:00+08:00"):
    """Build an OKF markdown string with YAML frontmatter for search tests."""
    lines = ["---"]
    lines.append(f"type: {doc_type}")
    lines.append(f'title: "{title}"')
    lines.append(f"description: {title}")
    lines.append(f"tags: [{', '.join(tags or [])}]")
    lines.append(f"timestamp: {timestamp}")
    lines.append(f"project: {project}")
    lines.append(f"people: [{', '.join(people or [])}]")
    if mentions is not None:
        lines.append(f"mentions: [{', '.join(mentions)}]")
    lines.append("---")
    lines.append("")
    lines.append(body)
    return "\n".join(lines)


def _index_okf(engine, tmp_path, filename, okf_text):
    """Write an OKF file under tmp_path and index it. Returns the file path."""
    p = tmp_path / filename
    p.write_text(okf_text, encoding="utf-8")
    engine.update_index(str(p))
    return p


# ---------------------------------------------------------------------------
# 1. test_search_basic_keyword
# ---------------------------------------------------------------------------

def test_search_basic_keyword(tmp_path):
    """search() returns only docs matching the keyword, sorted by score desc.

    Three docs are indexed; two contain the keyword "architecture". The
    search must return exactly those two, ordered by descending score.
    """
    engine = QueryEngine(str(tmp_path))
    _index_okf(engine, tmp_path, "doc_a.md", _make_okf(
        "Architecture Design", "Meeting Minutes", "demo",
        "# Summary\n\narchitecture design for the new system.\n",
        tags=["arch"], people=["Alice"]))
    _index_okf(engine, tmp_path, "doc_b.md", _make_okf(
        "Architecture Review", "Review", "demo",
        "# Summary\n\narchitecture review notes from last week.\n",
        tags=["review"], people=["Bob"]))
    _index_okf(engine, tmp_path, "doc_c.md", _make_okf(
        "Marketing Plan", "Requirement", "marketing",
        "# Summary\n\nmarketing plan for q3 release.\n",
        tags=["mkt"], people=["Carol"]))

    result = engine.search("architecture")

    assert isinstance(result, SearchResult)
    assert result.total_found == 2, \
        f"expected 2 matches, got {result.total_found}"
    titles = {m.title for m in result.matches}
    assert titles == {"Architecture Design", "Architecture Review"}, \
        f"unexpected match titles: {titles}"
    # Sorted by score descending.
    assert result.matches[0].score >= result.matches[1].score, \
        "matches should be sorted by score descending"


# ---------------------------------------------------------------------------
# 2. test_search_chinese_keyword
# ---------------------------------------------------------------------------

def test_search_chinese_keyword(tmp_path):
    """search() with a single Chinese character finds CJK-indexed docs.

    This verifies _preprocess_query applies CJK spacing to the query so
    that MATCH '重' hits the spaced body_text token '重'.
    """
    engine = QueryEngine(str(tmp_path))
    _index_okf(engine, tmp_path, "cn.md", _make_okf(
        "重构讨论", "Meeting Minutes", "demo",
        "# Summary\n\n本次会议讨论了重构方向，确定采用Pipeline架构。\n",
        tags=["重构"], people=["张三"]))

    result = engine.search("重")

    assert result.total_found == 1, \
        f"single CJK char search should find 1 doc, got {result.total_found}"
    assert result.matches[0].title == "重构讨论"


# ---------------------------------------------------------------------------
# 3. test_search_with_project_filter
# ---------------------------------------------------------------------------

def test_search_with_project_filter(tmp_path):
    """search() with filters.project keeps only docs in that project."""
    engine = QueryEngine(str(tmp_path))
    _index_okf(engine, tmp_path, "a1.md", _make_okf(
        "Report A1", "Meeting Minutes", "project_a",
        "# Summary\n\nquarterly report for project a.\n"))
    _index_okf(engine, tmp_path, "a2.md", _make_okf(
        "Report A2", "Requirement", "project_a",
        "# Summary\n\nannual report for project a.\n"))
    _index_okf(engine, tmp_path, "b1.md", _make_okf(
        "Report B1", "Meeting Minutes", "project_b",
        "# Summary\n\nstatus report for project b.\n"))
    _index_okf(engine, tmp_path, "b2.md", _make_okf(
        "Report B2", "Requirement", "project_b",
        "# Summary\n\nbudget report for project b.\n"))

    result = engine.search("report", filters=SearchFilters(project="project_a"))

    assert result.total_found == 2, \
        f"project filter should leave 2 docs, got {result.total_found}"
    for m in result.matches:
        assert m.local_path in ("a1.md", "a2.md"), \
            f"only project_a docs expected, got {m.local_path}"


# ---------------------------------------------------------------------------
# 4. test_search_with_type_filter
# ---------------------------------------------------------------------------

def test_search_with_type_filter(tmp_path):
    """search() with filters.doc_type keeps only docs of that type."""
    engine = QueryEngine(str(tmp_path))
    _index_okf(engine, tmp_path, "mm.md", _make_okf(
        "Meeting Report", "Meeting Minutes", "demo",
        "# Summary\n\nreport from the meeting.\n"))
    _index_okf(engine, tmp_path, "req.md", _make_okf(
        "Requirement Report", "Requirement", "demo",
        "# Summary\n\nreport for the requirement.\n"))
    _index_okf(engine, tmp_path, "rev.md", _make_okf(
        "Review Report", "Review", "demo",
        "# Summary\n\nreport for the review.\n"))

    result = engine.search("report", filters=SearchFilters(doc_type="Meeting Minutes"))

    assert result.total_found == 1, \
        f"type filter should leave 1 doc, got {result.total_found}"
    assert result.matches[0].title == "Meeting Report"
    assert result.matches[0].doc_type == "Meeting Minutes"


# ---------------------------------------------------------------------------
# 5. test_search_no_results
# ---------------------------------------------------------------------------

def test_search_no_results(tmp_path):
    """search() for a non-existent keyword returns an empty SearchResult."""
    engine = QueryEngine(str(tmp_path))
    _index_okf(engine, tmp_path, "doc.md", _make_okf(
        "Some Doc", "Meeting Minutes", "demo",
        "# Summary\n\nthis document talks about pipelines.\n"))

    result = engine.search("zzznonexistentkeyword")

    assert result.total_found == 0
    assert result.matches == []
    assert result.context == ""


# ---------------------------------------------------------------------------
# 6. test_search_returns_snippet
# ---------------------------------------------------------------------------

def test_search_returns_snippet(tmp_path):
    """search() populates DocMatch.snippet with the matched text excerpt."""
    engine = QueryEngine(str(tmp_path))
    _index_okf(engine, tmp_path, "doc.md", _make_okf(
        "Snippet Doc", "Meeting Minutes", "demo",
        "# Summary\n\narchitecture design for the new system.\n"))

    result = engine.search("architecture")

    assert result.total_found == 1
    m = result.matches[0]
    assert m.snippet, "snippet should be non-empty"
    assert "architecture" in m.snippet.lower(), \
        f"snippet should contain the keyword, got: {m.snippet!r}"


# ---------------------------------------------------------------------------
# 7. test_search_deep_read
# ---------------------------------------------------------------------------

def test_search_deep_read(tmp_path):
    """search() with deep_read=True fills context and full_content."""
    engine = QueryEngine(str(tmp_path))
    _index_okf(engine, tmp_path, "doc.md", _make_okf(
        "重构讨论", "Meeting Minutes", "demo",
        "# Summary\n\n本次会议讨论了重构方向，确定采用Pipeline架构。\n",
        tags=["重构"], people=["张三"]))

    result = engine.search("重构", deep_read=True)

    assert result.total_found == 1
    m = result.matches[0]
    assert m.full_content is not None, "full_content should be filled on deep read"
    assert result.context, "context should be non-empty on deep read"
    # Context contains the raw file content (title + body).
    assert "重构讨论" in result.context, \
        "context should contain the document title"
    assert "Pipeline" in result.context, \
        "context should contain body content"


# ---------------------------------------------------------------------------
# 8. test_search_no_deep_read
# ---------------------------------------------------------------------------

def test_search_no_deep_read(tmp_path):
    """search() with deep_read=False leaves context empty and full_content None."""
    engine = QueryEngine(str(tmp_path))
    _index_okf(engine, tmp_path, "doc.md", _make_okf(
        "重构讨论", "Meeting Minutes", "demo",
        "# Summary\n\n本次会议讨论了重构方向，确定采用Pipeline架构。\n",
        tags=["重构"], people=["张三"]))

    result = engine.search("重构", deep_read=False)

    assert result.total_found == 1
    assert result.context == "", "context should be empty when deep_read=False"
    assert result.matches[0].full_content is None, \
        "full_content should be None when deep_read=False"
    assert result.matches[0].related_docs is None, \
        "related_docs should be None when deep_read=False"


# ---------------------------------------------------------------------------
# 9. test_calculate_score_time_decay
# ---------------------------------------------------------------------------

def test_calculate_score_time_decay(tmp_path):
    """_calculate_score() gives recent docs a higher score than old docs."""
    engine = QueryEngine(str(tmp_path))
    fts_rank = -1.0
    doc_type = "Meeting Minutes"
    recent = engine._calculate_score(fts_rank, doc_type, "2026-07-21T00:00:00+08:00")
    old = engine._calculate_score(fts_rank, doc_type, "2025-07-21T00:00:00+08:00")

    assert recent > old, \
        f"recent doc ({recent}) should score higher than old doc ({old})"
    # Sanity-check the time-decay band contributions.
    # fts_score = 1/(1+1) = 0.5; type_weight = 1.0.
    # recent: 0.5*0.6 + 1.0*0.2 + 1.0*0.2 = 0.7
    # old:     0.5*0.6 + 0.3*0.2 + 1.0*0.2 = 0.56
    assert abs(recent - 0.7) < 1e-9, f"recent score expected ~0.7, got {recent}"
    assert abs(old - 0.56) < 1e-9, f"old score expected ~0.56, got {old}"


# ---------------------------------------------------------------------------
# 10. test_calculate_score_type_weight
# ---------------------------------------------------------------------------

def test_calculate_score_type_weight(tmp_path):
    """_calculate_score() weights Meeting Minutes above generic types."""
    engine = QueryEngine(str(tmp_path))
    fts_rank = -1.0
    modified_time = "2026-07-21T00:00:00+08:00"
    mm = engine._calculate_score(fts_rank, "Meeting Minutes", modified_time)
    other = engine._calculate_score(fts_rank, "Other", modified_time)

    assert mm > other, \
        f"Meeting Minutes ({mm}) should score higher than Other ({other})"
    # type_weight: MM=1.0, Other=0.6; fts=0.5; time=1.0.
    # mm:    0.5*0.6 + 1.0*0.2 + 1.0*0.2 = 0.7
    # other: 0.5*0.6 + 1.0*0.2 + 0.6*0.2 = 0.62
    assert abs(mm - 0.7) < 1e-9, f"MM score expected ~0.7, got {mm}"
    assert abs(other - 0.62) < 1e-9, f"Other score expected ~0.62, got {other}"


# ---------------------------------------------------------------------------
# 11. test_preprocess_query_cjk
# ---------------------------------------------------------------------------

def test_preprocess_query_cjk(tmp_path):
    """_preprocess_query() inserts spaces between consecutive CJK chars."""
    engine = QueryEngine(str(tmp_path))
    assert engine._preprocess_query("重构讨论") == "重 构 讨 论"
    # ASCII queries are left untouched.
    assert engine._preprocess_query("architecture") == "architecture"
    # Mixed CJK + ASCII keeps CJK spacing without trailing spaces.
    assert engine._preprocess_query("重构Pipeline") == "重 构Pipeline"


# ---------------------------------------------------------------------------
# 12. test_deep_read_context_limit
# ---------------------------------------------------------------------------

def test_deep_read_context_limit(tmp_path):
    """_deep_read() never produces a context longer than max_context_chars."""
    engine = QueryEngine(str(tmp_path))
    large_body = "# Summary\n\n" + ("alpha content " * 80)
    for name in ("big1.md", "big2.md", "big3.md"):
        _index_okf(engine, tmp_path, name, _make_okf(
            name, "Meeting Minutes", "demo", large_body,
            tags=["big"], people=["Alice"]))

    # Recall matches without deep reading first.
    result = engine.search("alpha", deep_read=False)
    assert len(result.matches) == 3

    context = engine._deep_read(result.matches, max_context_chars=200)
    assert len(context) <= 200, \
        f"context must not exceed limit, got {len(context)} chars"
    assert len(context) > 0, "context should contain at least one entry"


# ---------------------------------------------------------------------------
# 13. test_deep_read_related_docs
# ---------------------------------------------------------------------------

def test_deep_read_related_docs(tmp_path):
    """_deep_read() follows frontmatter mentions and lists related docs."""
    engine = QueryEngine(str(tmp_path))
    _index_okf(engine, tmp_path, "main.md", _make_okf(
        "带关联的文档", "Meeting Minutes", "demo",
        "# Summary\n\n这是一篇带有关联文档的笔记。\n",
        tags=["test"], people=["张三"],
        mentions=["/people/Alice.md", "/concepts/OKF.md"]))

    # Create the related doc files inside the bundle.
    people_dir = tmp_path / "people"
    people_dir.mkdir()
    (people_dir / "Alice.md").write_text(
        "---\ntype: Reference\ntitle: \"Alice的资料\"\n---\n\nAlice是一位工程师。\n",
        encoding="utf-8")
    concepts_dir = tmp_path / "concepts"
    concepts_dir.mkdir()
    (concepts_dir / "OKF.md").write_text(
        "---\ntype: Reference\ntitle: \"OKF概念\"\n---\n\nOKF是目标关键结果框架。\n",
        encoding="utf-8")

    result = engine.search("关联", deep_read=True)

    assert result.total_found == 1, \
        f"expected 1 match, got {result.total_found}"
    m = result.matches[0]
    assert m.related_docs is not None, "related_docs should be populated"
    assert "/people/Alice.md" in m.related_docs
    assert "/concepts/OKF.md" in m.related_docs
    assert "Related:" in result.context, \
        "context should mention related docs"
    assert "Alice" in result.context, \
        "context should contain the related doc content"
