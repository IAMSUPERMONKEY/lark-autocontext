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
import json
import tempfile
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


# ===========================================================================
# Task 2: read operations (list_raw_docs, fetch_doc_content, fetch_doc_meta,
#         list_wiki_subtree, list_agent_docs)
#
# Every test mocks subprocess.run (the lowest layer _run_lark delegates to)
# so that NO real lark-cli calls are ever made. _run_lark itself runs for
# real, which also exercises its 429-retry path on the read operations.
# ===========================================================================

def _ok(stdout_str):
    """Build a successful subprocess.run return value mock."""
    m = MagicMock()
    m.returncode = 0
    m.stdout = stdout_str
    m.stderr = ""
    return m


# ---------------------------------------------------------------------------
# list_raw_docs
# ---------------------------------------------------------------------------

def test_list_raw_docs_basic():
    """list_raw_docs returns DocInfo only for direct children of raw_node_token."""
    conn = WikiConnector("space-1", "raw-root", "agent-root")
    nodes = [
        {"node_token": "n1", "obj_token": "o1", "title": "Doc 1", "obj_type": "docx",
         "obj_edit_time": "1700000000", "parent_node_token": "raw-root",
         "has_child": False},
        {"node_token": "n2", "obj_token": "o2", "title": "Doc 2", "obj_type": "sheet",
         "obj_edit_time": "1700000001", "parent_node_token": "raw-root",
         "has_child": True},
        {"node_token": "n3", "obj_token": "o3", "title": "Doc 3", "obj_type": "docx",
         "obj_edit_time": "1700000002", "parent_node_token": "other-parent",
         "has_child": False},
    ]
    payload = json.dumps({"data": {"nodes": nodes}})

    with patch("subprocess.run", return_value=_ok(payload)) as mock_run:
        result = conn.list_raw_docs()

    # Exactly one lark-cli call (wiki +node-list), no real subprocess.
    assert mock_run.call_count == 1
    assert len(result) == 2
    assert all(isinstance(d, DocInfo) for d in result)
    assert [d.node_token for d in result] == ["n1", "n2"]

    assert result[0].title == "Doc 1"
    assert result[0].obj_type == "docx"
    assert result[0].modified_time == "1700000000"
    assert result[0].url == "https://feishu.cn/wiki/n1"
    assert result[0].has_children is False

    assert result[1].title == "Doc 2"
    assert result[1].obj_type == "sheet"
    assert result[1].has_children is True
    assert result[1].url == "https://feishu.cn/wiki/n2"


def test_list_raw_docs_with_since_filter():
    """list_raw_docs(since=...) keeps only nodes with obj_edit_time >= since."""
    conn = WikiConnector("space-1", "raw-root", "agent-root")
    nodes = [
        {"node_token": "old", "obj_token": "o-old", "title": "Old", "obj_type": "docx",
         "obj_edit_time": "1700000000", "parent_node_token": "raw-root",
         "has_child": False},
        {"node_token": "new", "obj_token": "o-new", "title": "New", "obj_type": "docx",
         "obj_edit_time": "1700000020", "parent_node_token": "raw-root",
         "has_child": False},
    ]
    payload = json.dumps({"data": {"nodes": nodes}})
    # 2023-11-14T22:13:30+00:00 == Unix 1700000010
    with patch("subprocess.run", return_value=_ok(payload)):
        result = conn.list_raw_docs(since="2023-11-14T22:13:30+00:00")

    assert len(result) == 1
    assert result[0].node_token == "new"


# ---------------------------------------------------------------------------
# fetch_doc_content
# ---------------------------------------------------------------------------

