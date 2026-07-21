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
from datetime import datetime, timezone
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

            # FTS5 virtual table (standalone, not external-content).
            # We use a standalone FTS5 table (no content= option) so that
            # the FTS index can store CJK-spaced text for matching while
            # the documents table stores original text for display and
            # structured filtering. FTS entries are managed manually in
            # update_index / remove_from_index / rebuild_index.
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                    title,
                    description,
                    body_text,
                    tags,
                    people,
                    tokenize='unicode61'
                )
            """)

            # Migration: drop legacy triggers from older schema versions
            # that used content='documents' with sync triggers. These
            # triggers are no longer needed (FTS is managed manually) and
            # would cause errors if they fire on the standalone FTS table.
            for trigger_name in ("documents_ai", "documents_ad",
                                 "documents_au"):
                conn.execute(
                    f"DROP TRIGGER IF EXISTS {trigger_name}"
                )

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

        # 10. CJK character spacing: delegate to the shared helper so the
        #     same tokenization is applied to body text, metadata fields,
        #     and search queries.
        text = self._apply_cjk_spacing(text)

        # 11. Collapse all whitespace runs to a single space.
        text = re.sub(r'\s+', ' ', text)

        # 12. Strip leading/trailing whitespace.
        return text.strip()

    @staticmethod
    def _apply_cjk_spacing(text: str) -> str:
        """Insert a space between every pair of consecutive CJK characters.

        FTS5's ``unicode61`` tokenizer treats an unbroken run of CJK
        characters as a single token, so single-character or multi-character
        CJK queries never match. Inserting a space between each pair makes
        every CJK character its own token, enabling sub-string matching via
        ``AND`` / phrase queries.

        Used by :meth:`_extract_body_text` (body text), :meth:`update_index`
        (title / description / tags / people), and :meth:`_preprocess_query`
        (search queries) so that the same tokenization is applied to indexed
        content and to user queries.
        """
        if not text:
            return text
        return re.sub(
            r'([\u4e00-\u9fff\u3400-\u4dbf])(?=[\u4e00-\u9fff\u3400-\u4dbf])',
            r'\1 ', text,
        )

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
        replaces) the document row and manually syncs the FTS index with
        CJK-spaced text for all searchable fields.

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

                # Store ORIGINAL (unspaced) text in documents table for
                # display and structured filtering (tags/people matching).
                tags_str = self._list_to_str(fm.get("tags"))
                people_str = self._list_to_str(fm.get("people"))
                orig_title = fm.get("title", "") or ""
                orig_desc = fm.get("description", "") or ""

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
                        orig_title,
                        orig_desc,
                        fm.get("type", "") or "",
                        fm.get("project", "") or "",
                        tags_str,
                        people_str,
                        body_text,
                        str(fm.get("timestamp") or ""),
                        new_hash,
                    ),
                )

                # Manually sync the FTS index with CJK-spaced text.
                # The FTS table is standalone (no triggers), so we must
                # delete the old entry and insert the new one explicitly.
                # CJK spacing is applied to ALL text fields so that FTS5
                # can match CJK keywords against title, description, tags,
                # and people -- not just body_text.
                rowid_row = conn.execute(
                    "SELECT rowid FROM documents WHERE local_path = ?",
                    (rel_path,),
                ).fetchone()
                if rowid_row is not None:
                    fts_rowid = rowid_row[0]
                    conn.execute(
                        "DELETE FROM documents_fts WHERE rowid = ?",
                        (fts_rowid,),
                    )
                    conn.execute(
                        """
                        INSERT INTO documents_fts
                            (rowid, title, description, body_text, tags, people)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            fts_rowid,
                            self._apply_cjk_spacing(orig_title),
                            self._apply_cjk_spacing(orig_desc),
                            body_text,
                            self._apply_cjk_spacing(tags_str),
                            self._apply_cjk_spacing(people_str),
                        ),
                    )
        finally:
            conn.close()

    def remove_from_index(self, okf_path: str) -> None:
        """Remove a document from the index.

        Deletes the row from both ``documents`` and ``documents_fts``.
        Since the FTS table is standalone (no triggers), both deletions
        are explicit.

        Args:
            okf_path: Full absolute path to the ``.md`` file.
        """
        self.ensure_index()
        rel_path = os.path.relpath(okf_path, self.bundle_path)

        conn = sqlite3.connect(self.db_path)
        try:
            with conn:
                # Get rowid before deleting from documents.
                row = conn.execute(
                    "SELECT rowid FROM documents WHERE local_path = ?",
                    (rel_path,),
                ).fetchone()
                if row is not None:
                    conn.execute(
                        "DELETE FROM documents_fts WHERE rowid = ?",
                        (row[0],),
                    )
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
        # Both tables must be cleared since FTS is managed manually.
        conn = sqlite3.connect(self.db_path)
        try:
            with conn:
                conn.execute("DELETE FROM documents")
                conn.execute("DELETE FROM documents_fts")
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

    # ------------------------------------------------------------------
    # Task 10: search with FTS5 recall and structured filtering
    # ------------------------------------------------------------------

    def _preprocess_query(self, query: str) -> str:
        """Apply CJK character spacing to a search query.

        Delegates to :meth:`_apply_cjk_spacing` so that a query like
        ``"重构"`` becomes ``"重 构"`` and matches the spaced tokens in all
        FTS columns (body_text, title, description, tags, people). Without
        this preprocessing ``MATCH '重构'`` would never hit the indexed
        ``'重 构'`` tokens.
        """
        return self._apply_cjk_spacing(query)

    def _calculate_score(self, fts_rank: float, doc_type: str,
                         modified_time: str) -> float:
        """Composite score: FTS relevance * 0.6 + time decay * 0.2 + type weight * 0.2.

        - FTS relevance: ``bm25()`` returns negative values (lower = more
          relevant); normalize via ``1.0 / (1.0 + abs(fts_rank))`` to map
          any bm25 value into ``(0, 1]`` (closer to 1 = more relevant).
        - Time decay: <=30d = 1.0, <=90d = 0.8, <=180d = 0.5, else 0.3.
          Empty/unparseable ``modified_time`` -> 0.3.
        - Type weight: Meeting Minutes/Decision = 1.0, Requirement = 0.9,
          Review/Review Report = 0.8, Reference = 0.7, else 0.6.
        """
        fts_score = 1.0 / (1.0 + abs(fts_rank))

        # Time decay.
        time_decay = 0.3
        if modified_time:
            try:
                dt = datetime.fromisoformat(modified_time)
                now = datetime.now(timezone.utc)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                days = (now - dt).days
                if days <= 30:
                    time_decay = 1.0
                elif days <= 90:
                    time_decay = 0.8
                elif days <= 180:
                    time_decay = 0.5
                else:
                    time_decay = 0.3
            except (ValueError, TypeError):
                time_decay = 0.3

        # Type weight.
        if doc_type in ("Meeting Minutes", "Decision"):
            type_weight = 1.0
        elif doc_type == "Requirement":
            type_weight = 0.9
        elif doc_type in ("Review", "Review Report"):
            type_weight = 0.8
        elif doc_type == "Reference":
            type_weight = 0.7
        else:
            type_weight = 0.6

        return fts_score * 0.6 + time_decay * 0.2 + type_weight * 0.2

    def search(self, query: str, filters: SearchFilters = None,
               top_n: int = 10, deep_read: bool = True) -> SearchResult:
        """Three-stage progressive RAG search.

        Stage 1 -- FTS5 recall: full-text match on the preprocessed query.
        Stage 2 -- structured filtering (project / doc_type / tags / people
        / date range) applied in Python.
        Stage 2b -- composite scoring + descending sort, capped at ``top_n``.
        Stage 3 -- deep read: assemble matched full content (plus mentions)
        as Agent context when ``deep_read=True``.

        Args:
            query: Natural-language or keyword query.
            filters: Optional :class:`SearchFilters` for structured filtering.
            top_n: Maximum number of matches to return.
            deep_read: When True, read full content of matches and build a
                context string.

        Returns:
            A :class:`SearchResult`. If the FTS5 query fails (syntax error),
            an empty ``SearchResult`` is returned.
        """
        self.ensure_index()
        processed_query = self._preprocess_query(query)

        # ---- Stage 1: FTS5 recall ----
        conn = sqlite3.connect(self.db_path)
        try:
            try:
                rows = conn.execute(
                    """
                    SELECT d.local_path, d.title, d.doc_type, d.project,
                           d.tags, d.people, d.modified_time, d.body_text,
                           bm25(documents_fts) AS rank,
                           snippet(documents_fts, 2, '<mark>', '</mark>',
                                   '...', 20) AS snippet
                    FROM documents_fts
                    JOIN documents d ON d.rowid = documents_fts.rowid
                    WHERE documents_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (processed_query, top_n),
                ).fetchall()
            except sqlite3.OperationalError:
                # FTS5 query syntax error -> empty result.
                return SearchResult(matches=[], context="", total_found=0)
        finally:
            conn.close()

        # ---- Stage 2: structured filtering ----
        filtered = []
        for row in rows:
            (local_path, title, doc_type, project, tags, people,
             modified_time, body_text, rank, snippet_text) = row

            if filters is not None:
                if filters.project is not None and project != filters.project:
                    continue
                if filters.doc_type is not None and doc_type != filters.doc_type:
                    continue
                if filters.tags is not None:
                    doc_tags = [t.strip() for t in (tags or "").split(",")
                                if t.strip()]
                    if not any(t in doc_tags for t in filters.tags):
                        continue
                if filters.people is not None:
                    if filters.people not in (people or ""):
                        continue
                if filters.date_from is not None or filters.date_to is not None:
                    mt = modified_time or ""
                    if filters.date_from is not None and mt < filters.date_from:
                        continue
                    if filters.date_to is not None and mt > filters.date_to:
                        continue

            filtered.append(row)

        # ---- Stage 2b: scoring and sorting ----
        matches = []
        for row in filtered:
            (local_path, title, doc_type, project, tags, people,
             modified_time, body_text, rank, snippet_text) = row
            score = self._calculate_score(rank, doc_type, modified_time)
            matches.append(DocMatch(
                local_path=local_path,
                title=title,
                doc_type=doc_type,
                score=score,
                snippet=snippet_text or "",
            ))

        matches.sort(key=lambda m: m.score, reverse=True)
        matches = matches[:top_n]

        # ---- Stage 3: deep read ----
        context = ""
        if deep_read:
            context = self._deep_read(matches, max_context_chars=8000)

        return SearchResult(matches=matches, context=context,
                            total_found=len(matches))

    def _deep_read(self, matches: list, max_context_chars: int = 8000) -> str:
        """Read full content of matched docs and assemble as Agent context.

        For each match (already sorted by score desc):
        - Read the OKF file from disk; store full content on the match.
        - Parse frontmatter ``mentions`` and store on ``match.related_docs``.
        - Append a structured entry ``=== title (type, score: X.XX) ===``.

        For each mention (depth limit 1), if the referenced file exists and
        has not already been included, append its first 500 chars as
        ``Related: <title>``.

        The assembled context never exceeds ``max_context_chars``: each
        entry is truncated to fit the remaining budget.
        """
        context = ""
        included = set()  # local paths / mention paths already added

        for match in matches:
            if len(context) >= max_context_chars:
                break
            file_path = os.path.join(self.bundle_path, match.local_path)
            if not os.path.exists(file_path):
                continue
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            match.full_content = content

            fm, _ = self._parse_okf(content)
            mentions = fm.get("mentions", [])
            if mentions is None:
                mentions = []
            elif isinstance(mentions, str):
                mentions = [mentions]
            match.related_docs = list(mentions) if mentions else []

            remaining = max_context_chars - len(context)
            if remaining <= 0:
                break
            header = (f"=== {match.title} ({match.doc_type}, "
                      f"score: {match.score:.2f}) ===\n")
            entry = header + content + "\n\n"
            if len(entry) > remaining:
                entry = entry[:remaining]
            context += entry
            included.add(match.local_path)

            # Related docs (depth limit 1).
            for mention in match.related_docs:
                if len(context) >= max_context_chars:
                    break
                rel_path = os.path.join(self.bundle_path, mention.lstrip("/"))
                if not os.path.exists(rel_path):
                    continue
                if mention in included or rel_path in included:
                    continue
                with open(rel_path, "r", encoding="utf-8") as f:
                    rel_content = f.read(500)
                rel_fm, _ = self._parse_okf(rel_content)
                rel_title = rel_fm.get("title", "") or mention
                remaining = max_context_chars - len(context)
                if remaining <= 0:
                    break
                rel_entry = f"Related: {rel_title}\n{rel_content}\n\n"
                if len(rel_entry) > remaining:
                    rel_entry = rel_entry[:remaining]
                context += rel_entry
                included.add(mention)

        return context


# ------------------------------------------------------------------
# Task 11: CLI interface (argparse-based, JSON output for Agent)
# ------------------------------------------------------------------

import argparse
import sys
import json


def _get_bundle_path():
    """Resolve bundle path from config.json or default.

    Reads ``scripts/config.json`` (if present) and returns its
    ``bundle_path`` value; on any read/parse error, falls back to the
    ``../bundle`` directory relative to this script.
    """
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        return config.get("bundle_path", os.path.join(os.path.dirname(__file__), "..", "bundle"))
    except (FileNotFoundError, json.JSONDecodeError):
        return os.path.join(os.path.dirname(__file__), "..", "bundle")


def main():
    """CLI entry point for query_engine.

    Commands:
        search <query>  - Search the knowledge base
        rebuild         - Rebuild the full index
        status          - Show index statistics

    All output is JSON for Agent consumption.
    """
    parser = argparse.ArgumentParser(
        description="Progressive RAG query engine for lark-autocontext"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # search subcommand
    search_parser = subparsers.add_parser("search", help="Search the knowledge base")
    search_parser.add_argument("query", type=str, help="Search query")
    search_parser.add_argument("--project", type=str, default=None, help="Filter by project")
    search_parser.add_argument("--type", type=str, default=None, dest="doc_type", help="Filter by document type")
    search_parser.add_argument("--tags", type=str, default=None, help="Filter by tags (comma-separated)")
    search_parser.add_argument("--people", type=str, default=None, help="Filter by people")
    search_parser.add_argument("--no-deep-read", action="store_true", help="Skip deep read (browse mode)")
    search_parser.add_argument("--top-n", type=int, default=10, help="Max results")
    search_parser.add_argument("--bundle", type=str, default=None, help="Override bundle path")

    # rebuild subcommand
    rebuild_parser = subparsers.add_parser("rebuild", help="Rebuild the full index")
    rebuild_parser.add_argument("--bundle", type=str, default=None, help="Override bundle path")

    # status subcommand
    status_parser = subparsers.add_parser("status", help="Show index statistics")
    status_parser.add_argument("--bundle", type=str, default=None, help="Override bundle path")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    # Resolve bundle path
    bundle_path = args.bundle if hasattr(args, "bundle") and args.bundle else _get_bundle_path()
    engine = QueryEngine(bundle_path)

    if args.command == "search":
        # Build filters
        filters = None
        if any([args.project, args.doc_type, args.tags, args.people]):
            tags_list = args.tags.split(",") if args.tags else None
            filters = SearchFilters(
                project=args.project,
                doc_type=args.doc_type,
                tags=tags_list,
                people=args.people,
            )

        result = engine.search(
            query=args.query,
            filters=filters,
            top_n=args.top_n,
            deep_read=not args.no_deep_read,
        )

        # Convert to JSON-serializable dict
        output = {
            "query": args.query,
            "total_found": result.total_found,
            "matches": [
                {
                    "local_path": m.local_path,
                    "title": m.title,
                    "doc_type": m.doc_type,
                    "score": round(m.score, 4),
                    "snippet": m.snippet,
                    "full_content": m.full_content,
                    "related_docs": m.related_docs,
                }
                for m in result.matches
            ],
            "context": result.context,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))

    elif args.command == "rebuild":
        engine.ensure_index()
        count = engine.rebuild_index()
        output = {
            "status": "success",
            "documents_indexed": count,
            "bundle_path": bundle_path,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))

    elif args.command == "status":
        engine.ensure_index()
        import sqlite3
        conn = sqlite3.connect(engine.db_path)
        try:
            cursor = conn.execute("SELECT COUNT(*) FROM documents")
            doc_count = cursor.fetchone()[0]

            # Get DB file size
            db_size = os.path.getsize(engine.db_path) if os.path.exists(engine.db_path) else 0

            # Get types breakdown
            cursor = conn.execute("SELECT doc_type, COUNT(*) FROM documents GROUP BY doc_type")
            type_breakdown = {row[0]: row[1] for row in cursor.fetchall()}

            # Get projects breakdown
            cursor = conn.execute("SELECT project, COUNT(*) FROM documents GROUP BY project")
            project_breakdown = {row[0]: row[1] for row in cursor.fetchall()}
        finally:
            conn.close()

        output = {
            "status": "ready",
            "db_path": engine.db_path,
            "document_count": doc_count,
            "db_size_bytes": db_size,
            "types": type_breakdown,
            "projects": project_breakdown,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
