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


# ===========================================================================
# Task 7: SyncItem dataclass + detect_feishu_edits + pull_from_feishu +
# _backup_conflict (Feishu -> local pull flow)
# ===========================================================================

# Sample Feishu content (emoji metadata header + body) returned by
# fetch_doc_content. feishu_to_okf_body strips the header and cleans the body,
# so the pull flow must re-attach the local frontmatter before writing.
SAMPLE_FEISHU_CONTENT = (
    "📝 类型：Meeting Minutes | 项目：demo | 标签：测试\n"
    "👥 相关人员：张三 | 📅 2026-06-20\n"
    "---\n"
    "\n"
    "# Updated Summary\n"
    "\n"
    "这是更新后的飞书内容。\n"
)


def _make_doc_info(node_token, modified_time, title="doc", obj_type="docx",
                   url=None, has_children=False):
    """Build a DocInfo for mocking list_agent_docs."""
    from wiki_connector import DocInfo
    return DocInfo(
        node_token=node_token,
        title=title,
        obj_type=obj_type,
        modified_time=modified_time,
        url=url or f"https://feishu.cn/wiki/{node_token}",
        has_children=has_children,
    )


# ---------------------------------------------------------------------------
# SyncItem dataclass
# ---------------------------------------------------------------------------

def test_sync_item_dataclass():
    """SyncItem exposes the 4 required fields."""
    from dual_storage import SyncItem
    from dataclasses import fields as dc_fields

    field_names = {f.name for f in dc_fields(SyncItem)}
    assert field_names == {
        "node_token", "local_path", "feishu_modified_time", "action_needed"
    }

    item = SyncItem(
        node_token="nt_1",
        local_path="projects/demo/a.md",
        feishu_modified_time="2026-06-20T15:00:00+08:00",
        action_needed="pull",
    )
    assert item.node_token == "nt_1"
    assert item.local_path == "projects/demo/a.md"
    assert item.feishu_modified_time == "2026-06-20T15:00:00+08:00"
    assert item.action_needed == "pull"


# ---------------------------------------------------------------------------
# DualStorage.detect_feishu_edits
# ---------------------------------------------------------------------------

def test_detect_feishu_edits_finds_changed(tmp_path):
    """Docs with modified_time newer than sync_state are flagged for pull.

    Two Agent-area docs are listed: one edited after the recorded
    feishu_modified_time, one unchanged. Only the edited one is returned,
    as a SyncItem with action_needed="pull".
    """
    from dual_storage import DualStorage, SyncState, SyncItem

    mock_wc = MagicMock()
    mock_wc.list_agent_docs.return_value = [
        _make_doc_info("nt_newer", "2026-06-21T10:00:00+08:00"),
        _make_doc_info("nt_unchanged", "2026-06-20T10:00:00+08:00"),
    ]
    ds = DualStorage(str(tmp_path), wiki_connector=mock_wc)

    # nt_newer: recorded time is older -> needs pull.
    ds.update_doc_state("nt_newer", SyncState(
        local_path="projects/demo/newer.md",
        feishu_node_token="nt_newer",
        feishu_modified_time="2026-06-20T09:00:00+08:00",
    ))
    # nt_unchanged: recorded time equals feishu time -> no pull.
    ds.update_doc_state("nt_unchanged", SyncState(
        local_path="projects/demo/unchanged.md",
        feishu_node_token="nt_unchanged",
        feishu_modified_time="2026-06-20T10:00:00+08:00",
    ))

    items = ds.detect_feishu_edits()
    assert len(items) == 1
    item = items[0]
    assert isinstance(item, SyncItem)
    assert item.node_token == "nt_newer"
    assert item.local_path == "projects/demo/newer.md"
    assert item.feishu_modified_time == "2026-06-21T10:00:00+08:00"
    assert item.action_needed == "pull"


def test_detect_feishu_edits_finds_new(tmp_path):
    """Docs not in sync_state are returned with action_needed='unknown'."""
    from dual_storage import DualStorage, SyncItem

    mock_wc = MagicMock()
    mock_wc.list_agent_docs.return_value = [
        _make_doc_info("nt_brand_new", "2026-06-21T10:00:00+08:00"),
    ]
    ds = DualStorage(str(tmp_path), wiki_connector=mock_wc)

    items = ds.detect_feishu_edits()
    assert len(items) == 1
    item = items[0]
    assert isinstance(item, SyncItem)
    assert item.node_token == "nt_brand_new"
    assert item.local_path == ""  # no local mapping yet
    assert item.feishu_modified_time == "2026-06-21T10:00:00+08:00"
    assert item.action_needed == "unknown"


def test_detect_feishu_edits_no_connector(tmp_path):
    """No wiki_connector -> returns empty list (no Feishu I/O possible)."""
    from dual_storage import DualStorage

    ds = DualStorage(str(tmp_path), wiki_connector=None)
    assert ds.detect_feishu_edits() == []


