"""QueryEngine: SQLite FTS5-based progressive RAG query engine.

Replaces the old query.py substring matching with full-text search +
structured filtering + deep read. Task 8 delivers the FTS5 schema only;
indexing and search are added in Tasks 9-10.
"""
from __future__ import annotations
import sqlite3
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SearchFilters:
    """Structured filtering options for search."""
    project: Optional[str] = None
    doc_type: Optional[str] = None
    tags: Optional[list] = None
    people: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None


@dataclass
class DocMatch:
    """A single document match from search."""
    local_path: str
    title: str
    doc_type: str
    score: float
    snippet: str               # FTS5 snippet (matched text excerpt)
    full_content: Optional[str] = None        # filled when deep_read=True
    related_docs: Optional[list] = None       # mention paths


@dataclass
class SearchResult:
    """Complete search result."""
    matches: list              # list[DocMatch]
    context: str               # assembled Agent context (when deep_read=True)
    total_found: int


class QueryEngine:
    """Progressive RAG query engine using SQLite FTS5.

    Three-stage query (implemented in Tasks 9-10):
    1. FTS5 full-text recall (keyword matching)
    2. Structured filtering (type/project/tags/people)
    3. Deep read (full content of top matches)

    Args:
        bundle_path: Path to the local bundle directory.
    """

    def __init__(self, bundle_path: str):
        self.bundle_path = bundle_path
        self.db_path = os.path.join(bundle_path, ".index", "search.db")

    def ensure_index(self) -> None:
        """Create the SQLite database with FTS5 schema if it doesn't exist.

        Creates:
        - .index/ directory
        - documents table (metadata + body_text)
        - documents_fts virtual table (FTS5 with unicode61 tokenizer)
        - Sync triggers (INSERT/UPDATE/DELETE on documents -> documents_fts)
        """
        # Create directory
        index_dir = os.path.join(self.bundle_path, ".index")
        os.makedirs(index_dir, exist_ok=True)

        conn = sqlite3.connect(self.db_path)
        try:
            # Documents main table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    local_path TEXT PRIMARY KEY,
                    feishu_node_token TEXT,
                    title TEXT,
                    description TEXT,
                    doc_type TEXT,
                    project TEXT,
                    tags TEXT,
                    people TEXT,
                    body_text TEXT,
                    modified_time TEXT,
                    content_hash TEXT
                )
            """)

            # FTS5 virtual table (contentless external content approach)
            # Using content='documents' to link FTS index to documents table
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                    title,
                    description,
                    body_text,
                    tags,
                    people,
                    content='documents',
                    content_rowid='rowid',
                    tokenize='unicode61'
                )
            """)

            # Sync triggers: keep FTS table in sync with documents table
            # AFTER INSERT
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
                    INSERT INTO documents_fts(rowid, title, description, body_text, tags, people)
                    VALUES (new.rowid, new.title, new.description, new.body_text, new.tags, new.people);
                END
            """)

            # AFTER DELETE
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
                    INSERT INTO documents_fts(documents_fts, rowid, title, description, body_text, tags, people)
                    VALUES ('delete', old.rowid, old.title, old.description, old.body_text, old.tags, old.people);
                END
            """)

            # AFTER UPDATE
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
                    INSERT INTO documents_fts(documents_fts, rowid, title, description, body_text, tags, people)
                    VALUES ('delete', old.rowid, old.title, old.description, old.body_text, old.tags, old.people);
                    INSERT INTO documents_fts(rowid, title, description, body_text, tags, people)
                    VALUES (new.rowid, new.title, new.description, new.body_text, new.tags, new.people);
                END
            """)

            conn.commit()
        finally:
            conn.close()
