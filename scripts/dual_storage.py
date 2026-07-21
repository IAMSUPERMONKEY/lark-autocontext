"""DualStorage: bidirectional sync coordinator between local bundle and Feishu Wiki.

Manages sync_state.json — per-document sync tracking with conflict detection.
Task 5 delivers state management only; sync operations added in Tasks 6-7.
"""
from __future__ import annotations

import json
import os
import hashlib
import logging
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime

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


class DualStorage:
    """Bidirectional sync coordinator between local bundle and Feishu Wiki.

    Manages sync_state.json with atomic writes and corruption recovery.
    Sync operations (sync_to_feishu, pull_from_feishu) added in Tasks 6-7.

    Args:
        bundle_path: Path to the local bundle directory.
        wiki_connector: WikiConnector instance (used in Tasks 6-7, not needed for Task 5).
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
