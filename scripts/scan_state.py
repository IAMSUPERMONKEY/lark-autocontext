"""
Append-only scan state helpers for Context Wizard auto discovery.

The state file is JSONL so interrupted scans can resume from the last durable
event without rewriting the whole file on every candidate transition.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from hashlib import sha1
from typing import Any, Dict, Iterable, List, Optional


REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
DEFAULT_STATE_DIR = os.path.join(REPO_ROOT, ".context_wizard")
DEFAULT_STATE_PATH = os.path.join(DEFAULT_STATE_DIR, "scan_state.jsonl")
DEFAULT_CURSOR_PATH = os.path.join(DEFAULT_STATE_DIR, "backfill_cursor.json")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_state_dir(path: str = DEFAULT_STATE_PATH) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)


def stable_id(*parts: Any) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return sha1(raw.encode("utf-8")).hexdigest()[:16]


def candidate_id(candidate: Dict[str, Any]) -> str:
    if candidate.get("id"):
        return candidate["id"]
    if candidate.get("token") or candidate.get("url"):
        return stable_id(candidate.get("doc_type"), candidate.get("token"), candidate.get("url"))
    return stable_id(candidate.get("doc_type"), candidate.get("title"))


def append_event(record: Dict[str, Any], state_path: str = DEFAULT_STATE_PATH) -> Dict[str, Any]:
    ensure_state_dir(state_path)
    event = dict(record)
    event["id"] = candidate_id(event)
    event.setdefault("last_seen_at", now_iso())
    event["_event_at"] = now_iso()
    with open(state_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return event


def iter_events(state_path: str = DEFAULT_STATE_PATH) -> Iterable[Dict[str, Any]]:
    if not os.path.exists(state_path):
        return
    with open(state_path, "r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                yield {
                    "id": f"corrupt-{line_no}",
                    "status": "failed",
                    "reason": "corrupt state line",
                    "last_error": f"Could not parse state line {line_no}",
                }
                continue
            yield event


def latest_records(state_path: str = DEFAULT_STATE_PATH) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for event in iter_events(state_path) or []:
        event_id = event.get("id")
        if event_id:
            latest[event_id] = event
    return latest


def latest_list(
    state_path: str = DEFAULT_STATE_PATH,
    status: Optional[str] = None,
    lane: Optional[str] = None,
) -> List[Dict[str, Any]]:
    records = list(latest_records(state_path).values())
    if status:
        records = [record for record in records if record.get("status") == status]
    if lane:
        records = [record for record in records if record.get("lane") == lane]
    return sorted(records, key=lambda record: record.get("last_seen_at", ""), reverse=True)


def update_record(
    record: Dict[str, Any],
    changes: Dict[str, Any],
    state_path: str = DEFAULT_STATE_PATH,
) -> Dict[str, Any]:
    updated = dict(record)
    updated.update(changes)
    return append_event(updated, state_path=state_path)


def load_cursor(cursor_path: str = DEFAULT_CURSOR_PATH) -> Dict[str, Any]:
    if not os.path.exists(cursor_path):
        return {}
    try:
        with open(cursor_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}


def save_cursor(cursor: Dict[str, Any], cursor_path: str = DEFAULT_CURSOR_PATH) -> None:
    directory = os.path.dirname(cursor_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    payload = dict(cursor)
    payload["updated_at"] = now_iso()
    with open(cursor_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)


def compact_state(state_path: str = DEFAULT_STATE_PATH) -> int:
    latest = latest_records(state_path)
    ensure_state_dir(state_path)
    tmp_path = f"{state_path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        for record in sorted(latest.values(), key=lambda item: item.get("id", "")):
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(tmp_path, state_path)
    return len(latest)