def test_fetch_doc_content():
    """fetch_doc_content resolves obj_token, fetches markdown, cleans it."""
    conn = WikiConnector("space-1", "raw-root", "agent-root")
    nodes = [
        {"node_token": "target", "obj_token": "obj-123", "title": "Target",
         "obj_type": "docx", "obj_edit_time": "1700000000",
         "parent_node_token": "raw-root", "has_child": False},
    ]
    node_list_payload = json.dumps({"data": {"nodes": nodes}})
    raw_content = "# Raw\n\n<p>hello</p>"
    doc_payload = json.dumps({"data": {"document": {"content": raw_content}}})

    def fake_run(cmd, *args, **kwargs):
        # lark-cli shortcut elements carry a '+' prefix (e.g. '+node-get'), so
        # match by substring across the whole joined command string.
        cmd_str = " ".join(cmd)
        if "node-get" in cmd_str:
            return _ok(json.dumps({"data": {"obj_token": "obj-123"}}))
        if "fetch" in cmd_str:
            return _ok(doc_payload)
        return _ok("{}")

    with patch("subprocess.run", side_effect=fake_run) as mock_run, \
            patch("wiki_connector.clean_feishu_content",
                  return_value="CLEANED_OUTPUT") as mock_clean:
        result = conn.fetch_doc_content("target")

    # Two lark-cli calls: node-get (resolve obj_token) + docs fetch (content).
    assert mock_run.call_count == 2
    mock_clean.assert_called_once_with(raw_content)
    assert result == "CLEANED_OUTPUT"


# ---------------------------------------------------------------------------
# fetch_doc_meta
# ---------------------------------------------------------------------------

def test_fetch_doc_meta():
    """fetch_doc_meta resolves obj_token, inspects title, fetches timestamps."""
    conn = WikiConnector("space-1", "raw-root", "agent-root")
    nodes = [
        {"node_token": "meta-node", "obj_token": "obj-meta", "title": "Meta Node",
         "obj_type": "docx", "obj_edit_time": "1700000000",
         "parent_node_token": "raw-root", "has_child": False},
    ]
    node_list_payload = json.dumps({"data": {"nodes": nodes}})
    inspect_payload = json.dumps({"data": {"title": "Meta Doc Title"}})
    detail_payload = json.dumps({"data": {"document": {
        "title": "Meta Doc Title",
        "created_time_iso": "2026-01-01T00:00:00+08:00",
        "last_modified_time_iso": "2026-07-21T10:00:00+08:00",
        "creator": "alice",
        "owner": "bob",
    }}})

    def fake_run(cmd, *args, **kwargs):
        # lark-cli shortcut elements carry a '+' prefix (e.g. '+node-get'), so
        # match by substring across the whole joined command string.
        cmd_str = " ".join(cmd)
        if "node-get" in cmd_str:
            return _ok(json.dumps({"data": {"obj_token": "obj-meta"}}))
        if "inspect" in cmd_str:
            return _ok(inspect_payload)
        if "fetch" in cmd_str:
            return _ok(detail_payload)
        return _ok("{}")

    with patch("subprocess.run", side_effect=fake_run) as mock_run:
        meta = conn.fetch_doc_meta("meta-node")

    # Three lark-cli calls: node-get + drive inspect + docs fetch --detail full.
    assert mock_run.call_count == 3
    assert isinstance(meta, DocMeta)
    assert meta.title == "Meta Doc Title"
    assert meta.created_time == "2026-01-01T00:00:00+08:00"
    assert meta.modified_time == "2026-07-21T10:00:00+08:00"
    assert meta.creator == "alice"
    assert meta.owner == "bob"


# ---------------------------------------------------------------------------
# list_wiki_subtree
# ---------------------------------------------------------------------------

def test_list_wiki_subtree():
    """list_wiki_subtree returns ALL descendants via parent_node_token traversal."""
    conn = WikiConnector("space-1", "raw-root", "agent-root")
    nodes = [
        {"node_token": "subroot", "obj_token": "o-sub", "title": "Subroot",
         "obj_type": "docx", "obj_edit_time": "1700000000",
         "parent_node_token": "", "has_child": True},
        {"node_token": "child1", "obj_token": "o-c1", "title": "Child 1",
         "obj_type": "docx", "obj_edit_time": "1700000001",
         "parent_node_token": "subroot", "has_child": True},
        {"node_token": "grandchild1", "obj_token": "o-gc1", "title": "GC 1",
         "obj_type": "docx", "obj_edit_time": "1700000002",
         "parent_node_token": "child1", "has_child": False},
        {"node_token": "child2", "obj_token": "o-c2", "title": "Child 2",
         "obj_type": "docx", "obj_edit_time": "1700000003",
         "parent_node_token": "subroot", "has_child": False},
        {"node_token": "other", "obj_token": "o-other", "title": "Other",
         "obj_type": "docx", "obj_edit_time": "1700000004",
         "parent_node_token": "different", "has_child": False},
    ]
    payload = json.dumps({"data": {"nodes": nodes}})

    with patch("subprocess.run", return_value=_ok(payload)):
        result = conn.list_wiki_subtree("subroot")

    tokens = sorted(d.node_token for d in result)
    # subroot itself is NOT included; only its descendants (child1, child2,
    # and the deeply nested grandchild1). "other" is unrelated.
    assert tokens == ["child1", "child2", "grandchild1"]
    assert all(isinstance(d, DocInfo) for d in result)
    assert all(d.url == f"https://feishu.cn/wiki/{d.node_token}" for d in result)


