"""Unit tests for scanner.py wiki mode integration (Task 12).

Covers the new wiki-mode branches added to ``scan_single_doc`` /
``scan_batch`` plus the ``_get_wiki_connector`` factory, while verifying
that the legacy folder-mode behaviour is preserved unchanged.

All WikiConnector / LarkCLI interactions are mocked -- no real lark-cli
calls are made.
"""
import sys
import os
from unittest.mock import patch, MagicMock

import pytest

# Make scripts/ importable when running from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))

from wiki_connector import DocInfo, DocMeta  # noqa: E402
import scanner  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_wiki_connector():
    """Build a MagicMock standing in for a WikiConnector.

    Defaults:
      - fetch_doc_content(node_token) -> "Mock content for <node_token>"
      - fetch_doc_meta(node_token)    -> a DocMeta with title/dates
      - list_raw_docs()               -> [] (override per test)
    """
    conn = MagicMock()
    conn.agent_node_token = "agent_root"
    conn.space_id = "space-fake"
    conn.raw_node_token = "raw_root"

    def _content(node_token):
        return f"Mock content for {node_token}"

    def _meta(node_token):
        return DocMeta(
            title=f"Title of {node_token}",
            created_time="2026-01-01T00:00:00+08:00",
            modified_time="2026-07-21T10:00:00+08:00",
            creator="alice",
            owner="bob",
        )

    conn.fetch_doc_content.side_effect = _content
    conn.fetch_doc_meta.side_effect = _meta
    conn.list_raw_docs.return_value = []
    return conn


def _make_docinfo(node_token, title, obj_type="docx",
                  modified_time="2026-07-21T10:00:00+08:00",
                  has_children=False):
    return DocInfo(
        node_token=node_token,
        title=title,
        obj_type=obj_type,
        modified_time=modified_time,
        url=f"https://feishu.cn/wiki/{node_token}",
        has_children=has_children,
    )


# ---------------------------------------------------------------------------
# scan_single_doc -- wiki mode
# ---------------------------------------------------------------------------

def test_scan_single_doc_wiki_mode():
    """Wiki URL is parsed for node_token; content/title come from connector."""
    conn = _make_mock_wiki_connector()
    result = scanner.scan_single_doc(
        "https://feishu.cn/wiki/test_node_123",
        use_wiki=True,
        wiki_connector=conn,
    )
    assert result["source_type"] == "wiki_doc"
    assert result["node_token"] == "test_node_123"
    assert result["doc_token"] == "test_node_123"
    assert result["content"] == "Mock content for test_node_123"
    assert result["title"] == "Title of test_node_123"
    assert result["url"] == "https://feishu.cn/wiki/test_node_123"
    assert "fetched_at" in result
    assert result["last_modified"] == "2026-07-21T10:00:00+08:00"
    conn.fetch_doc_content.assert_called_once_with("test_node_123")
    conn.fetch_doc_meta.assert_called_once_with("test_node_123")


def test_scan_single_doc_wiki_token_only():
    """A bare node_token (no URL prefix) is used as-is in wiki mode."""
    conn = _make_mock_wiki_connector()
    result = scanner.scan_single_doc(
        "test_node_123",
        use_wiki=True,
        wiki_connector=conn,
    )
    assert result["source_type"] == "wiki_doc"
    assert result["node_token"] == "test_node_123"
    assert result["content"] == "Mock content for test_node_123"
    assert result["title"] == "Title of test_node_123"
    conn.fetch_doc_content.assert_called_once_with("test_node_123")


def test_scan_single_doc_wiki_no_connector():
    """When no connector is supplied and config is absent, return an error."""
    with patch.object(scanner, '_get_wiki_connector', return_value=None):
        result = scanner.scan_single_doc(
            "https://feishu.cn/wiki/abc",
            use_wiki=True,
            wiki_connector=None,
        )
    assert "error" in result
    assert "not configured" in result["error"].lower() or "wiki" in result["error"].lower()


def test_scan_single_doc_wiki_error():
    """A fetch exception is captured as an error dict, not raised."""
    conn = _make_mock_wiki_connector()
    conn.fetch_doc_content.side_effect = RuntimeError("boom")
    result = scanner.scan_single_doc(
        "https://feishu.cn/wiki/abc",
        use_wiki=True,
        wiki_connector=conn,
    )
    assert "error" in result
    assert "boom" in result["error"]
    assert "hint" in result


# ---------------------------------------------------------------------------
# scan_batch -- wiki mode
# ---------------------------------------------------------------------------

def test_scan_batch_wiki_mode():
    """Only docx nodes are processed; sheets/files are skipped."""
    conn = _make_mock_wiki_connector()
    conn.list_raw_docs.return_value = [
        _make_docinfo("n1", "Doc One", obj_type="docx"),
        _make_docinfo("n2", "Sheet Two", obj_type="sheet"),
        _make_docinfo("n3", "Doc Three", obj_type="docx"),
    ]
    result = scanner.scan_batch(use_wiki=True, wiki_connector=conn)

    assert "error" not in result
    assert result["total_documents"] == 2
    docs = result["documents"]
    titles = [d["title"] for d in docs]
    assert titles == ["Doc One", "Doc Three"]
    # Each doc carries the wiki source_type and connector-provided content.
    for d in docs:
        assert d["source_type"] == "wiki_doc"
        assert d["source_name"] == "wiki_raw"
        assert d["content"].startswith("Mock content for ")
        assert d["node_token"] == d["doc_token"]
    # The sheet node was never fetched.
    conn.fetch_doc_content.assert_any_call("n1")
    conn.fetch_doc_content.assert_any_call("n3")
    with pytest.raises(AssertionError):
        conn.fetch_doc_content.assert_any_call("n2")


def test_scan_batch_wiki_no_connector():
    """Batch mode returns an error when wiki is not configured."""
    with patch.object(scanner, '_get_wiki_connector', return_value=None):
        result = scanner.scan_batch(use_wiki=True, wiki_connector=None)
    assert "error" in result
    assert "not configured" in result["error"].lower() or "wiki" in result["error"].lower()


# ---------------------------------------------------------------------------
# Legacy folder-mode preservation
# ---------------------------------------------------------------------------

def test_scan_single_doc_legacy_mode_unchanged():
    """Without use_wiki, scan_single_doc still uses LarkCLI (folder mode)."""
    with patch('scanner.LarkCLI') as mock_cli_cls:
        instance = mock_cli_cls.return_value
        instance.fetch_doc.return_value = "# Legacy Content"
        instance.fetch_doc_title.return_value = "Legacy Title"
        instance.fetch_doc_metadata.return_value = {
            "edited_time": "2026-07-21T12:00:00+08:00"
        }
        result = scanner.scan_single_doc("https://feishu.cn/docx/test123")

    assert result["source_type"] == "doc"
    assert result["doc_token"] == "test123"
    assert result["title"] == "Legacy Title"
    assert result["content"] == "# Legacy Content"
    assert result["url"] == "https://feishu.cn/docx/test123"
    assert result["last_modified"] == "2026-07-21T12:00:00+08:00"
    # LarkCLI was instantiated and used (not the wiki connector).
    mock_cli_cls.assert_called_once_with()
    instance.fetch_doc.assert_called_once_with("test123")


# ---------------------------------------------------------------------------
# _get_wiki_connector factory
# ---------------------------------------------------------------------------

def test_get_wiki_connector_not_configured():
    """With no config.json available, the factory returns None."""
    with patch('builtins.open', side_effect=FileNotFoundError("no config")):
        result = scanner._get_wiki_connector()
    assert result is None
