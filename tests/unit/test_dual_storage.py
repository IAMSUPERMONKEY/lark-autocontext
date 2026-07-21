"""Unit tests for DualStorage: sync_state.json management (Task 5) and
sync_to_feishu push flow (Task 6).

Covers the state-management slice of :mod:`dual_storage`:

- ``SyncDirection`` enum (4 values).
- ``SyncState`` dataclass ``to_dict`` / ``from_dict`` round-trip.
- ``DualStorage.load_state`` -- empty / existing / corrupted-file recovery.
- ``DualStorage.save_state`` -- atomic write (correct content, no ``.tmp``).
- ``DualStorage._compute_hash`` -- SHA256 with ``sha256:`` prefix.
- ``DualStorage.get_doc_state`` -- lookup by node_token / local_path / missing.
- ``DualStorage.update_doc_state`` -- insert + update.
- ``DualStorage.remove_doc_state`` -- entry removal.

And the Task 6 push flow (local -> Feishu):

- ``SyncResult`` dataclass fields.
- ``DualStorage.sync_to_feishu`` -- new doc create / existing doc update /
  failure leaves sync_state untouched / missing wiki_connector raises.

Task 5 tests are pure file/JSON operations; Task 6 tests mock the
WikiConnector. Each test uses the ``tmp_path`` pytest fixture for an isolated
temp directory.
"""
import sys
import os
import json
import hashlib
from unittest.mock import MagicMock
from dataclasses import fields

import pytest

# Make scripts/ importable when running from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))


# Sample OKF Markdown content used by the Task 6 sync tests.
SAMPLE_OKF = """---
type: Meeting Minutes
title: "测试会议"
description: 测试用OKF文档
timestamp: 2026-06-20T14:30:00+08:00
project: demo
tags: [测试]
people: [张三]
---

# Summary

这是测试内容。
"""


def _make_mock_wiki_connector(create_return: str = "new_node_123",
                              create_raises: Exception = None) -> MagicMock:
    """Build a MagicMock standing in for a WikiConnector.

    Configures ``agent_node_token`` and stubs ``create_doc`` / ``update_doc``
    with the requested behavior. Tests assert on the call args of these mocks
    to verify the sync flow dispatched to the right write operation.
    """
    mock = MagicMock()
    mock.agent_node_token = "agent_root_token"
    if create_raises is not None:
        mock.create_doc.side_effect = create_raises
    else:
        mock.create_doc.return_value = create_return
    # update_doc returns None by default (real signature returns None).
    mock.update_doc.return_value = None
    return mock


# ---------------------------------------------------------------------------
# SyncDirection enum
# ---------------------------------------------------------------------------

def test_sync_direction_enum():
    """All 4 SyncDirection values exist with correct string values."""
    from dual_storage import SyncDirection
    assert SyncDirection.IN_SYNC.value == "in_sync"
    assert SyncDirection.LOCAL_NEWER.value == "local_newer"
    assert SyncDirection.FEISHU_NEWER.value == "feishu_newer"
    assert SyncDirection.CONFLICT.value == "conflict"
    # Exactly 4 members.
    assert len(list(SyncDirection)) == 4


# ---------------------------------------------------------------------------
# SyncState dataclass round-trip
# ---------------------------------------------------------------------------

def test_sync_state_to_dict_from_dict_roundtrip():
    """to_dict -> from_dict yields an equal SyncState."""
    from dual_storage import SyncState
    original = SyncState(
        local_path="projects/demo/2026-06-20-test.md",
        feishu_node_token="abc123",
        feishu_url="https://feishu.cn/docx/abc123",
        local_content_hash="sha256:deadbeef",
        feishu_modified_time="2026-06-20T15:00:00+08:00",
        local_modified_time="2026-06-20T15:05:00+08:00",
        sync_direction="in_sync",
        last_sync_at="2026-06-20T15:10:00+08:00",
    )
    d = original.to_dict()
    assert isinstance(d, dict)
    restored = SyncState.from_dict(d)
    assert restored == original


# ---------------------------------------------------------------------------
# DualStorage.load_state
# ---------------------------------------------------------------------------

def test_load_state_empty(tmp_path):
    """No sync_state.json -> returns {"docs": {}}."""
    from dual_storage import DualStorage
    ds = DualStorage(str(tmp_path))
    assert ds.load_state() == {"docs": {}}


