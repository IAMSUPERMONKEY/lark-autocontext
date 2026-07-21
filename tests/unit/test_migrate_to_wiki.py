"""Unit tests for Task 16: migrate_to_wiki.py.

Verifies the folder-mode -> wiki-mode config migration tool:

- ``migrate_config(...)`` preserves existing top-level fields and adds the
  ``wiki`` section with ``space_id`` / ``raw_node_token`` / ``agent_node_token``.
- A timestamped backup of the original config.json is written before mutation.
- Pre-existing wiki fields are preserved, while the three core wiki fields are
  overwritten with the new values.
- ``main()`` CLI wires up argparse, calls ``migrate_config``, and prints
  "Next steps" on success.
"""
import contextlib
import io
import json
import os
import sys
from unittest import mock

import pytest

# Make scripts/ importable when running from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))


def _write_config(path, data):
    """Write a JSON config file at *path* (a pathlib.Path or str)."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 1. Basic migration: adds wiki, preserves existing top-level fields
# ---------------------------------------------------------------------------

def test_migrate_config_basic(tmp_path):
    """Migrating a folder-mode config adds the wiki section and preserves
    bundle_path / identity / feishu."""
    from migrate_to_wiki import migrate_config

    cfg_path = tmp_path / "config.json"
    _write_config(cfg_path, {
        "bundle_path": "./bundle",
        "identity": "user",
        "feishu": {"base_token": "tok_abc"},
    })

    result = migrate_config(str(cfg_path), "space123", "raw456", "agent789")

    assert result["success"] is True

    with open(cfg_path, "r", encoding="utf-8") as f:
        migrated = json.load(f)

    # Wiki section added with the three core fields
    assert migrated["wiki"]["space_id"] == "space123"
    assert migrated["wiki"]["raw_node_token"] == "raw456"
    assert migrated["wiki"]["agent_node_token"] == "agent789"

    # Existing fields preserved
    assert migrated["bundle_path"] == "./bundle"
    assert migrated["identity"] == "user"
    assert migrated["feishu"]["base_token"] == "tok_abc"


# ---------------------------------------------------------------------------
# 2. Backup file is created and contains the ORIGINAL config
# ---------------------------------------------------------------------------

def test_migrate_config_creates_backup(tmp_path):
    """A backup_<timestamp> file is created at the returned path and contains
    the pre-migration config (no wiki section yet)."""
    from migrate_to_wiki import migrate_config

    cfg_path = tmp_path / "config.json"
    original = {
        "bundle_path": "./bundle",
        "identity": "user",
        "feishu": {"base_token": "tok_abc"},
    }
    _write_config(cfg_path, original)

    result = migrate_config(str(cfg_path), "space123", "raw456", "agent789")

    assert result["success"] is True
    backup_path = result["backup_path"]
    assert backup_path
    assert os.path.exists(backup_path)

    with open(backup_path, "r", encoding="utf-8") as f:
        backed_up = json.load(f)

    # Backup is the ORIGINAL config -- no wiki section yet
    assert "wiki" not in backed_up
    assert backed_up == original


# ---------------------------------------------------------------------------
# 3. Pre-existing wiki fields are preserved
# ---------------------------------------------------------------------------

def test_migrate_config_preserves_existing_wiki_fields(tmp_path):
    """If the config already has a wiki dict with extra fields, those extras
    are preserved while the three core fields are added."""
    from migrate_to_wiki import migrate_config

    cfg_path = tmp_path / "config.json"
    _write_config(cfg_path, {
        "bundle_path": "./bundle",
        "wiki": {"old_field": "value"},
    })

    result = migrate_config(str(cfg_path), "space_new", "raw_new", "agent_new")

    assert result["success"] is True

    with open(cfg_path, "r", encoding="utf-8") as f:
        migrated = json.load(f)

    # Old field preserved
    assert migrated["wiki"]["old_field"] == "value"
    # New fields added
    assert migrated["wiki"]["space_id"] == "space_new"
    assert migrated["wiki"]["raw_node_token"] == "raw_new"
    assert migrated["wiki"]["agent_node_token"] == "agent_new"


# ---------------------------------------------------------------------------
# 4. Missing config file -> success=False with error message
# ---------------------------------------------------------------------------

def test_migrate_config_not_found(tmp_path):
    """Calling migrate_config on a non-existent path returns success=False
    with an error message that references the path."""
    from migrate_to_wiki import migrate_config

    missing = tmp_path / "does_not_exist.json"
    result = migrate_config(str(missing), "s1", "r1", "a1")

    assert result["success"] is False
    assert result["backup_path"] == ""
    assert str(missing) in result["message"] or "does_not_exist.json" in result["message"]


# ---------------------------------------------------------------------------
# 5. Existing wiki core fields are overwritten, not merged
# ---------------------------------------------------------------------------

def test_migrate_config_overwrites_existing_wiki(tmp_path):
    """When wiki.space_id already exists, migrating with a new space_id
    overwrites it (not skipped)."""
    from migrate_to_wiki import migrate_config

    cfg_path = tmp_path / "config.json"
    _write_config(cfg_path, {
        "bundle_path": "./bundle",
        "wiki": {"space_id": "old_space"},
    })

    result = migrate_config(str(cfg_path), "new_space", "r1", "a1")

    assert result["success"] is True

    with open(cfg_path, "r", encoding="utf-8") as f:
        migrated = json.load(f)

    assert migrated["wiki"]["space_id"] == "new_space"
    assert migrated["wiki"]["raw_node_token"] == "r1"
    assert migrated["wiki"]["agent_node_token"] == "a1"


# ---------------------------------------------------------------------------
# 6. CLI main() wires up argparse and prints Next steps
# ---------------------------------------------------------------------------

def test_cli_main(tmp_path):
    """Invoking main() with mocked sys.argv migrates the config and prints
    a 'Next steps' section on stdout."""
    from migrate_to_wiki import main

    cfg_path = tmp_path / "config.json"
    _write_config(cfg_path, {
        "bundle_path": "./bundle",
        "identity": "user",
    })

    argv = [
        "migrate_to_wiki.py",
        "--space-id", "s1",
        "--raw-node", "r1",
        "--agent-node", "a1",
        "--config", str(cfg_path),
    ]

    stdout_buf = io.StringIO()
    with mock.patch("sys.argv", argv), contextlib.redirect_stdout(stdout_buf):
        main()

    # Config actually migrated
    with open(cfg_path, "r", encoding="utf-8") as f:
        migrated = json.load(f)
    assert migrated["wiki"]["space_id"] == "s1"
    assert migrated["wiki"]["raw_node_token"] == "r1"
    assert migrated["wiki"]["agent_node_token"] == "a1"
    # Existing field preserved through the CLI path too
    assert migrated["identity"] == "user"

    output = stdout_buf.getvalue()
    assert "Next steps" in output
