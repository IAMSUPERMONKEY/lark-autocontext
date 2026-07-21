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
from wiki_connector import (okf_to_feishu_content, feishu_to_okf_body, _parse_frontmatter)

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


@dataclass
class SyncItem:
    """Represents a document that needs sync action.

    Produced by :meth:`DualStorage.detect_feishu_edits` for each Agent-area
    document whose Feishu ``modified_time`` is newer than the recorded
    ``feishu_modified_time`` in sync_state, or for documents that exist on
    Feishu but have no local mapping yet.

    Attributes:
        node_token: Feishu wiki node token.
        local_path: relative path of the local OKF file (empty if no local
            mapping exists yet -- the document is new on the Feishu side).
        feishu_modified_time: the current ``modified_time`` reported by Feishu
            (ISO 8601 or Unix timestamp string).
        action_needed: ``"pull"`` when the document is known locally and
            Feishu is newer; ``"unknown"`` when the document has no local
            mapping yet (caller decides whether to create one).
    """
    node_token: str
    local_path: str        # empty if no local mapping yet
    feishu_modified_time: str
    action_needed: str     # "pull" (feishu_newer) or "unknown" (new in feishu)


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

    # ------------------------------------------------------------------
    # Task 7: Feishu -> local pull flow
    # ------------------------------------------------------------------

    def detect_feishu_edits(self) -> list:
        """Detect which Agent-area docs have been edited on Feishu side.

        Flow:
        1. ``wiki_connector.list_agent_docs()`` -> all Agent area docs
           (``list[DocInfo]``).
        2. For each DocInfo, check sync_state:
           - If sync_state has this node_token AND recorded
             ``feishu_modified_time`` < ``doc.modified_time`` -> feishu_newer
             (``action_needed="pull"``).
           - If sync_state doesn't have this node_token -> new doc in Feishu
             (``action_needed="unknown"``).
        3. Return a list of :class:`SyncItem` for docs needing pull.

        Returns an empty list if ``wiki_connector`` is None (no Feishu I/O
        possible). String comparison of same-format timestamps is used; both
        sides are ISO 8601 or Unix timestamp strings of consistent length, so
        lexicographic order matches chronological order.
        """
        if self.wiki_connector is None:
            return []

        docs = self.wiki_connector.list_agent_docs()
        state = self.load_state()
        state_docs = state.get("docs", {})

        items: list = []
        for doc in docs:
            node_token = doc.node_token
            if node_token in state_docs:
                recorded_time = state_docs[node_token].get(
                    "feishu_modified_time", ""
                )
                if doc.modified_time > recorded_time:
                    items.append(SyncItem(
                        node_token=node_token,
                        local_path=state_docs[node_token].get(
                            "local_path", ""
                        ),
                        feishu_modified_time=doc.modified_time,
                        action_needed="pull",
                    ))
            else:
                # New on the Feishu side -- no local mapping yet.
                items.append(SyncItem(
                    node_token=node_token,
                    local_path="",
                    feishu_modified_time=doc.modified_time,
                    action_needed="unknown",
                ))
        return items

    def pull_from_feishu(self, node_token: str) -> SyncResult:
        """Pull Feishu edits back to local bundle.

        Flow (spec section 3.2 "拉取流：飞书 → 本地"):
        1. Get sync_state for this node_token.
        2. ``wiki_connector.fetch_doc_content(node_token)`` -> Feishu content.
        3. ``feishu_to_okf_body(content)`` -> cleaned OKF body (without
           frontmatter; the emoji metadata header is stripped).
        4. Read the existing local file (if it exists) and extract the raw
           YAML frontmatter block so it can be re-attached to the new body
           (human edits on Feishu never touch frontmatter).
        5. Reconstruct OKF: existing frontmatter + new body.
        6. Conflict detection: compare sync_state's ``local_content_hash``
           with the actual file hash.
           - Match -> safe overwrite (local unchanged since last sync).
           - Mismatch -> conflict! Backup local, then overwrite (Feishu wins,
             spec section 3.2 "冲突解决策略").
        7. Write the OKF file to ``local_path``.
        8. Update sync_state: new hash, ``feishu_modified_time``,
           ``sync_direction=in_sync``.
        9. Return :class:`SyncResult`.

        Atomicity (spec section 3.2 "原子性保证"): the local file is
        overwritten only after the Feishu fetch succeeds; sync_state is
        updated only after the local write succeeds. On fetch failure,
        sync_state is left untouched so the document stays
        ``feishu_newer`` and is retried next time.

        Args:
            node_token: Feishu wiki node token to pull.

        Returns:
            A :class:`SyncResult`. ``success=False`` with ``error`` set when
            there is no local mapping or the Feishu fetch fails.

        Raises:
            RuntimeError: if ``self.wiki_connector`` is None (misconfigured).
        """
        if self.wiki_connector is None:
            raise RuntimeError("wiki_connector not configured")

        existing = self.get_doc_state(node_token)
        if existing is None or not existing.local_path:
            return SyncResult(
                success=False,
                error=f"no local mapping for node_token {node_token!r}",
            )

        # 1. Fetch Feishu content.
        try:
            feishu_content = self.wiki_connector.fetch_doc_content(node_token)
        except Exception as e:  # noqa: BLE001 - report any Feishu API failure
            logger.error(
                "pull_from_feishu: fetch failed for %s: %s", node_token, e
            )
            # Do NOT update sync_state -- stays feishu_newer for retry.
            return SyncResult(
                success=False, action="failed", error=str(e)
            )

        # 2. Convert Feishu content -> OKF body (header stripped, cleaned).
        new_body = feishu_to_okf_body(feishu_content)

        # 3. Read existing local file (if it exists) and extract the raw
        #    frontmatter block so we can re-attach it to the new body.
        local_file = os.path.join(self.bundle_path, existing.local_path)
        old_content = ""
        if os.path.exists(local_file):
            with open(local_file, "r", encoding="utf-8") as f:
                old_content = f.read()

        frontmatter_block = ""
        if old_content.startswith("---"):
            lines = old_content.split("\n")
            closing_idx = None
            for i in range(1, len(lines)):
                if lines[i].strip() == "---":
                    closing_idx = i
                    break
            if closing_idx is not None:
                frontmatter_block = "\n".join(lines[:closing_idx + 1])

        # 4. Reconstruct OKF: frontmatter + new body.
        if frontmatter_block:
            new_okf = frontmatter_block + "\n\n" + new_body
        else:
            new_okf = new_body

        # 5. Conflict detection: compare recorded hash with actual file hash.
        actual_hash = self._compute_hash(old_content) if old_content else ""
        recorded_hash = existing.local_content_hash
        if old_content and actual_hash != recorded_hash:
            # Conflict! Backup local before overwriting (Feishu wins).
            self._backup_conflict(
                node_token, existing.local_path, old_content
            )

        # 6. Write new OKF content to local file.
        os.makedirs(os.path.dirname(local_file) or ".", exist_ok=True)
        with open(local_file, "w", encoding="utf-8") as f:
            f.write(new_okf)

        # 7. Update sync_state with new hash and in_sync direction.
        new_hash = self._compute_hash(new_okf)
        now = datetime.now().isoformat()
        updated_state = SyncState(
            local_path=existing.local_path,
            feishu_node_token=node_token,
            feishu_url=existing.feishu_url,
            local_content_hash=new_hash,
            feishu_modified_time=now,
            local_modified_time=now,
            sync_direction=SyncDirection.IN_SYNC.value,
            last_sync_at=now,
        )
        self.update_doc_state(node_token, updated_state)

        # 8. Return success result.
        return SyncResult(
            success=True,
            action="pulled",
            node_token=node_token,
            feishu_url=existing.feishu_url,
        )

    def _backup_conflict(self, node_token: str, local_path: str,
                         old_content: str) -> str:
        """Backup local content to ``.conflicts/`` directory on conflict.

        Creates ``bundle/.conflicts/`` (if needed), writes a backup file named
        ``{node_token}_{timestamp}.md`` containing ``old_content``, and
        appends a structured entry to ``log.md`` in the same directory.

        Args:
            node_token: Feishu node token of the conflicting document.
            local_path: relative local path of the document (for the log).
            old_content: the local file content to back up.

        Returns:
            The absolute path of the backup file that was written.
        """
        conflicts_dir = os.path.join(self.bundle_path, ".conflicts")
        os.makedirs(conflicts_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"{node_token}_{timestamp}.md"
        backup_path = os.path.join(conflicts_dir, backup_filename)

        with open(backup_path, "w", encoding="utf-8") as f:
            f.write(old_content)

        log_path = os.path.join(conflicts_dir, "log.md")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(
                f"\n## Conflict {timestamp}\n"
                f"- node_token: {node_token}\n"
                f"- local_path: {local_path}\n"
                f"- backup: {backup_filename}\n"
            )

        return backup_path
