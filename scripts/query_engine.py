"""QueryEngine: SQLite FTS5-based progressive RAG query engine.

Replaces the old query.py substring matching with full-text search +
structured filtering + deep read. Task 8 delivered the FTS5 schema;
Task 9 adds index build/update operations; Task 10 will add search.
"""
from __future__ import annotations
import sqlite3
import os
import re
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Optional

# Reuse the frontmatter parser from wiki_connector (Task 4). Both modules
# live in scripts/, so the import works whenever scripts/ is on sys.path
# (tests add it explicitly; the runtime cwd is scripts/).
from wiki_connector import _parse_frontmatter

logger = logging.getLogger(__name__)


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

    # ------------------------------------------------------------------
    # Task 9: index build and update operations
    # ------------------------------------------------------------------

    def _parse_okf(self, content: str) -> tuple[dict, str]:
        """Parse OKF Markdown into ``(frontmatter_dict, body_str)``.

        Thin wrapper around :func:`wiki_connector._parse_frontmatter` that
        extracts the YAML frontmatter and returns the body. Named
        ``_parse_okf`` for clarity at the call site. When no frontmatter is
        present, returns ``({}, content)``.
        """
        return _parse_frontmatter(content)

    def _compute_hash(self, content: str) -> str:
        """Compute a SHA-256 content hash for change detection.

        Returns the hash prefixed with ``"sha256:"`` so the hash algorithm
        is self-describing in the database.
        """
        return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _extract_body_text(self, content: str) -> str:
        """Strip markdown formatting and apply CJK spacing for FTS indexing.

        Removes: fenced code blocks, inline code, images (alt kept), links
        (text kept), HTML tags, header markers, bold/italic markers, and
        list markers. Then inserts a space between every pair of consecutive
        CJK characters so that FTS5's ``unicode61`` tokenizer treats each
        CJK character as a separate token (otherwise an unbroken CJK run is
        indexed as a single token and single-character queries never match).

        Args:
            content: Markdown body (frontmatter already stripped).

        Returns:
            Plain text suitable for the FTS ``body_text`` column.
        """
        text = content

        # 1. Fenced code blocks (```...```) -- remove entirely.
        text = re.sub(r'```.*?```', ' ', text, flags=re.DOTALL)

        # 2. Inline code (`...`) -- keep the inner text.
        text = re.sub(r'`([^`]*)`', r'\1', text)

        # 3. Images: ![alt](url) -> alt (process before links).
        text = re.sub(r'!\[([^\]]*)\]\([^)]*\)', r'\1', text)

        # 4. Links: [text](url) -> text.
        text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)

        # 5. HTML tags.
        text = re.sub(r'<[^>]+>', ' ', text)

        # 6. Header markers (# at line start, 1-6 levels).
        text = re.sub(r'^\s{0,3}#{1,6}\s+', '', text, flags=re.MULTILINE)

        # 7. Bold (**...** / __...__).
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'__(.+?)__', r'\1', text)

        # 8. Italic (*...* / _..._).
        text = re.sub(r'\*(.+?)\*', r'\1', text)
        text = re.sub(r'_(.+?)_', r'\1', text)

        # 9. List markers (-, *, +, digit.) at line start.
        text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)

        # 10. CJK character spacing: insert a space between two consecutive
        #     CJK characters (lookahead so we don't trail a space after the
        #     last CJK char before a non-CJK char / end of string).
        text = re.sub(
            r'([\u4e00-\u9fff\u3400-\u4dbf])(?=[\u4e00-\u9fff\u3400-\u4dbf])',
            r'\1 ', text,
        )

        # 11. Collapse all whitespace runs to a single space.
        text = re.sub(r'\s+', ' ', text)

        # 12. Strip leading/trailing whitespace.
        return text.strip()

    @staticmethod
    def _list_to_str(value) -> str:
        """Join a list frontmatter field into a comma-separated string.

        Lists become ``", "``-joined; scalars are stringified; missing
        values become ``""``.
        """
        if isinstance(value, list):
            return ", ".join(str(v) for v in value)
        if value is None:
            return ""
        return str(value)

    def update_index(self, okf_path: str) -> None:
        """Index a single OKF document (incremental update).

        Reads the file, parses its frontmatter, computes a content hash, and
        skips the write when the hash is unchanged. Otherwise inserts (or
        replaces) the document row; the ``documents_ai`` / ``documents_au``
        triggers keep the FTS index in sync.

        Args:
            okf_path: Full absolute path to the ``.md`` file.
        """
        self.ensure_index()

        with open(okf_path, "r", encoding="utf-8") as f:
            content = f.read()

        fm, body = self._parse_okf(content)
        new_hash = self._compute_hash(content)
        rel_path = os.path.relpath(okf_path, self.bundle_path)

        conn = sqlite3.connect(self.db_path)
        try:
            with conn:
                # Skip if the document is already indexed with the same hash.
                existing = conn.execute(
                    "SELECT content_hash FROM documents WHERE local_path = ?",
                    (rel_path,),
                ).fetchone()
                if existing is not None and existing[0] == new_hash:
                    logger.debug(
                        "update_index: skip unchanged doc %s (hash %s)",
                        rel_path, new_hash,
                    )
                    return

                body_text = self._extract_body_text(body)
                tags_str = self._list_to_str(fm.get("tags"))
                people_str = self._list_to_str(fm.get("people"))

                conn.execute(
                    """
                    INSERT OR REPLACE INTO documents
                    (local_path, feishu_node_token, title, description,
                     doc_type, project, tags, people, body_text,
                     modified_time, content_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        rel_path,
                        fm.get("resource", "") or "",
                        fm.get("title", "") or "",
                        fm.get("description", "") or "",
                        fm.get("type", "") or "",
                        fm.get("project", "") or "",
                        tags_str,
                        people_str,
                        body_text,
                        str(fm.get("timestamp") or ""),
                        new_hash,
                    ),
                )
        finally:
            conn.close()

    def remove_from_index(self, okf_path: str) -> None:
        """Remove a document from the index.

        Deletes the row from ``documents``; the ``documents_ad`` trigger
        removes the corresponding FTS entry.

        Args:
            okf_path: Full absolute path to the ``.md`` file.
        """
        self.ensure_index()
        rel_path = os.path.relpath(okf_path, self.bundle_path)

        conn = sqlite3.connect(self.db_path)
        try:
            with conn:
                conn.execute(
                    "DELETE FROM documents WHERE local_path = ?",
                    (rel_path,),
                )
        finally:
            conn.close()

    def rebuild_index(self) -> int:
        """Full rebuild: scan all ``.md`` files in the bundle and index each.

        Walks ``self.bundle_path`` recursively, skipping hidden directories
        (names starting with ``.``) and navigation/log files (``index.md``,
        ``log.md``). Clears the existing index first to remove stale entries,
        then calls :meth:`update_index` for each surviving ``.md`` file.

        Returns:
            The number of documents indexed.
        """
        self.ensure_index()

        # Clear existing index to drop stale entries (deleted files).
        conn = sqlite3.connect(self.db_path)
        try:
            with conn:
                conn.execute("DELETE FROM documents")
        finally:
            conn.close()

        skip_filenames = {"index.md", "log.md"}
        count = 0
        for root, dirs, files in os.walk(self.bundle_path):
            # Prune hidden directories in-place (skips .index/, .conflicts/,
            # .git/, etc. for this and all deeper levels).
            dirs[:] = [d for d in dirs if not d.startswith(".")]

            for fname in files:
                if not fname.endswith(".md"):
                    continue
                if fname in skip_filenames:
                    continue
                full_path = os.path.join(root, fname)
                self.update_index(full_path)
                count += 1

        logger.info("rebuild_index: indexed %d documents under %s",
                    count, self.bundle_path)
        return count
