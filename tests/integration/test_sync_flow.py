"""End-to-end integration test for the bidirectional sync flow.

Verifies the full pipeline:
  1. Write OKF document locally
  2. Index it via QueryEngine
  3. Search finds it
  4. Sync to Feishu via DualStorage.sync_to_feishu (mocked WikiConnector)
  5. Detect Feishu-side edit via DualStorage.detect_feishu_edits
  6. Pull from Feishu via DualStorage.pull_from_feishu
  7. Local file updated, sync_state reflects IN_SYNC
"""
import sys
import os
import json
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))

from dual_storage import (  # noqa: E402
    DualStorage, SyncState, SyncDirection, SyncResult, SyncItem,
)
from wiki_connector import (  # noqa: E402
    DocInfo, okf_to_feishu_content, feishu_to_okf_body,
)
from query_engine import QueryEngine, SearchFilters, SearchResult  # noqa: E402


# Sample OKF content for testing -- a Meeting Minutes doc about architecture
# review. Contains CJK terms (架构, 微服务, DDD) used for search verification.
SAMPLE_OKF = '''---
type: Meeting Minutes
title: "架构评审会议"
description: 讨论微服务架构拆分方案
timestamp: 2026-07-15T10:00:00+08:00
project: platform
tags: [架构, 微服务, 评审]
people: [张三, 李四]
concepts: [微服务, DDD]
---

# Summary

本次会议讨论了微服务架构拆分方案，决定采用 DDD 领域驱动设计方法。

# Key Points

- 确定采用 DDD 进行领域建模
- 识别出 3 个核心限界上下文
- 下周完成详细设计文档
'''

