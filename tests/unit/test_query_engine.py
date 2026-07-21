"""Unit tests for QueryEngine: FTS5 schema creation (Task 8).

Covers the schema-only slice of :mod:`query_engine`:

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
