"""Unit tests for auto_sync.py wiki mode (Task 14).

Covers the wiki-mode branch added to ``cmd_list_only``: when config.json
contains ``wiki.space_id``, the function uses ``WikiConnector.list_raw_docs``
instead of ``scanner.list_changed``. Legacy folder-mode behaviour is verified
to be preserved unchanged.

All WikiConnector / scanner interactions are mocked -- no real lark-cli calls
are made.
"""
import sys
import os
import json
import argparse
from unittest.mock import MagicMock

import pytest

# Make scripts/ importable when running from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))

from wiki_connector import DocInfo  # noqa: E402
import scanner  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _make_wiki_config(space_id="test_space", raw_node_token="raw_root",
                      agent_node_token="agent_root"):
    """Return a config dict with a wiki section."""
    return {
        "wiki": {
            "space_id": space_id,
            "raw_node_token": raw_node_token,
            "agent_node_token": agent_node_token,
        },
        "identity": "user",
    }


def _make_mock_connector(docs=None):
    """Build a MagicMock standing in for a WikiConnector."""
    conn = MagicMock()
    conn.space_id = "test_space"
    conn.raw_node_token = "raw_root"
    conn.agent_node_token = "agent_root"
    conn.list_raw_docs.return_value = docs or []
    return conn