# Sample Feishu content simulating a human edit on the Feishu side. The emoji
# metadata header mirrors what okf_to_feishu_content produces; the body has
# been edited to reflect follow-up decisions. strip_metadata_header removes
# the header, leaving the new body for pull_from_feishu to write locally.
SAMPLE_FEISHU_EDITED = (
    "📝 类型：Meeting Minutes | 项目：platform | 标签：架构, 微服务, 评审\n"
    "👥 相关人员：张三, 李四 | 📅 2026-07-15\n"
    "---\n"
    "\n"
    "# Summary\n"
    "\n"
    "这是飞书端更新后的内容。微服务架构方案已最终确认并落地。\n"
    "\n"
    "# Key Points\n"
    "\n"
    "- DDD 领域驱动设计已落地实施\n"
    "- 3 个限界上下文已划分完成\n"
    "- 详细设计文档已提交\n"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_wiki_connector(create_return: str = "new_node_token",
                               fetch_content: str = None) -> MagicMock:
    """Build a MagicMock standing in for a WikiConnector.

    Configures ``agent_node_token`` and stubs ``create_doc`` /
    ``update_doc`` / ``fetch_doc_content`` / ``list_agent_docs`` with
    sensible defaults. Tests override specific return values as needed.
    """
    mock = MagicMock()
    mock.agent_node_token = "agent_root"
    mock.create_doc.return_value = create_return
    mock.update_doc.return_value = None
    if fetch_content is not None:
        mock.fetch_doc_content.return_value = fetch_content
    return mock


def _make_doc_info(node_token, modified_time, title="doc", obj_type="docx",
                   url=None, has_children=False):
    """Build a DocInfo for mocking list_agent_docs."""
    return DocInfo(
        node_token=node_token,
        title=title,
        obj_type=obj_type,
        modified_time=modified_time,
        url=url or f"https://feishu.cn/wiki/{node_token}",
        has_children=has_children,
    )


def _write_okf(bundle_path, rel_path, content):
    """Write an OKF file inside the bundle and return the absolute path."""
    full_path = os.path.join(bundle_path, rel_path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)
    return full_path


# ---------------------------------------------------------------------------
# Test 1: test_full_sync_push_flow
# ---------------------------------------------------------------------------

def test_full_sync_push_flow(tmp_path):
    """Full push flow: write OKF -> sync to Feishu -> verify state.

    Verifies:
    - ``create_doc`` is called with the agent_node_token, the title parsed
      from frontmatter, and the OKF->Feishu converted content.
    - ``update_doc`` is NOT called (no existing node_token).
    - sync_state.json now has an entry keyed by the new node_token.
    - sync_direction is IN_SYNC after a successful push.
    """
    bundle = str(tmp_path)
    mock_wc = _make_mock_wiki_connector(create_return="new_node_token")
    ds = DualStorage(bundle, wiki_connector=mock_wc)

    okf_rel = "projects/platform/arch-review.md"
    _write_okf(bundle, okf_rel, SAMPLE_OKF)

    result = ds.sync_to_feishu(okf_rel, SAMPLE_OKF)

    # --- create_doc called with correct parent token + title + content ---
    assert mock_wc.create_doc.called
    call_args = mock_wc.create_doc.call_args
    assert call_args.args[0] == "agent_root"  # parent_node_token
    assert call_args.args[1] == "架构评审会议"   # title from frontmatter
    expected_content = okf_to_feishu_content(SAMPLE_OKF)
    assert call_args.args[2] == expected_content

    # update_doc must not have been called on a brand-new doc.
    assert not mock_wc.update_doc.called

    # --- SyncResult ---
    assert result.success is True
    assert result.action == "created"
    assert result.node_token == "new_node_token"
    assert result.feishu_url == "https://feishu.cn/wiki/new_node_token"

    # --- sync_state.json has the entry keyed by node_token ---
    state = ds.load_state()
    assert "new_node_token" in state["docs"]
    entry = SyncState.from_dict(state["docs"]["new_node_token"])
    assert entry.feishu_node_token == "new_node_token"
    assert entry.local_path == okf_rel
    assert entry.sync_direction == SyncDirection.IN_SYNC.value
    assert entry.local_content_hash == ds._compute_hash(SAMPLE_OKF)
    assert entry.last_sync_at  # non-empty timestamp


# ---------------------------------------------------------------------------
# Test 2: test_sync_then_update_flow
# ---------------------------------------------------------------------------

def test_sync_then_update_flow(tmp_path):
    """Create-then-update flow: first sync creates, second sync updates.

    Verifies:
    - First sync calls ``create_doc`` and records the node_token.
    - After modifying the OKF content, the second sync calls ``update_doc``
      (not ``create_doc``) with the existing node_token.
    - sync_state is updated with the new content hash.
    """
    bundle = str(tmp_path)
    mock_wc = _make_mock_wiki_connector(create_return="node_abc")
    ds = DualStorage(bundle, wiki_connector=mock_wc)

    okf_rel = "projects/platform/arch-review.md"
    _write_okf(bundle, okf_rel, SAMPLE_OKF)

    # --- First sync: create ---
    result1 = ds.sync_to_feishu(okf_rel, SAMPLE_OKF)
    assert result1.success is True
    assert result1.action == "created"
    assert result1.node_token == "node_abc"
    assert mock_wc.create_doc.call_count == 1
    assert not mock_wc.update_doc.called

    # sync_state now has the entry.
    state_after_create = ds.load_state()
    assert "node_abc" in state_after_create["docs"]
    hash_after_create = state_after_create["docs"]["node_abc"][
        "local_content_hash"]

    # --- Modify OKF content locally ---
    modified_okf = SAMPLE_OKF.replace(
        "识别出 3 个核心限界上下文",
        "识别出 5 个核心限界上下文（已更新）",
    )
    _write_okf(bundle, okf_rel, modified_okf)

    # --- Second sync: update ---
    result2 = ds.sync_to_feishu(okf_rel, modified_okf)
    assert result2.success is True
    assert result2.action == "updated"
    assert result2.node_token == "node_abc"  # same node_token

    # update_doc was called with the existing token + converted content.
    assert mock_wc.update_doc.called
    update_args = mock_wc.update_doc.call_args
    assert update_args.args[0] == "node_abc"
    expected_content = okf_to_feishu_content(modified_okf)
    assert update_args.args[1] == expected_content

    # create_doc was NOT called again (still 1 call from the first sync).
    assert mock_wc.create_doc.call_count == 1

    # sync_state updated with the new hash.
    state_after_update = ds.load_state()
    assert "node_abc" in state_after_update["docs"]
    entry = SyncState.from_dict(state_after_update["docs"]["node_abc"])
    assert entry.sync_direction == SyncDirection.IN_SYNC.value
    assert entry.local_content_hash == ds._compute_hash(modified_okf)
    assert entry.local_content_hash != hash_after_create


# ---------------------------------------------------------------------------
# Test 3: test_detect_feishu_edit_flow
# ---------------------------------------------------------------------------

def test_detect_feishu_edit_flow(tmp_path):
    """Detect flow: synced doc edited on Feishu -> flagged for pull.

    Verifies:
    - ``detect_feishu_edits`` calls ``list_agent_docs`` on the connector.
    - A doc whose Feishu modified_time is newer than the recorded
      feishu_modified_time is returned as a SyncItem with
      ``action_needed="pull"``.
    - An unchanged doc is NOT returned.
    """
    bundle = str(tmp_path)
    mock_wc = _make_mock_wiki_connector()
    mock_wc.list_agent_docs.return_value = [
        _make_doc_info("nt_edited", "2026-07-20T10:00:00+08:00",
                       title="架构评审会议"),
        _make_doc_info("nt_unchanged", "2026-07-15T10:00:00+08:00",
                       title="其他文档"),
    ]
    ds = DualStorage(bundle, wiki_connector=mock_wc)

    # nt_edited: recorded time is older -> needs pull.
    ds.update_doc_state("nt_edited", SyncState(
        local_path="projects/platform/arch-review.md",
        feishu_node_token="nt_edited",
        feishu_modified_time="2026-07-15T09:00:00+08:00",
        sync_direction=SyncDirection.IN_SYNC.value,
    ))
    # nt_unchanged: recorded time equals Feishu time -> no pull.
    ds.update_doc_state("nt_unchanged", SyncState(
        local_path="projects/other/doc.md",
        feishu_node_token="nt_unchanged",
        feishu_modified_time="2026-07-15T10:00:00+08:00",
        sync_direction=SyncDirection.IN_SYNC.value,
    ))

    items = ds.detect_feishu_edits()

    # list_agent_docs was called.
    mock_wc.list_agent_docs.assert_called_once()

    # Only the edited doc is returned.
    assert len(items) == 1
    item = items[0]
    assert isinstance(item, SyncItem)
    assert item.node_token == "nt_edited"
    assert item.local_path == "projects/platform/arch-review.md"
    assert item.feishu_modified_time == "2026-07-20T10:00:00+08:00"
    assert item.action_needed == "pull"


# ---------------------------------------------------------------------------
# Test 4: test_pull_from_feishu_flow
# ---------------------------------------------------------------------------

def test_pull_from_feishu_flow(tmp_path):
    """Pull flow: Feishu edit pulled back -> local file updated, state in_sync.

    Verifies the full pull pipeline:
    - ``fetch_doc_content`` is called with the node_token.
    - Local OKF file is overwritten with the new body while the existing
      YAML frontmatter is preserved.
    - sync_state is updated to IN_SYNC with the new content hash.
    - SyncResult is successful with action="pulled".
    """
    bundle = str(tmp_path)
    mock_wc = _make_mock_wiki_connector(
        fetch_content=SAMPLE_FEISHU_EDITED,
    )
    ds = DualStorage(bundle, wiki_connector=mock_wc)

    # Write the local OKF file (matching the recorded hash -> no conflict).
    okf_rel = "projects/platform/arch-review.md"
    local_file = _write_okf(bundle, okf_rel, SAMPLE_OKF)

    # Pre-seed sync_state: hash matches the on-disk file (no local edit).
    pre_state = SyncState(
        local_path=okf_rel,
        feishu_node_token="nt_pull",
        feishu_url="https://feishu.cn/wiki/nt_pull",
        local_content_hash=ds._compute_hash(SAMPLE_OKF),
        feishu_modified_time="2026-07-15T09:00:00+08:00",
        sync_direction=SyncDirection.FEISHU_NEWER.value,
    )
    ds.update_doc_state("nt_pull", pre_state)

    result = ds.pull_from_feishu("nt_pull")

    # --- SyncResult success ---
    assert result.success is True
    assert result.action == "pulled"
    assert result.node_token == "nt_pull"
    assert result.feishu_url == "https://feishu.cn/wiki/nt_pull"

    # --- fetch_doc_content was called with node_token ---
    mock_wc.fetch_doc_content.assert_called_once_with("nt_pull")

    # --- Local file overwritten with new body, frontmatter preserved ---
    new_content = open(local_file, "r", encoding="utf-8").read()
    assert new_content.startswith("---")
    assert "type: Meeting Minutes" in new_content
    assert "title: \"架构评审会议\"" in new_content
    assert "飞书端更新后的内容" in new_content
    assert "DDD 领域驱动设计已落地实施" in new_content
    # Old body is gone.
    assert "讨论了微服务架构拆分方案" not in new_content

    # --- No conflict backup (hash matched) ---
    assert not os.path.exists(os.path.join(bundle, ".conflicts"))

    # --- sync_state updated to IN_SYNC with new hash ---
    state = ds.get_doc_state("nt_pull")
    assert state.sync_direction == SyncDirection.IN_SYNC.value
    assert state.local_content_hash == ds._compute_hash(new_content)
    assert state.feishu_modified_time  # non-empty
    assert state.last_sync_at  # non-empty


# ---------------------------------------------------------------------------
# Test 5: test_conflict_backup_flow
# ---------------------------------------------------------------------------

def test_conflict_backup_flow(tmp_path):
    """Conflict flow: local edited + Feishu edited -> backup + overwrite.

    Combines detect_feishu_edits + pull_from_feishu to verify the full
    conflict resolution pipeline:
    1. detect_feishu_edits flags the doc for pull (Feishu newer).
    2. pull_from_feishu detects the local file hash mismatches the
       recorded hash (local was also edited) -> conflict.
    3. Local old content is backed up to ``.conflicts/``.
    4. ``log.md`` in .conflicts/ records the conflict.
    5. Local file is overwritten with Feishu content.
    6. sync_state is IN_SYNC after the pull.
    """
    bundle = str(tmp_path)
    mock_wc = _make_mock_wiki_connector(
        fetch_content=SAMPLE_FEISHU_EDITED,
    )
    mock_wc.list_agent_docs.return_value = [
        _make_doc_info("nt_conflict", "2026-07-20T10:00:00+08:00",
                       title="架构评审会议"),
    ]
    ds = DualStorage(bundle, wiki_connector=mock_wc)

    # Write a locally-modified OKF file (differs from the original hash
    # recorded in sync_state -> triggers conflict on pull).
    okf_rel = "projects/platform/arch-review.md"
    local_modified = SAMPLE_OKF.replace(
        "识别出 3 个核心限界上下文",
        "识别出 5 个核心限界上下文（本地修改）",
    )
    local_file = _write_okf(bundle, okf_rel, local_modified)

    # sync_state records the ORIGINAL hash (mismatch with actual file).
    ds.update_doc_state("nt_conflict", SyncState(
        local_path=okf_rel,
        feishu_node_token="nt_conflict",
        feishu_url="https://feishu.cn/wiki/nt_conflict",
        local_content_hash=ds._compute_hash(SAMPLE_OKF),
        feishu_modified_time="2026-07-15T09:00:00+08:00",
        sync_direction=SyncDirection.IN_SYNC.value,
    ))

    # --- Step 1: detect_feishu_edits flags the doc ---
    items = ds.detect_feishu_edits()
    assert len(items) == 1
    assert items[0].node_token == "nt_conflict"
    assert items[0].action_needed == "pull"

    # --- Step 2: pull_from_feishu (conflict -> backup + overwrite) ---
    result = ds.pull_from_feishu("nt_conflict")
    assert result.success is True
    assert result.action == "pulled"

    # --- Backup created in .conflicts/ ---
    conflicts_dir = os.path.join(bundle, ".conflicts")
    assert os.path.isdir(conflicts_dir)
    backups = [
        f for f in os.listdir(conflicts_dir)
        if f.startswith("nt_conflict_") and f.endswith(".md")
    ]
    assert len(backups) == 1
    backup_path = os.path.join(conflicts_dir, backups[0])
    backup_content = open(backup_path, "r", encoding="utf-8").read()
    # Backup contains the OLD local content (with the local edit).
    assert "本地修改" in backup_content
    assert "5 个核心限界上下文" in backup_content

    # --- log.md has an entry ---
    log_path = os.path.join(conflicts_dir, "log.md")
    assert os.path.isfile(log_path)
    log_content = open(log_path, "r", encoding="utf-8").read()
    assert "nt_conflict" in log_content
    assert "Conflict" in log_content
    assert backups[0] in log_content

    # --- Local file overwritten with Feishu content ---
    new_content = open(local_file, "r", encoding="utf-8").read()
    assert "飞书端更新后的内容" in new_content
    assert "本地修改" not in new_content
    # Frontmatter preserved.
    assert new_content.startswith("---")
    assert "type: Meeting Minutes" in new_content

    # --- sync_state IN_SYNC after pull ---
    state = ds.get_doc_state("nt_conflict")
    assert state.sync_direction == SyncDirection.IN_SYNC.value
    assert state.local_content_hash == ds._compute_hash(new_content)


# ---------------------------------------------------------------------------
# Test 6: test_index_after_sync
# ---------------------------------------------------------------------------

def test_index_after_sync(tmp_path):
    """Full pipeline: write -> index -> search -> sync -> search still works.

    Verifies the integration between QueryEngine and DualStorage in the same
    bundle:
    1. Write an OKF file to the bundle.
    2. Index it via QueryEngine.update_index.
    3. Search finds the document.
    4. Sync to Feishu (mocked) -- sync_state is updated.
    5. Search STILL finds the document (sync did not corrupt the local file
       or the index).
    6. The document's feishu_node_token is now tracked in sync_state.
    """
    bundle = str(tmp_path)
    mock_wc = _make_mock_wiki_connector(create_return="nt_indexed")
    ds = DualStorage(bundle, wiki_connector=mock_wc)
    engine = QueryEngine(bundle)

    # --- Step 1: write OKF ---
    okf_rel = "projects/platform/arch-review.md"
    okf_abs = _write_okf(bundle, okf_rel, SAMPLE_OKF)

    # --- Step 2: index ---
    engine.update_index(okf_abs)

    # --- Step 3: search finds it (search for "DDD", an ASCII term in body) ---
    result1 = engine.search("DDD", top_n=10, deep_read=False)
    assert isinstance(result1, SearchResult)
    assert result1.total_found >= 1, \
        "search should find the indexed document"
    paths = {m.local_path for m in result1.matches}
    assert okf_rel in paths, \
        f"search should include {okf_rel}, got {paths}"
    match1 = next(m for m in result1.matches if m.local_path == okf_rel)
    assert match1.title == "架构评审会议"
    assert match1.doc_type == "Meeting Minutes"

    # --- Step 4: sync to Feishu (mocked) ---
    sync_result = ds.sync_to_feishu(okf_rel, SAMPLE_OKF)
    assert sync_result.success is True
    assert sync_result.action == "created"
    assert sync_result.node_token == "nt_indexed"

    # sync_state now tracks the document.
    state = ds.load_state()
    assert "nt_indexed" in state["docs"]
    entry = SyncState.from_dict(state["docs"]["nt_indexed"])
    assert entry.local_path == okf_rel
    assert entry.sync_direction == SyncDirection.IN_SYNC.value

    # --- Step 5: search STILL finds it (sync did not touch the local file) ---
    result2 = engine.search("DDD", top_n=10, deep_read=False)
    assert result2.total_found >= 1, \
        "search should still find the document after sync"
    paths2 = {m.local_path for m in result2.matches}
    assert okf_rel in paths2, \
        f"search should still include {okf_rel} after sync, got {paths2}"

    # --- Step 6: deep_read returns the full content (file intact) ---
    result3 = engine.search("DDD", top_n=10, deep_read=True)
    assert result3.total_found >= 1
    match3 = next(m for m in result3.matches if m.local_path == okf_rel)
    assert match3.full_content is not None
    assert "微服务架构拆分方案" in match3.full_content
    assert "DDD 领域驱动设计" in match3.full_content