def test_load_state_existing(tmp_path):
    """Valid sync_state.json with a doc entry -> load returns it verbatim."""
    from dual_storage import DualStorage
    state = {
        "docs": {
            "abc123": {
                "local_path": "projects/demo/x.md",
                "feishu_node_token": "abc123",
                "feishu_url": "https://feishu.cn/docx/abc123",
                "local_content_hash": "sha256:hash",
                "feishu_modified_time": "2026-06-20T15:00:00+08:00",
                "local_modified_time": "2026-06-20T15:05:00+08:00",
                "sync_direction": "in_sync",
                "last_sync_at": "2026-06-20T15:10:00+08:00",
            }
        }
    }
    (tmp_path / ".sync_state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ds = DualStorage(str(tmp_path))
    loaded = ds.load_state()
    assert loaded == state
    assert "abc123" in loaded["docs"]
    assert loaded["docs"]["abc123"]["local_path"] == "projects/demo/x.md"


def test_load_state_corrupted(tmp_path):
    """Corrupted JSON -> returns {"docs": {}} without raising."""
    from dual_storage import DualStorage
    (tmp_path / ".sync_state.json").write_text(
        "{not valid json at all", encoding="utf-8"
    )
    ds = DualStorage(str(tmp_path))
    # Must not raise.
    loaded = ds.load_state()
    assert loaded == {"docs": {}}


# ---------------------------------------------------------------------------
# DualStorage.save_state
# ---------------------------------------------------------------------------

def test_save_state_atomic(tmp_path):
    """save_state writes correct JSON and leaves no .tmp file behind."""
    from dual_storage import DualStorage
    ds = DualStorage(str(tmp_path))
    state = {"docs": {"k1": {"local_path": "a.md", "sync_direction": "in_sync"}}}
    ds.save_state(state)

    state_file = tmp_path / ".sync_state.json"
    assert state_file.exists()
    # Content is valid JSON and round-trips.
    loaded = json.loads(state_file.read_text(encoding="utf-8"))
    assert loaded == state
    # No .tmp file left behind.
    tmp_file = tmp_path / ".sync_state.json.tmp"
    assert not tmp_file.exists()


# ---------------------------------------------------------------------------
# DualStorage._compute_hash
# ---------------------------------------------------------------------------

def test_compute_hash():
    """_compute_hash returns 'sha256:<hex>' matching hashlib of the input."""
    from dual_storage import DualStorage
    ds = DualStorage.__new__(DualStorage)  # bypass __init__ (no fs needed)
    expected = "sha256:" + hashlib.sha256(b"hello").hexdigest()
    assert ds._compute_hash("hello") == expected
    # Determinism.
    assert ds._compute_hash("hello") == ds._compute_hash("hello")
    # Different input -> different hash.
    assert ds._compute_hash("hello") != ds._compute_hash("world")


# ---------------------------------------------------------------------------
# DualStorage.get_doc_state
# ---------------------------------------------------------------------------

def test_get_doc_state_found(tmp_path):
    """get_doc_state returns a SyncState for a known key (by node_token)."""
    from dual_storage import DualStorage
    state = {
        "docs": {
            "abc123": {
                "local_path": "projects/demo/x.md",
                "feishu_node_token": "abc123",
                "feishu_url": "https://feishu.cn/docx/abc123",
                "local_content_hash": "sha256:hash",
                "feishu_modified_time": "2026-06-20T15:00:00+08:00",
                "local_modified_time": "2026-06-20T15:05:00+08:00",
                "sync_direction": "in_sync",
                "last_sync_at": "2026-06-20T15:10:00+08:00",
            }
        }
    }
    (tmp_path / ".sync_state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ds = DualStorage(str(tmp_path))
    result = ds.get_doc_state("abc123")
    assert result is not None
    assert result.feishu_node_token == "abc123"
    assert result.local_path == "projects/demo/x.md"
    assert result.sync_direction == "in_sync"

    # Also findable by local_path.
    result_by_path = ds.get_doc_state("projects/demo/x.md")
    assert result_by_path is not None
    assert result_by_path.feishu_node_token == "abc123"


def test_get_doc_state_not_found(tmp_path):
    """get_doc_state returns None for an unknown key."""
    from dual_storage import DualStorage
    ds = DualStorage(str(tmp_path))
    assert ds.get_doc_state("does_not_exist") is None


# ---------------------------------------------------------------------------
# DualStorage.update_doc_state
# ---------------------------------------------------------------------------

def test_update_doc_state(tmp_path):
    """update_doc_state inserts a new doc and updates an existing one."""
    from dual_storage import DualStorage, SyncState
    ds = DualStorage(str(tmp_path))

    # Insert new.
    s1 = SyncState(
        local_path="projects/demo/a.md",
        feishu_node_token="tok_a",
        sync_direction="local_newer",
    )
    ds.update_doc_state("tok_a", s1)
    got = ds.get_doc_state("tok_a")
    assert got is not None
    assert got.local_path == "projects/demo/a.md"
    assert got.sync_direction == "local_newer"

    # Update existing (change sync_direction).
    s1_updated = SyncState(
        local_path="projects/demo/a.md",
        feishu_node_token="tok_a",
        sync_direction="in_sync",
        last_sync_at="2026-06-20T16:00:00+08:00",
    )
    ds.update_doc_state("tok_a", s1_updated)
    got2 = ds.get_doc_state("tok_a")
    assert got2 is not None
    assert got2.sync_direction == "in_sync"
    assert got2.last_sync_at == "2026-06-20T16:00:00+08:00"

    # Second doc coexists.
    s2 = SyncState(
        local_path="projects/demo/b.md",
        feishu_node_token="tok_b",
        sync_direction="feishu_newer",
    )
    ds.update_doc_state("tok_b", s2)
    state = ds.load_state()
    assert set(state["docs"].keys()) == {"tok_a", "tok_b"}


# ---------------------------------------------------------------------------
# DualStorage.remove_doc_state
# ---------------------------------------------------------------------------

def test_remove_doc_state(tmp_path):
    """remove_doc_state deletes an entry; missing key is a no-op."""
    from dual_storage import DualStorage, SyncState
    ds = DualStorage(str(tmp_path))

    s = SyncState(
        local_path="projects/demo/a.md",
        feishu_node_token="tok_a",
    )
    ds.update_doc_state("tok_a", s)
    assert ds.get_doc_state("tok_a") is not None

    # Remove.
    ds.remove_doc_state("tok_a")
    assert ds.get_doc_state("tok_a") is None

    # Removing a missing key must not raise.
    ds.remove_doc_state("never_existed")
    assert ds.load_state() == {"docs": {}}


# ===========================================================================
# Task 6: SyncResult dataclass + DualStorage.sync_to_feishu
# ===========================================================================

# ---------------------------------------------------------------------------
# SyncResult dataclass
# ---------------------------------------------------------------------------

def test_sync_result_dataclass():
    """SyncResult exposes the 5 required fields with correct defaults."""
    from dual_storage import SyncResult

    field_names = {f.name for f in fields(SyncResult)}
    assert field_names == {
        "success", "action", "node_token", "feishu_url", "error"
    }

    # Default values: success is required (no default), the rest default to "".
    result = SyncResult(success=True)
    assert result.success is True
    assert result.action == ""
    assert result.node_token == ""
    assert result.feishu_url == ""
    assert result.error == ""

    # Fully-populated failure result round-trips through fields.
    fail = SyncResult(
        success=False, action="failed", error="boom"
    )
    assert fail.success is False
    assert fail.action == "failed"
    assert fail.error == "boom"


# ---------------------------------------------------------------------------
# DualStorage.sync_to_feishu -- new doc (create flow)
# ---------------------------------------------------------------------------

def test_sync_to_feishu_new_doc(tmp_path):
    """Pushing an unknown local doc creates a Feishu doc and records state.

    Verifies:
    - ``create_doc`` is called with the agent_node_token, the title parsed
      from the OKF frontmatter, and the OKF->Feishu converted content.
    - ``update_doc`` is NOT called (no existing node_token).
    - The returned SyncResult is successful with ``action="created"`` and the
      node_token returned by the mock.
    - sync_state.json now has an entry keyed by the new node_token, with
      ``sync_direction="in_sync"`` and a populated ``feishu_url``.
    """
    from dual_storage import DualStorage, SyncState
    from wiki_connector import okf_to_feishu_content

    mock_wc = _make_mock_wiki_connector(create_return="new_node_123")
    ds = DualStorage(str(tmp_path), wiki_connector=mock_wc)

    result = ds.sync_to_feishu("projects/demo/test.md", SAMPLE_OKF)

    # --- create_doc was called correctly ---
    assert mock_wc.create_doc.called
    call_args = mock_wc.create_doc.call_args
    # Positional args: (parent_node_token, title, content_md).
    assert call_args.args[0] == "agent_root_token"
    assert call_args.args[1] == "测试会议"
    # Content passed to create_doc is the OKF->Feishu conversion.
    expected_content = okf_to_feishu_content(SAMPLE_OKF)
    assert call_args.args[2] == expected_content

    # update_doc must not have been called on a brand-new doc.
    assert not mock_wc.update_doc.called

    # --- SyncResult ---
    assert result.success is True
    assert result.action == "created"
    assert result.node_token == "new_node_123"
    assert result.feishu_url == "https://feishu.cn/wiki/new_node_123"
    assert result.error == ""

    # --- sync_state.json now has the entry, keyed by node_token ---
    state = ds.load_state()
    assert "new_node_123" in state["docs"]
    entry = SyncState.from_dict(state["docs"]["new_node_123"])
    assert entry.feishu_node_token == "new_node_123"
    assert entry.local_path == "projects/demo/test.md"
    assert entry.feishu_url == "https://feishu.cn/wiki/new_node_123"
    assert entry.sync_direction == "in_sync"
    assert entry.local_content_hash == ds._compute_hash(SAMPLE_OKF)
    assert entry.last_sync_at  # non-empty timestamp


# ---------------------------------------------------------------------------
# DualStorage.sync_to_feishu -- existing doc (update flow)
# ---------------------------------------------------------------------------

def test_sync_to_feishu_update_existing(tmp_path):
    """Pushing a doc that already has a feishu_node_token updates it.

    Pre-seeds sync_state with an entry whose ``feishu_node_token`` is
    ``"existing_token"``. After sync_to_feishu:
    - ``update_doc`` is called with ``"existing_token"`` and the converted
      content.
    - ``create_doc`` is NOT called.
    - SyncResult is successful with ``action="updated"`` and the same
      node_token.
    - sync_state entry is still keyed by ``"existing_token"`` and is now
      ``in_sync``.
    """
    from dual_storage import DualStorage, SyncState, SyncDirection
    from wiki_connector import okf_to_feishu_content

    mock_wc = _make_mock_wiki_connector()
    ds = DualStorage(str(tmp_path), wiki_connector=mock_wc)

    # Pre-populate sync_state with an existing entry.
    pre_existing = SyncState(
        local_path="projects/demo/test.md",
        feishu_node_token="existing_token",
        feishu_url="https://feishu.cn/wiki/existing_token",
        local_content_hash="sha256:oldhash",
        sync_direction=SyncDirection.LOCAL_NEWER.value,
    )
    ds.update_doc_state("existing_token", pre_existing)

    result = ds.sync_to_feishu("projects/demo/test.md", SAMPLE_OKF)

    # --- update_doc was called with the existing token + converted content ---
    assert mock_wc.update_doc.called
    update_args = mock_wc.update_doc.call_args
    assert update_args.args[0] == "existing_token"
    expected_content = okf_to_feishu_content(SAMPLE_OKF)
    assert update_args.args[1] == expected_content

    # create_doc must not have been called (we already have a node_token).
    assert not mock_wc.create_doc.called

    # --- SyncResult ---
    assert result.success is True
    assert result.action == "updated"
    assert result.node_token == "existing_token"
    assert result.feishu_url == "https://feishu.cn/wiki/existing_token"

    # --- sync_state still keyed by existing_token, now in_sync ---
    state = ds.load_state()
    assert "existing_token" in state["docs"]
    # No spurious new key was added.
    assert set(state["docs"].keys()) == {"existing_token"}
    entry = SyncState.from_dict(state["docs"]["existing_token"])
    assert entry.feishu_node_token == "existing_token"
    assert entry.sync_direction == "in_sync"
    assert entry.local_content_hash == ds._compute_hash(SAMPLE_OKF)


# ---------------------------------------------------------------------------
# DualStorage.sync_to_feishu -- failure does not update sync_state
# ---------------------------------------------------------------------------

def test_sync_to_feishu_failure_no_state_update(tmp_path):
    """When create_doc raises, sync_to_feishu returns a failure result and
    leaves sync_state untouched (so it stays local_newer for retry).

    Per spec section 3.2 "原子性保证": a failed push must NOT update
    sync_state -- the document remains pending and will be retried next time.
    """
    from dual_storage import DualStorage

    mock_wc = _make_mock_wiki_connector(
        create_raises=RuntimeError("feishu API exploded")
    )
    ds = DualStorage(str(tmp_path), wiki_connector=mock_wc)

    # Fresh bundle: sync_state.json does not exist yet.
    assert not (tmp_path / ".sync_state.json").exists()

    result = ds.sync_to_feishu("projects/demo/test.md", SAMPLE_OKF)

    # --- create_doc was attempted ---
    assert mock_wc.create_doc.called
    # update_doc was never reached.
    assert not mock_wc.update_doc.called

    # --- SyncResult reflects failure ---
    assert result.success is False
    assert result.action == "failed"
    assert "feishu API exploded" in result.error
    assert result.node_token == ""

    # --- sync_state was NOT updated (stays empty / nonexistent) ---
    # Either the file was never written, or it has no docs.
    state = ds.load_state()
    assert state.get("docs", {}) == {}


# ---------------------------------------------------------------------------
# DualStorage.sync_to_feishu -- missing wiki_connector
# ---------------------------------------------------------------------------

def test_sync_to_feishu_no_wiki_connector(tmp_path):
    """Calling sync_to_feishu without a wiki_connector raises RuntimeError.

    This guards against a misconfigured DualStorage attempting any Feishu I/O.
    """
    from dual_storage import DualStorage

    ds = DualStorage(str(tmp_path), wiki_connector=None)
    with pytest.raises(RuntimeError, match="wiki_connector not configured"):
        ds.sync_to_feishu("projects/demo/test.md", SAMPLE_OKF)
