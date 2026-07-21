"""Auto-Sync coordinator: state management + workflow steps."""
import os
import sys
import json
import argparse
import subprocess
from datetime import datetime, timezone

if sys.platform == "win32" and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Default paths (can be monkeypatched in tests)
PENDING_PATH = ".auto_sync/pending_changes.json"
STATE_PATH = ".auto_sync/state.json"
MAIN_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_state(state_path):
    """Load .state.json, returning a default skeleton when missing."""
    if not os.path.exists(state_path):
        return {"last_scan_at": "", "sources": {}, "stats": {}}
    with open(state_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state_path, state):
    """Atomically write state.json (write to .tmp, rename)."""
    os.makedirs(os.path.dirname(state_path) or ".", exist_ok=True)
    tmp = state_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, state_path)


def update_source_state(state, source_key, success, scan_at, error=None):
    """Update a single source's state entry."""
    src = state.setdefault("sources", {}).setdefault(source_key, {
        "last_scan_at": "",
        "last_success": True,
        "last_error": None,
        "consecutive_failures": 0,
    })
    if success:
        src["last_scan_at"] = scan_at
        src["last_success"] = True
        src["last_error"] = None
        src["consecutive_failures"] = 0
    else:
        src["last_success"] = False
        src["last_error"] = error or "unknown"
        src["consecutive_failures"] = src.get("consecutive_failures", 0) + 1


def cmd_list_only(args):
    """Scan all sources, write pending_changes.json. Does NOT update state.

    In wiki mode (config.json has wiki.space_id), uses WikiConnector.list_raw_docs()
    instead of scanner.list_changed().
    """
    state = load_state(STATE_PATH)
    scan_at = _now_iso()

    # Check for wiki mode
    use_wiki = False
    wiki_config = {}
    if os.path.exists(MAIN_CONFIG_PATH):
        try:
            with open(MAIN_CONFIG_PATH, "r", encoding="utf-8") as f:
                main_config = json.load(f)
            wiki_config = main_config.get("wiki", {})
            if wiki_config.get("space_id"):
                use_wiki = True
        except (json.JSONDecodeError, KeyError):
            pass

    if use_wiki:
        # Wiki mode: use WikiConnector to list changed raw docs
        from scanner import _get_wiki_connector
        conn = _get_wiki_connector()
        if conn is None:
            print("[auto_sync] wiki mode configured but connector creation failed")
            return 1

        # Get last scan time from state
        wiki_key = f"wiki:{wiki_config.get('space_id')}"
        since = state.get("sources", {}).get(wiki_key, {}).get("last_scan_at") or None

        try:
            docs = conn.list_raw_docs(since=since)
            all_changes = []
            for doc in docs:
                all_changes.append({
                    "doc_token": doc.node_token,
                    "node_token": doc.node_token,
                    "url": doc.url,
                    "title": doc.title,
                    "edited_time": doc.modified_time,
                    "source": wiki_key,
                    "source_type": "wiki_doc",
                })
            source_scans = {wiki_key: scan_at}
        except Exception as e:
            print(f"[auto_sync] wiki list_raw_docs failed: {e}")
            return 1
    else:
        # Legacy folder mode (existing code)
        from scanner import list_changed
        with open(args.config, "r", encoding="utf-8") as f:
            config = json.load(f)
        all_changes = []
        source_scans = {}
        for src in config.get("sources", []):
            key = src.get("key") or f"{src.get('type')}:{src.get('token', '')}"
            since = state.get("sources", {}).get(key, {}).get("last_scan_at") or None
            result = list_changed([src], since=since or "2000-01-01T00:00:00+08:00")
            all_changes.extend(result.get("changed", []))
            source_scans[key] = scan_at

    # Write pending_changes.json (same for both modes)
    os.makedirs(os.path.dirname(PENDING_PATH) or ".", exist_ok=True)
    with open(PENDING_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "scan_at": scan_at,
            "source_scans": source_scans,
            "changes": all_changes,
        }, f, ensure_ascii=False, indent=2)
    print(f"[auto_sync] {len(all_changes)} change(s) listed -> {PENDING_PATH}")
    return 0


def cmd_finalize(args):
    """Update state.json from pending, optionally git commit."""
    if not os.path.exists(PENDING_PATH):
        print("[auto_sync] no pending file; nothing to finalize")
        return 0
    with open(PENDING_PATH, "r", encoding="utf-8") as f:
        pending = json.load(f)
    state = load_state(STATE_PATH)
    for key, scan_at in pending.get("source_scans", {}).items():
        update_source_state(state, key, success=True, scan_at=scan_at)
    state["last_scan_at"] = pending.get("scan_at", _now_iso())
    save_state(STATE_PATH, state)
    os.remove(PENDING_PATH)
    if args.commit:
        subprocess.run(["git", "add", "bundle/", STATE_PATH], check=False)
        subprocess.run(
            ["git", "commit", "-m", f"chore(auto_sync): sync @ {state['last_scan_at']}"],
            check=False,
        )
    print("[auto_sync] finalized")
    return 0


def main():
    p = argparse.ArgumentParser(description="Auto-Sync coordinator")
    sub = p.add_subparsers(dest="mode", required=True)
    sub.add_parser("list-only").set_defaults(func=cmd_list_only)
    fin = sub.add_parser("finalize")
    fin.add_argument("--commit", action="store_true")
    fin.set_defaults(func=cmd_finalize)
    p.add_argument("--config", default="config.json")
    args = p.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