def _setup_wiki_mode(monkeypatch, tmp_path, config_dict):
    """Common setup: patch MAIN_CONFIG_PATH/STATE_PATH/PENDING_PATH for wiki mode.

    Returns the pending_path string.
    """
    import auto_sync

    main_config_path = tmp_path / "config.json"
    main_config_path.write_text(
        json.dumps(config_dict, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(auto_sync, "MAIN_CONFIG_PATH", str(main_config_path))

    monkeypatch.setattr(auto_sync, "STATE_PATH", str(tmp_path / "state.json"))
    pending_path = str(tmp_path / "pending.json")
    monkeypatch.setattr(auto_sync, "PENDING_PATH", pending_path)
    return pending_path


# ---------------------------------------------------------------------------
# Test 1: wiki mode happy path
# ---------------------------------------------------------------------------

def test_cmd_list_only_wiki_mode(tmp_path, monkeypatch):
    """Wiki config detected -> WikiConnector.list_raw_docs -> pending written.

    Asserts:
      - pending_changes.json is written
      - Changes contain 2 entries with correct node_tokens
      - source_type == "wiki_doc" for every change
      - source_scans contains the wiki key
    """
    import auto_sync

    pending_path = _setup_wiki_mode(monkeypatch, tmp_path, _make_wiki_config())

    docs = [
        _make_docinfo("node_1", "Doc One"),
        _make_docinfo("node_2", "Doc Two"),
    ]
    conn = _make_mock_connector(docs=docs)
    monkeypatch.setattr(scanner, "_get_wiki_connector", lambda: conn)

    args = argparse.Namespace(config="dummy")
    ret = auto_sync.cmd_list_only(args)

    assert ret == 0
    pending = json.loads(open(pending_path, "r", encoding="utf-8").read())
    assert len(pending["changes"]) == 2
    node_tokens = [c["node_token"] for c in pending["changes"]]
    assert node_tokens == ["node_1", "node_2"]
    for c in pending["changes"]:
        assert c["source_type"] == "wiki_doc"
        assert c["source"] == "wiki:test_space"
        assert c["doc_token"] == c["node_token"]
        assert c["url"] == f"https://feishu.cn/wiki/{c['node_token']}"
    assert "wiki:test_space" in pending["source_scans"]


# ---------------------------------------------------------------------------
# Test 2: wiki configured but connector creation fails
# ---------------------------------------------------------------------------

def test_cmd_list_only_wiki_no_connector(tmp_path, monkeypatch):
    """Config has wiki.space_id but _get_wiki_connector returns None -> error."""
    import auto_sync

    _setup_wiki_mode(monkeypatch, tmp_path, _make_wiki_config())
    monkeypatch.setattr(scanner, "_get_wiki_connector", lambda: None)

    args = argparse.Namespace(config="dummy")
    ret = auto_sync.cmd_list_only(args)
    assert ret == 1


# ---------------------------------------------------------------------------
# Test 3: wiki list_raw_docs raises an exception
# ---------------------------------------------------------------------------

def test_cmd_list_only_wiki_list_fails(tmp_path, monkeypatch):
    """list_raw_docs raises -> cmd_list_only returns 1 (error)."""
    import auto_sync

    _setup_wiki_mode(monkeypatch, tmp_path, _make_wiki_config())

    conn = _make_mock_connector()
    conn.list_raw_docs.side_effect = RuntimeError("lark-cli failed")
    monkeypatch.setattr(scanner, "_get_wiki_connector", lambda: conn)

    args = argparse.Namespace(config="dummy")
    ret = auto_sync.cmd_list_only(args)
    assert ret == 1


# ---------------------------------------------------------------------------
# Test 4: folder mode unchanged (no wiki config)
# ---------------------------------------------------------------------------

def test_cmd_list_only_folder_mode_unchanged(tmp_path, monkeypatch):
    """No wiki config in config.json -> folder mode path is taken.

    Verifies the legacy behaviour is preserved: reads scan config, calls
    list_changed, writes pending with folder-mode source keys.
    """
    import auto_sync

    # Main config WITHOUT wiki section (so use_wiki stays False)
    main_config_path = tmp_path / "config.json"
    main_config_path.write_text(
        json.dumps({"sources": [], "identity": "user"}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(auto_sync, "MAIN_CONFIG_PATH", str(main_config_path))

    # Scan config (args.config) with a folder source
    scan_cfg = {"sources": [
        {"type": "folder", "token": "fldFAKE", "name": "Test",
         "key": "folder:fldFAKE"}
    ]}
    scan_cfg_path = tmp_path / "scan_config.json"
    scan_cfg_path.write_text(
        json.dumps(scan_cfg, ensure_ascii=False), encoding="utf-8"
    )

    monkeypatch.setattr(auto_sync, "STATE_PATH", str(tmp_path / "state.json"))
    pending_path = str(tmp_path / "pending.json")
    monkeypatch.setattr(auto_sync, "PENDING_PATH", pending_path)

    # Mock scanner.list_changed
    monkeypatch.setattr(scanner, "list_changed", lambda sources, since: {
        "changed": [{"doc_token": "DOC1", "url": "https://x", "title": "T1",
                      "edited_time": "2026-06-21", "source": "folder:fldFAKE"}],
        "source_results": {"folder:fldFAKE": {"ok": True, "error": None}}
    })

    args = argparse.Namespace(config=str(scan_cfg_path))
    ret = auto_sync.cmd_list_only(args)
    assert ret == 0

    pending = json.loads(open(pending_path, "r", encoding="utf-8").read())
    assert len(pending["changes"]) == 1
    assert pending["changes"][0]["doc_token"] == "DOC1"
    assert "folder:fldFAKE" in pending["source_scans"]


# ---------------------------------------------------------------------------
# Test 5: wiki mode with since filter from state
# ---------------------------------------------------------------------------

def test_cmd_list_only_wiki_with_since_filter(tmp_path, monkeypatch):
    """Pre-populated state last_scan_at is passed as ``since`` to list_raw_docs."""
    import auto_sync

    wiki_cfg = _make_wiki_config()
    main_config_path = tmp_path / "config.json"
    main_config_path.write_text(
        json.dumps(wiki_cfg, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(auto_sync, "MAIN_CONFIG_PATH", str(main_config_path))

    # Pre-populate state with last_scan_at for the wiki source
    state_path = tmp_path / "state.json"
    state = {
        "last_scan_at": "2026-07-01T00:00:00Z",
        "sources": {
            "wiki:test_space": {
                "last_scan_at": "2026-07-01T00:00:00Z",
                "last_success": True,
                "last_error": None,
                "consecutive_failures": 0,
            }
        },
        "stats": {}
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(auto_sync, "STATE_PATH", str(state_path))
    monkeypatch.setattr(auto_sync, "PENDING_PATH", str(tmp_path / "pending.json"))

    conn = _make_mock_connector(docs=[_make_docinfo("node_x", "Doc X")])
    monkeypatch.setattr(scanner, "_get_wiki_connector", lambda: conn)

    args = argparse.Namespace(config="dummy")
    ret = auto_sync.cmd_list_only(args)
    assert ret == 0

    # Verify list_raw_docs was called with since from state
    conn.list_raw_docs.assert_called_once_with(since="2026-07-01T00:00:00Z")
