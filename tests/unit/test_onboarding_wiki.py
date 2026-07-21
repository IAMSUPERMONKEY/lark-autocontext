"""Unit tests for Task 15: wiki upgrade status checks in onboarding.py and
.index/.conflicts directory creation in init_bundle.py.

Covers the new onboarding helper functions:

- ``_check_wiki_config(config)`` -- wiki mode configured / not configured.
- ``_check_fts5()`` -- SQLite FTS5 availability probe.
- ``_check_search_index(bundle_path)`` -- .index/search.db exists / missing.
- ``_check_sync_state(bundle_path)`` -- .sync_state.json exists / missing.

And the init_bundle change:

- ``init_bundle`` creates ``.index/`` and ``.conflicts/`` directories.

Each onboarding helper returns a ``list[str]`` of status lines so the tests
can assert on the returned text directly (no stdout capture needed).
"""
import os
import sys

import pytest

# Make scripts/ importable when running from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))


# ---------------------------------------------------------------------------
# Wiki config check
# ---------------------------------------------------------------------------

def test_onboarding_wiki_configured():
    """Wiki section with space_id set reports space_id configured."""
    from onboarding import _check_wiki_config
    config = {
        "wiki": {
            "space_id": "space_abc",
            "raw_node_token": "raw_token_123",
            "agent_node_token": "agent_token_456",
        }
    }
    lines = _check_wiki_config(config)
    text = "\n".join(lines)
    assert "Wiki mode: space_id configured" in text
    assert "Wiki raw_node_token configured" in text
    assert "Wiki agent_node_token configured" in text


def test_onboarding_wiki_configured_missing_tokens():
    """Wiki space_id set but tokens missing -> WARN lines for tokens."""
    from onboarding import _check_wiki_config
    config = {"wiki": {"space_id": "space_abc"}}
    lines = _check_wiki_config(config)
    text = "\n".join(lines)
    assert "Wiki mode: space_id configured" in text
    assert "Wiki raw_node_token not set" in text
    assert "Wiki agent_node_token not set" in text


def test_onboarding_wiki_not_configured():
    """Config without wiki section reports not configured (folder mode)."""
    from onboarding import _check_wiki_config
    config = {}  # no wiki key at all
    lines = _check_wiki_config(config)
    text = "\n".join(lines)
    assert "not configured (folder mode)" in text


def test_onboarding_wiki_empty_space_id():
    """Wiki section present but space_id empty -> folder mode."""
    from onboarding import _check_wiki_config
    config = {"wiki": {"space_id": ""}}
    lines = _check_wiki_config(config)
    text = "\n".join(lines)
    assert "not configured (folder mode)" in text


# ---------------------------------------------------------------------------
# FTS5 availability check
# ---------------------------------------------------------------------------

def test_onboarding_fts5_check():
    """FTS5 should be available on this system (standard SQLite builds)."""
    from onboarding import _check_fts5
    lines = _check_fts5()
    text = "\n".join(lines)
    assert "SQLite FTS5 available" in text
    assert "not available" not in text


# ---------------------------------------------------------------------------
# Search index check
# ---------------------------------------------------------------------------

def test_onboarding_index_check_exists(tmp_path):
    """When .index/search.db exists, check reports Search index exists."""
    from onboarding import _check_search_index
    bundle = tmp_path / "bundle"
    index_dir = bundle / ".index"
    index_dir.mkdir(parents=True)
    (index_dir / "search.db").write_bytes(b"dummy")
    lines = _check_search_index(str(bundle))
    text = "\n".join(lines)
    assert "Search index exists" in text
    assert "not built yet" not in text


def test_onboarding_index_check_missing(tmp_path):
    """When .index/search.db is absent, check reports not built yet."""
    from onboarding import _check_search_index
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    lines = _check_search_index(str(bundle))
    text = "\n".join(lines)
    assert "not built yet" in text
    assert "Search index exists" not in text


# ---------------------------------------------------------------------------
# Sync state check
# ---------------------------------------------------------------------------

def test_onboarding_sync_state_check_exists(tmp_path):
    """When .sync_state.json exists, check reports Sync state exists."""
    from onboarding import _check_sync_state
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / ".sync_state.json").write_text("{}", encoding="utf-8")
    lines = _check_sync_state(str(bundle))
    text = "\n".join(lines)
    assert "Sync state exists" in text
    assert "not initialized" not in text


def test_onboarding_sync_state_check_missing(tmp_path):
    """When .sync_state.json is absent, check reports not initialized."""
    from onboarding import _check_sync_state
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    lines = _check_sync_state(str(bundle))
    text = "\n".join(lines)
    assert "not initialized" in text
    assert "Sync state exists" not in text


# ---------------------------------------------------------------------------
# init_bundle creates .index/ and .conflicts/
# ---------------------------------------------------------------------------

def test_init_bundle_creates_index_conflicts(tmp_path):
    """init_bundle creates .index/ and .conflicts/ directories."""
    from init_bundle import init_bundle
    bundle = tmp_path / "newbundle"
    init_bundle(str(bundle))
    assert (bundle / ".index").is_dir()
    assert (bundle / ".conflicts").is_dir()