def test_detect_feishu_edits_no_changes(tmp_path):
    """All docs match sync_state times -> empty list."""
    from dual_storage import DualStorage, SyncState

    mock_wc = MagicMock()
    mock_wc.list_agent_docs.return_value = [
        _make_doc_info("nt_a", "2026-06-20T10:00:00+08:00"),
    ]
    ds = DualStorage(str(tmp_path), wiki_connector=mock_wc)
    ds.update_doc_state("nt_a", SyncState(
        local_path="projects/demo/a.md",
        feishu_node_token="nt_a",
        feishu_modified_time="2026-06-20T10:00:00+08:00",
    ))

    assert ds.detect_feishu_edits() == []


# ---------------------------------------------------------------------------
# DualStorage.pull_from_feishu -- no conflict (safe overwrite)
# ---------------------------------------------------------------------------

def test_pull_from_feishu_no_conflict(tmp_path):
    """Local file hash matches sync_state -> safe overwrite, no backup.

    Verifies:
    - fetch_doc_content is called with the node_token.
    - Local file is overwritten with the new Feishu body while the existing
      YAML frontmatter is preserved.
    - No .conflicts/ directory is created.
    - sync_state is updated to in_sync with the new content hash.
    - SyncResult is successful with action="pulled".
    """
    from dual_storage import DualStorage, SyncState, SyncDirection

    mock_wc = MagicMock()
    mock_wc.fetch_doc_content.return_value = SAMPLE_FEISHU_CONTENT
    ds = DualStorage(str(tmp_path), wiki_connector=mock_wc)

    # Create local OKF file whose hash matches sync_state.
    local_rel = "projects/demo/test.md"
    local_file = tmp_path / "projects" / "demo" / "test.md"
    local_file.parent.mkdir(parents=True, exist_ok=True)
    local_file.write_text(SAMPLE_OKF, encoding="utf-8")

    pre_state = SyncState(
        local_path=local_rel,
        feishu_node_token="nt_pull",
        feishu_url="https://feishu.cn/wiki/nt_pull",
        local_content_hash=ds._compute_hash(SAMPLE_OKF),
        feishu_modified_time="2026-06-20T09:00:00+08:00",
        sync_direction=SyncDirection.FEISHU_NEWER.value,
    )
    ds.update_doc_state("nt_pull", pre_state)

    result = ds.pull_from_feishu("nt_pull")

    # --- SyncResult success ---
    assert result.success is True
    assert result.action == "pulled"
    assert result.node_token == "nt_pull"
    assert result.feishu_url == "https://feishu.cn/wiki/nt_pull"
    assert result.error == ""

    # --- fetch_doc_content was called with node_token ---
    mock_wc.fetch_doc_content.assert_called_once_with("nt_pull")

    # --- File overwritten with new body, frontmatter preserved ---
    new_content = local_file.read_text(encoding="utf-8")
    assert new_content.startswith("---")
    assert "type: Meeting Minutes" in new_content
    assert "# Updated Summary" in new_content
    assert "更新后的飞书内容" in new_content
    # Old body is gone.
    assert "测试内容" not in new_content

    # --- No conflict backup created ---
    assert not (tmp_path / ".conflicts").exists()

    # --- sync_state updated to in_sync with new hash ---
    state = ds.get_doc_state("nt_pull")
    assert state.sync_direction == SyncDirection.IN_SYNC.value
    assert state.local_content_hash == ds._compute_hash(new_content)
    assert state.feishu_modified_time  # non-empty
    assert state.last_sync_at  # non-empty


# ---------------------------------------------------------------------------
# DualStorage.pull_from_feishu -- conflict (local changed, Feishu wins)
# ---------------------------------------------------------------------------

def test_pull_from_feishu_conflict(tmp_path):
    """Local file hash mismatch -> conflict, backup created, file overwritten.

    Per spec section 3.2 "冲突解决策略": Feishu wins, local old version is
    backed up to bundle/.conflicts/{node_token}_{timestamp}.md and logged.
    """
    from dual_storage import DualStorage, SyncState, SyncDirection

    mock_wc = MagicMock()
    mock_wc.fetch_doc_content.return_value = SAMPLE_FEISHU_CONTENT
    ds = DualStorage(str(tmp_path), wiki_connector=mock_wc)

    # Create local OKF file with MODIFIED content (hash won't match state).
    local_rel = "projects/demo/conflict.md"
    local_file = tmp_path / "projects" / "demo" / "conflict.md"
    local_file.parent.mkdir(parents=True, exist_ok=True)
    modified_local = SAMPLE_OKF.replace("这是测试内容。", "这是本地修改的内容。")
    local_file.write_text(modified_local, encoding="utf-8")

    # sync_state records the ORIGINAL hash (mismatch with actual file).
    pre_state = SyncState(
        local_path=local_rel,
        feishu_node_token="nt_conflict",
        feishu_url="https://feishu.cn/wiki/nt_conflict",
        local_content_hash=ds._compute_hash(SAMPLE_OKF),
        feishu_modified_time="2026-06-20T09:00:00+08:00",
        sync_direction=SyncDirection.FEISHU_NEWER.value,
    )
    ds.update_doc_state("nt_conflict", pre_state)

    result = ds.pull_from_feishu("nt_conflict")

    # --- SyncResult still success (Feishu wins) ---
    assert result.success is True
    assert result.action == "pulled"
    assert result.node_token == "nt_conflict"

    # --- Backup created in .conflicts/ ---
    conflicts_dir = tmp_path / ".conflicts"
    assert conflicts_dir.exists()
    backups = list(conflicts_dir.glob("nt_conflict_*.md"))
    assert len(backups) == 1
    # Backup contains the OLD local content (with the local edit).
    assert "本地修改的内容" in backups[0].read_text(encoding="utf-8")

    # --- log.md has an entry ---
    log_md = conflicts_dir / "log.md"
    assert log_md.exists()
    log_content = log_md.read_text(encoding="utf-8")
    assert "nt_conflict" in log_content
    assert "Conflict" in log_content
    assert backups[0].name in log_content

    # --- File overwritten with Feishu content ---
    new_content = local_file.read_text(encoding="utf-8")
    assert "更新后的飞书内容" in new_content
    assert "本地修改的内容" not in new_content
    # Frontmatter preserved.
    assert new_content.startswith("---")
    assert "type: Meeting Minutes" in new_content

    # --- sync_state updated to in_sync ---
    state = ds.get_doc_state("nt_conflict")
    assert state.sync_direction == SyncDirection.IN_SYNC.value