# ---------------------------------------------------------------------------
# list_agent_docs
# ---------------------------------------------------------------------------

def test_list_agent_docs():
    """list_agent_docs filters direct children of agent_node_token."""
    conn = WikiConnector("space-1", "raw-root", "agent-root")
    nodes = [
        {"node_token": "a1", "obj_token": "oa1", "title": "Agent Doc 1",
         "obj_type": "docx", "obj_edit_time": "1700000000",
         "parent_node_token": "agent-root", "has_child": False},
        {"node_token": "a2", "obj_token": "oa2", "title": "Agent Doc 2",
         "obj_type": "docx", "obj_edit_time": "1700000001",
         "parent_node_token": "agent-root", "has_child": False},
        {"node_token": "r1", "obj_token": "or1", "title": "Raw Doc",
         "obj_type": "docx", "obj_edit_time": "1700000002",
         "parent_node_token": "raw-root", "has_child": False},
    ]
    payload = json.dumps({"data": {"nodes": nodes}})

    with patch("subprocess.run", return_value=_ok(payload)):
        result = conn.list_agent_docs()

    assert len(result) == 2
    assert [d.node_token for d in result] == ["a1", "a2"]
    assert all(d.url == f"https://feishu.cn/wiki/{d.node_token}" for d in result)


# ---------------------------------------------------------------------------
# 429 retry flows through to read operations
# ---------------------------------------------------------------------------

def test_429_retry_in_read_op():
    """list_raw_docs benefits from _run_lark's 429 exponential-backoff retry."""
    conn = WikiConnector("space-1", "raw-root", "agent-root")
    err = MagicMock()
    err.returncode = 1
    err.stderr = "error: HTTP 429 Too Many Requests"
    nodes = [
        {"node_token": "n1", "obj_token": "o1", "title": "Doc 1", "obj_type": "docx",
         "obj_edit_time": "1700000000", "parent_node_token": "raw-root",
         "has_child": False},
    ]
    ok = _ok(json.dumps({"data": {"nodes": nodes}}))

    with patch("subprocess.run", side_effect=[err, err, ok]) as mock_run, \
            patch("time.sleep") as mock_sleep:
        result = conn.list_raw_docs()

    # 1 initial attempt + 2 retries (429 twice, then success).
    assert mock_run.call_count == 3
    assert mock_sleep.call_count == 2
    assert [c.args[0] for c in mock_sleep.call_args_list] == [1, 2]
    assert len(result) == 1
    assert result[0].node_token == "n1"


# ===========================================================================
# Task 3: write operations (create_doc, update_doc, upload_attachment,
#         delete_doc, move_doc, check_doc_changed)
#
# Every test mocks either _run_lark (for the lark-cli delegating methods) or
# _fetch_all_nodes (for check_doc_changed). subprocess.run is NEVER invoked,
# so no real lark-cli calls are made. Temp files are tracked via a wrapper
# around tempfile.NamedTemporaryFile so cleanup can be asserted.
# ===========================================================================

def _tracking_temp_factory():
    """Return (wrapper, paths_list).

    The wrapper delegates to the real tempfile.NamedTemporaryFile so the
    implementation under test gets a genuine on-disk file (writeable, with a
    real .name path). Every created path is recorded in paths_list so the
    test can later assert os.path.exists(path) is False (i.e. the finally
    block cleaned up).
    """
    real = tempfile.NamedTemporaryFile
    paths = []

    def wrapper(*args, **kwargs):
        f = real(*args, **kwargs)
        paths.append(f.name)
        return f

    return wrapper, paths


# ---------------------------------------------------------------------------
# create_doc
# ---------------------------------------------------------------------------

