"""DualStorage: bidirectional sync coordinator between local bundle and Feishu Wiki.

Manages sync_state.json — per-document sync tracking with conflict detection.
Task 5 delivered state management; Task 6 adds the local -> Feishu push flow
(``sync_to_feishu``). The Feishu -> local pull flow is added in Task 7.
"""
from __future__ import annotations

import json
import os
import hashlib
import logging
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime

# OKF <-> Feishu docx conversion helpers (Task 4). wiki_connector imports
# scanner (stdlib-only cli); there is no circular import back to dual_storage.
from wiki_connector import okf_to_feishu_content, _parse_frontmatter

logger = logging.getLogger(__name__)


class SyncDirection(Enum):
    """Sync direction/state for a document."""
    IN_SYNC = "in_sync"           # both sides match
    LOCAL_NEWER = "local_newer"   # local has updates, needs push to Feishu
    FEISHU_NEWER = "feishu_newer"  # Feishu has edits, needs pull to local
    CONFLICT = "conflict"         # both sides modified, needs resolution


@dataclass
class SyncState:
    """Per-document sync state entry."""
    local_path: str
    feishu_node_token: str = ""
    feishu_url: str = ""
    local_content_hash: str = ""
    feishu_modified_time: str = ""
    local_modified_time: str = ""
    sync_direction: str = "local_newer"  # SyncDirection value string
    last_sync_at: str = ""

    def to_dict(self) -> dict:
        """Serialize to dict for JSON storage."""
        return {
            "local_path": self.local_path,
            "feishu_node_token": self.feishu_node_token,
            "feishu_url": self.feishu_url,
            "local_content_hash": self.local_content_hash,
            "feishu_modified_time": self.feishu_modified_time,
            "local_modified_time": self.local_modified_time,
            "sync_direction": self.sync_direction,
            "last_sync_at": self.last_sync_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SyncState":
        """Deserialize from dict."""
        return cls(
            local_path=d.get("local_path", ""),
            feishu_node_token=d.get("feishu_node_token", ""),
            feishu_url=d.get("feishu_url", ""),
            local_content_hash=d.get("local_content_hash", ""),
            feishu_modified_time=d.get("feishu_modified_time", ""),
            local_modified_time=d.get("local_modified_time", ""),
            sync_direction=d.get("sync_direction", "local_newer"),
            last_sync_at=d.get("last_sync_at", ""),
        )


@dataclass
class SyncResult:
    """Result of a sync operation."""
    success: bool
    action: str = ""        # "created", "updated", "failed"
    node_token: str = ""    # Feishu node token (if successful)
    feishu_url: str = ""
    error: str = ""         # error message if failed


class DualStorage:
    """Bidirectional sync coordinator between local bundle and Feishu Wiki.

    Manages sync_state.json with atomic writes and corruption recovery.
    Sync operations: ``sync_to_feishu`` (Task 6, local -> Feishu push) and
    ``pull_from_feishu`` (Task 7, Feishu -> local pull).

    Args:
        bundle_path: Path to the local bundle directory.
        wiki_connector: WikiConnector instance (required for sync_to_feishu;
            not needed for the pure state-management methods of Task 5).
    """

    def __init__(self, bundle_path: str, wiki_connector=None):
        self.bundle_path = bundle_path
        self.wiki_connector = wiki_connector
        self.state_path = os.path.join(bundle_path, ".sync_state.json")

    def load_state(self) -> dict:
        """Load sync_state.json. Returns {"docs": {}} if missing or corrupted.

        Corruption recovery: if JSON parse fails, log warning and return
        empty state (caller can rebuild via full comparison).
        """
        if not os.path.exists(self.state_path):
            return {"docs": {}}
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logging.warning(f"sync_state.json corrupted, starting fresh: {e}")
            return {"docs": {}}

    def save_state(self, state: dict) -> None:
        """Atomically write sync_state.json (write to .tmp, then os.replace).

        Follows the same pattern as auto_sync.save_state.
        """
        os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.state_path)

    def _compute_hash(self, content: str) -> str:
        """Compute SHA256 hash of content string.

        Returns: "sha256:<hex_digest>"
        """
        return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()

    def get_doc_state(self, key: str) -> SyncState | None:
        """Get sync state for a single document by key (feishu_node_token or local_path).

        Returns None if not found.
        """
        state = self.load_state()
        docs = state.get("docs", {})
        # Try by key directly
        if key in docs:
            return SyncState.from_dict(docs[key])
        # Try by local_path
        for k, v in docs.items():
            if v.get("local_path") == key:
                return SyncState.from_dict(v)
        return None

    def update_doc_state(self, key: str, sync_state: SyncState) -> None:
        """Update or insert a document's sync state. Key is feishu_node_token."""
        state = self.load_state()
        state.setdefault("docs", {})[key] = sync_state.to_dict()
        self.save_state(state)

    def remove_doc_state(self, key: str) -> None:
        """Remove a document's sync state entry."""
        state = self.load_state()
        state.get("docs", {}).pop(key, None)
        self.save_state(state)

    # ------------------------------------------------------------------
    # Task 6: local -> Feishu push flow
    # ------------------------------------------------------------------

    def sync_to_feishu(self, okf_path: str, okf_content: str) -> SyncResult:
        """Push local OKF document to Feishu Wiki (Agent maintenance area).

        Flow (spec section 3.2 "写入流：本地 → 飞书"):
        1. Parse frontmatter to get title.
        2. Convert OKF -> Feishu content (okf_to_feishu_content).
        3. Compute local content hash.
        4. Check sync_state: does this local_path already have a
           feishu_node_token?
           - Yes -> update_doc(node_token, feishu_content)
           - No  -> create_doc(agent_node_token, title, feishu_content)
                    -> get new node_token
        5. On success: update sync_state (hash, node_token,
           sync_direction=in_sync).
        6. On failure: return SyncResult(success=False), do NOT update
           sync_state (so it stays local_newer for retry).

        Atomicity (spec section 3.2 "原子性保证"): sync_state is only written
        AFTER a successful Feishu API call. A failed push leaves sync_state
        untouched, so the document remains pending and is retried next time.

        Args:
            okf_path: relative path of the OKF file in bundle (e.g.
                "projects/demo/meeting-minutes/2026-06-20.md").
            okf_content: full OKF Markdown content (with frontmatter).

        Returns:
            A :class:`SyncResult` describing the outcome.

        Raises:
            RuntimeError: if ``self.wiki_connector`` is None (misconfigured).
        """
        if self.wiki_connector is None:
            raise RuntimeError("wiki_connector not configured")

        # 1. Parse frontmatter -> title (fall back to the path itself).
        fm, _body = _parse_frontmatter(okf_content)
        title = fm.get("title", okf_path)

        # 2. Convert OKF -> Feishu-displayable content.
        feishu_content = okf_to_feishu_content(okf_content)

        # 3. Compute local content hash (over the full OKF content, including
        #    frontmatter, so any local edit is detected).
        local_hash = self._compute_hash(okf_content)

        # 4. Decide create vs. update based on existing sync_state.
        existing = self.get_doc_state(okf_path)

        try:
            if existing is not None and existing.feishu_node_token:
                # Update the existing Feishu doc.
                node_token = existing.feishu_node_token
                self.wiki_connector.update_doc(node_token, feishu_content)
                action = "updated"
            else:
                # Create a new doc under the Agent maintenance root.
                node_token = self.wiki_connector.create_doc(
                    self.wiki_connector.agent_node_token, title, feishu_content
                )
                action = "created"
        except Exception as e:  # noqa: BLE001 - report any Feishu API failure
            logger.error(
                "sync_to_feishu: push failed for %s: %s", okf_path, e
            )
            # Do NOT update sync_state -- stays local_newer for retry.
            return SyncResult(
                success=False, action="failed", error=str(e)
            )

        # 5. On success: build feishu_url and persist the new sync_state.
        feishu_url = f"https://feishu.cn/wiki/{node_token}"
        now = datetime.now().isoformat()
        sync_state = SyncState(
            local_path=okf_path,
            feishu_node_token=node_token,
            feishu_url=feishu_url,
            local_content_hash=local_hash,
            feishu_modified_time="",  # filled by detect_feishu_edits later
            local_modified_time=now,
            sync_direction=SyncDirection.IN_SYNC.value,
            last_sync_at=now,
        )
        self.update_doc_state(node_token, sync_state)

        # 6. Return success result.
        return SyncResult(
            success=True,
            action=action,
            node_token=node_token,
            feishu_url=feishu_url,
        )