# ---------------------------------------------------------------------------
# DualStorage.pull_from_feishu -- no local mapping
# ---------------------------------------------------------------------------

def test_pull_from_feishu_no_local_mapping(tmp_path):
    """node_token not in sync_state -> failure with 'no local mapping'."""
    from dual_storage import DualStorage

    mock_wc = MagicMock()
    ds = DualStorage(str(tmp_path), wiki_connector=mock_wc)

    result = ds.pull_from_feishu("nt_unknown")

    assert result.success is False
    assert "no local mapping" in result.error
    # fetch_doc_content should NOT have been called (short-circuit).
    assert not mock_wc.fetch_doc_content.called


# ---------------------------------------------------------------------------
# DualStorage.pull_from_feishu -- no wiki_connector
# ---------------------------------------------------------------------------

def test_pull_from_feishu_no_connector(tmp_path):
    """No wiki_connector -> RuntimeError (mirrors sync_to_feishu)."""
    from dual_storage import DualStorage

    ds = DualStorage(str(tmp_path), wiki_connector=None)
    with pytest.raises(RuntimeError, match="wiki_connector not configured"):
        ds.pull_from_feishu("nt_any")


# ---------------------------------------------------------------------------
# DualStorage.pull_from_feishu -- fetch failure
# ---------------------------------------------------------------------------

def test_pull_from_feishu_fetch_failure(tmp_path):
    """fetch_doc_content raises -> failure result carrying the error message."""
    from dual_storage import DualStorage, SyncState

    mock_wc = MagicMock()
    mock_wc.fetch_doc_content.side_effect = RuntimeError("feishu fetch boom")
    ds = DualStorage(str(tmp_path), wiki_connector=mock_wc)

    # Pre-populate sync_state so we get past the local-mapping check.
    ds.update_doc_state("nt_fetch_fail", SyncState(
        local_path="projects/demo/fail.md",
        feishu_node_token="nt_fetch_fail",
        local_content_hash="sha256:whatever",
    ))

    result = ds.pull_from_feishu("nt_fetch_fail")

    assert result.success is False
    assert "feishu fetch boom" in result.error


# ---------------------------------------------------------------------------
# DualStorage._backup_conflict
# ---------------------------------------------------------------------------

def test_backup_conflict(tmp_path):
    """_backup_conflict writes the backup file and appends to log.md.

    Called directly to verify the helper in isolation. The backup file is
    named ``{node_token}_{timestamp}.md`` and placed under
    ``bundle/.conflicts/``. ``log.md`` in the same directory records the
    conflict metadata (node_token, local_path, backup filename).
    """
    from dual_storage import DualStorage
    import os.path

    ds = DualStorage(str(tmp_path))
    old_content = "# Old local content\n人类修改的内容\n"

    backup_path = ds._backup_conflict(
        "nt_test", "projects/demo/x.md", old_content
    )

    # --- Backup file exists with correct name pattern ---
    assert os.path.exists(backup_path)
    backup_name = os.path.basename(backup_path)
    assert backup_name.startswith("nt_test_")
    assert backup_name.endswith(".md")

    # --- Backup file contains the old content ---
    with open(backup_path, "r", encoding="utf-8") as f:
        assert f.read() == old_content

    # --- .conflicts/ directory was created ---
    conflicts_dir = tmp_path / ".conflicts"
    assert conflicts_dir.exists()

    # --- log.md has the entry ---
    log_path = conflicts_dir / "log.md"
    assert log_path.exists()
    log_content = log_path.read_text(encoding="utf-8")
    assert "nt_test" in log_content
    assert "projects/demo/x.md" in log_content
    assert backup_name in log_content
    assert "Conflict" in log_content
