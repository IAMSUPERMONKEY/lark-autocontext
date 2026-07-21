"""Unit tests for WikiConnector skeleton and data structures (Task 1).

Covers:
- WikiConnector.__init__ stores space_id / raw_node_token / agent_node_token / identity
- DocInfo dataclass (6 fields)
- DocMeta dataclass (5 fields)
- _run_lark helper calls subprocess.run with lark-cli args
- _run_lark retries on 429 with exponential backoff (1s -> 2s -> 4s, max 3 retries)
- _run_lark raises RuntimeError on non-429 errors
- _run_lark raises RuntimeError after exhausting 429 retries

All subprocess/time interactions are mocked -- no real lark-cli calls are made.
"""
import sys
import os
import dataclasses
from unittest.mock import patch, MagicMock

import pytest

# Make scripts/ importable when running from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))

from wiki_connector import WikiConnector, DocInfo, DocMeta  # noqa: E402


# ---------------------------------------------------------------------------
# WikiConnector.__init__
# ---------------------------------------------------------------------------

def test_wiki_connector_init_stores_all_attrs():
    conn = WikiConnector(
        space_id="space-123",
        raw_node_token="raw-abc",
        agent_node_token="agent-xyz",
        identity="user",
    )
    assert conn.space_id == "space-123"
    assert conn.raw_node_token == "raw-abc"
    assert conn.agent_node_token == "agent-xyz"
    assert conn.identity == "user"


def test_wiki_connector_init_default_identity_is_user():
    conn = WikiConnector("s", "r", "a")
    assert conn.identity == "user"


def test_wiki_connector_init_accepts_tenant_identity():
    conn = WikiConnector("s", "r", "a", identity="tenant")
    assert conn.identity == "tenant"


# ---------------------------------------------------------------------------
# DocInfo dataclass
# ---------------------------------------------------------------------------

def test_doc_info_is_dataclass():
    assert dataclasses.is_dataclass(DocInfo)


def test_doc_info_field_set():
    fields = {f.name for f in dataclasses.fields(DocInfo)}
    assert fields == {
        "node_token", "title", "obj_type",
        "modified_time", "url", "has_children",
    }


def test_doc_info_construction_and_values():
    info = DocInfo(
        node_token="node-1",
        title="Doc Title",
        obj_type="docx",
        modified_time="2026-07-21T10:00:00+08:00",
        url="https://feishu.cn/wiki/node-1",
        has_children=True,
    )
    assert info.node_token == "node-1"
    assert info.title == "Doc Title"
    assert info.obj_type == "docx"
    assert info.modified_time == "2026-07-21T10:00:00+08:00"
    assert info.url == "https://feishu.cn/wiki/node-1"
    assert info.has_children is True


# ---------------------------------------------------------------------------
# DocMeta dataclass
# ---------------------------------------------------------------------------

def test_doc_meta_is_dataclass():
    assert dataclasses.is_dataclass(DocMeta)


def test_doc_meta_field_set():
    fields = {f.name for f in dataclasses.fields(DocMeta)}
    assert fields == {
        "title", "created_time", "modified_time", "creator", "owner",
    }


def test_doc_meta_construction_and_values():
    meta = DocMeta(
        title="Meta Title",
        created_time="2026-01-01T00:00:00+08:00",
        modified_time="2026-07-21T10:00:00+08:00",
        creator="alice",
        owner="bob",
    )
    assert meta.title == "Meta Title"
    assert meta.created_time == "2026-01-01T00:00:00+08:00"
    assert meta.modified_time == "2026-07-21T10:00:00+08:00"
    assert meta.creator == "alice"
    assert meta.owner == "bob"


# ---------------------------------------------------------------------------
# _run_lark: basic subprocess invocation
# ---------------------------------------------------------------------------

def test_run_lark_calls_subprocess_with_lark_cli_prefix():
    conn = WikiConnector("space-1", "raw-1", "agent-1")
    ok = MagicMock()
    ok.returncode = 0
    ok.stdout = '{"ok": true}'
    with patch("subprocess.run", return_value=ok) as mock_run:
        result = conn._run_lark(["wiki", "+list-nodes", "--space-id", "space-1"])

    assert mock_run.call_count == 1
    cmd = mock_run.call_args[0][0]
    # Command must start with the lark-cli executable
    assert cmd[0] == "lark-cli"
    # Every caller-supplied arg must be forwarded
    for arg in ("wiki", "+list-nodes", "--space-id", "space-1"):
        assert arg in cmd
    # Required subprocess kwargs
    kwargs = mock_run.call_args[1]
    assert kwargs.get("capture_output") is True
    assert kwargs.get("text") is True
    assert kwargs.get("timeout") == 30
    # as_json=True (default) -> parsed dict
    assert result == {"ok": True}


def test_run_lark_as_json_false_returns_raw_stdout():
    conn = WikiConnector("s", "r", "a")
    ok = MagicMock()
    ok.returncode = 0
    ok.stdout = "plain text output"
    with patch("subprocess.run", return_value=ok):
        result = conn._run_lark(["wiki", "+list-nodes"], as_json=False)
    assert result == "plain text output"


# ---------------------------------------------------------------------------
# _run_lark: 429 retry with exponential backoff
# ---------------------------------------------------------------------------

def test_run_lark_retries_on_429_then_succeeds():
    """429 twice, then success -> 3 total subprocess calls, 2 sleeps (1s, 2s)."""
    conn = WikiConnector("s", "r", "a")
    err = MagicMock()
    err.returncode = 1
    err.stderr = "error: HTTP 429 Too Many Requests"
    ok = MagicMock()
    ok.returncode = 0
    ok.stdout = '{"ok": true}'

    with patch("subprocess.run", side_effect=[err, err, ok]) as mock_run, \
            patch("time.sleep") as mock_sleep:
        result = conn._run_lark(["wiki", "+list-nodes"])

    assert mock_run.call_count == 3
    assert mock_sleep.call_count == 2
    sleeps = [c.args[0] for c in mock_sleep.call_args_list]
    assert sleeps == [1, 2]
    assert result == {"ok": True}


def test_run_lark_exhausts_429_retries_then_raises():
    """All attempts return 429 -> 4 calls (1 initial + 3 retries), sleeps 1,2,4."""
    conn = WikiConnector("s", "r", "a")
    err = MagicMock()
    err.returncode = 1
    err.stderr = "HTTP 429 rate limited"

    with patch("subprocess.run", return_value=err) as mock_run, \
            patch("time.sleep") as mock_sleep:
        with pytest.raises(RuntimeError):
            conn._run_lark(["wiki", "+list-nodes"])

    assert mock_run.call_count == 4
    assert mock_sleep.call_count == 3
    sleeps = [c.args[0] for c in mock_sleep.call_args_list]
    assert sleeps == [1, 2, 4]


# ---------------------------------------------------------------------------
# _run_lark: non-429 errors
# ---------------------------------------------------------------------------

def test_run_lark_raises_on_non_429_error_without_retry():
    conn = WikiConnector("s", "r", "a")
    err = MagicMock()
    err.returncode = 1
    err.stderr = "permission denied"

    with patch("subprocess.run", return_value=err) as mock_run, \
            patch("time.sleep") as mock_sleep:
        with pytest.raises(RuntimeError) as exc_info:
            conn._run_lark(["wiki", "+list-nodes"])

    # No retry should happen for non-429 errors
    assert mock_run.call_count == 1
    assert mock_sleep.call_count == 0
    assert "permission denied" in str(exc_info.value)
