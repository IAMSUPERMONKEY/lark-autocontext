"""Unit tests for DualStorage: sync_state.json management (Task 5).

Covers the state-management slice of :mod:`dual_storage`:

- ``SyncDirection`` enum (4 values).
- ``SyncState`` dataclass ``to_dict`` / ``from_dict`` round-trip.
- ``DualStorage.load_state`` -- empty / existing / corrupted-file recovery.
- ``DualStorage.save_state`` -- atomic write (correct content, no ``.tmp``).
- ``DualStorage._compute_hash`` -- SHA256 with ``sha256:`` prefix.
- ``DualStorage.get_doc_state`` -- lookup by node_token / local_path / missing.
- ``DualStorage.update_doc_state`` -- insert + update.
- ``DualStorage.remove_doc_state`` -- entry removal.

These are pure file/JSON operations; nothing is mocked. Each test uses the
``tmp_path`` pytest fixture for an isolated temp directory.
"""
import sys
import os
import json
import hashlib

# Make scripts/ importable when running from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))


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