def test_create_doc_returns_node_token():
    """create_doc returns node_token parsed from a JSON lark-cli response.

    Two-step process: 1) wiki +node-create (no --file), 2) docs +update.
    """
    import glob
    conn = WikiConnector("space-1", "raw-root", "agent-root")
    create_response = json.dumps({"data": {"node_token": "new-node-abc"}})
    nodes_payload = json.dumps({"data": {"nodes": [
        {"node_token": "new-node-abc", "obj_token": "obj-abc",
         "title": "New Doc", "obj_type": "docx", "obj_edit_time": "1700000000",
         "parent_node_token": "agent-root", "has_child": False}
    ]}})

    def fake_run_lark(args, as_json=True, retries=3):
        joined = " ".join(args)
        if "+node-get" in joined:
            return json.dumps({"data": {"obj_token": "obj-abc"}})
        if "+node-create" in joined:
            return create_response
        if "+update" in joined:
            return "ok"
        return ""

    with patch.object(conn, "_run_lark", side_effect=fake_run_lark) as mock_run:
        result = conn.create_doc("parent-1", "New Doc", "# Hello world")

    assert result == "new-node-abc"
    # First call: wiki +node-create (no --file flag).
    first_call_args = mock_run.call_args_list[0][0][0]
    assert "wiki" in first_call_args
    assert "+node-create" in first_call_args
    assert "--file" not in first_call_args
    assert "--title" in first_call_args
    # Second call: wiki +node-get (resolve obj_token for step 2).
    second_call_args = mock_run.call_args_list[1][0][0]
    assert "+node-get" in second_call_args
    # Third call: docs +update (write content).
    third_call_args = mock_run.call_args_list[2][0][0]
    assert "+update" in third_call_args
    # Temp file cleaned up (no .lark_tmp_*.md files left behind).
    assert glob.glob(".lark_tmp_*.md") == []


def test_create_doc_plain_text_response():
    """create_doc falls back to regex when the response is plain text."""
    import glob
    conn = WikiConnector("space-1", "raw-root", "agent-root")

    def fake_run_lark(args, as_json=True, retries=3):
        joined = " ".join(args)
        if "node-list" in joined:
            return json.dumps({"data": {"nodes": []}})
        if "+node-create" in joined:
            return "node_token: abc123"
        return ""

    with patch.object(conn, "_run_lark", side_effect=fake_run_lark):
        result = conn.create_doc("parent-1", "Plain Doc", "# Body")

    assert result == "abc123"
    assert glob.glob(".lark_tmp_*.md") == []


# ---------------------------------------------------------------------------
# update_doc
# ---------------------------------------------------------------------------

def test_update_doc():
    """update_doc resolves obj_token then calls docs +update with a temp file."""
    import glob
    conn = WikiConnector("space-1", "raw-root", "agent-root")
    nodes = [
        {"node_token": "node-1", "obj_token": "obj-123", "title": "Doc",
         "obj_type": "docx", "obj_edit_time": "1700000000",
         "parent_node_token": "raw-root", "has_child": False},
    ]
    node_list_payload = json.dumps({"data": {"nodes": nodes}})

    def fake_run_lark(args, as_json=True, retries=3):
        joined = " ".join(args)
        if "+node-get" in joined:
            return json.dumps({"data": {"obj_token": "obj-123"}})
        if "+update" in joined:
            return "ok"
        return ""

    with patch.object(conn, "_run_lark", side_effect=fake_run_lark) as mock_run:
        result = conn.update_doc("node-1", "# Updated content")

    assert result is None
    # Two lark-cli calls: node-get (resolve obj_token) + docs update.
    assert mock_run.call_count == 2
    update_args = mock_run.call_args_list[1][0][0]
    expected_prefix = [
        "docs", "+update", "--doc", "obj-123",
        "--doc-format", "markdown", "--command", "overwrite",
    ]
    assert update_args[:len(expected_prefix)] == expected_prefix
    assert "--content" in update_args
    assert any(str(a).startswith("@") for a in update_args)
    # Temp file cleaned up.
    assert glob.glob(".lark_tmp_*.md") == []


# ---------------------------------------------------------------------------
# upload_attachment
# ---------------------------------------------------------------------------

def test_upload_attachment():
    """upload_attachment returns file_token parsed from a JSON response."""
    import glob
    conn = WikiConnector("space-1", "raw-root", "agent-root")
    response = json.dumps({"data": {"file_token": "file-abc"}})
    file_bytes = b"%PDF-1.4 fake binary content"

    with patch.object(conn, "_run_lark", return_value=response) as mock_run:
        result = conn.upload_attachment("parent-1", "report.pdf", file_bytes)

    assert result == "file-abc"
    call_args = mock_run.call_args[0][0]
    expected_prefix = [
        "drive", "+upload", "--wiki-token", "parent-1",
        "--file", "--name", "report.pdf",
    ]
    # Check all expected tokens are present.
    for token in expected_prefix:
        assert token in call_args
    # Verify the temp file was cleaned up.
    assert glob.glob(".lark_tmp_report.pdf") == []


# ---------------------------------------------------------------------------
# delete_doc
# ---------------------------------------------------------------------------

def test_delete_doc():
    """delete_doc calls wiki +node-delete with space_id, node_token, obj-type, and --yes."""
    conn = WikiConnector("space-1", "raw-root", "agent-root")
    with patch.object(conn, "_run_lark", return_value="ok") as mock_run:
        result = conn.delete_doc("node-1")
    assert result is None
    call_args = mock_run.call_args[0][0]
    assert call_args == [
        "wiki", "+node-delete", "--space-id", "space-1",
        "--node-token", "node-1", "--obj-type", "wiki", "--yes",
    ]


# ---------------------------------------------------------------------------
# move_doc
# ---------------------------------------------------------------------------

def test_move_doc():
    """move_doc calls wiki +move-node with space_id, node_token and target parent."""
    conn = WikiConnector("space-1", "raw-root", "agent-root")
    with patch.object(conn, "_run_lark", return_value="ok") as mock_run:
        result = conn.move_doc("node-1", "new-parent")
    assert result is None
    call_args = mock_run.call_args[0][0]
    assert call_args == [
        "wiki", "+move-node", "--space-id", "space-1",
        "--node-token", "node-1", "--target-parent-token", "new-parent",
    ]


# ---------------------------------------------------------------------------
# check_doc_changed
# ---------------------------------------------------------------------------

def test_check_doc_changed_true():
    """check_doc_changed returns True when obj_edit_time > last_known_time."""
    conn = WikiConnector("space-1", "raw-root", "agent-root")
    nodes = [
        {"node_token": "node-1", "obj_edit_time": "1700000002"},
    ]
    with patch.object(conn, "_fetch_all_nodes", return_value=nodes):
        result = conn.check_doc_changed("node-1", "1700000001")
    assert result is True


def test_check_doc_changed_false():
    """check_doc_changed returns False when obj_edit_time <= last_known_time."""
    conn = WikiConnector("space-1", "raw-root", "agent-root")
    nodes = [
        {"node_token": "node-1", "obj_edit_time": "1700000002"},
    ]
    with patch.object(conn, "_fetch_all_nodes", return_value=nodes):
        result = conn.check_doc_changed("node-1", "1700000003")
    assert result is False


def test_check_doc_changed_node_not_found():
    """check_doc_changed returns False when the node is missing from the space."""
    conn = WikiConnector("space-1", "raw-root", "agent-root")
    nodes = [
        {"node_token": "other-node", "obj_edit_time": "1700000002"},
    ]
    with patch.object(conn, "_fetch_all_nodes", return_value=nodes):
        result = conn.check_doc_changed("missing-node", "1700000001")
    assert result is False


# ---------------------------------------------------------------------------
# Temp file cleanup on error
# ---------------------------------------------------------------------------

def test_temp_file_cleanup_on_error():
    """create_doc raises when _run_lark fails during node creation (step 1).

    In the two-step process, if step 1 (node creation) fails, no temp file
    is created yet. The error propagates immediately.
    """
    import glob
    conn = WikiConnector("space-1", "raw-root", "agent-root")

    with patch.object(conn, "_run_lark", side_effect=RuntimeError("lark-cli failed")):
        with pytest.raises(RuntimeError):
            conn.create_doc("parent-1", "Doc", "# content")

    # Step 1 failed before temp file creation, so no temp file to clean up.
    assert glob.glob(".lark_tmp_*.md") == []
