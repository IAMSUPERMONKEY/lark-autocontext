"""Migration tool: upgrade from folder-based config to wiki-based config.

Usage:
    python scripts/migrate_to_wiki.py --space-id <space_id> --raw-node <node_token> --agent-node <node_token>

What it does:
1. Reads existing config.json
2. Backs up to config.json.backup_<timestamp>
3. Adds wiki section with space_id, raw_node_token, agent_node_token
4. Preserves all existing fields (bundle_path, identity, feishu, etc.)
5. Writes updated config.json
6. Prints next steps for the user
"""
import json
import os
import sys
import argparse
from datetime import datetime


def migrate_config(config_path, space_id, raw_node_token, agent_node_token):
    """Migrate config.json from folder mode to wiki mode.

    Args:
        config_path: Path to config.json
        space_id: Feishu Wiki Space ID
        raw_node_token: Node token for the raw docs area
        agent_node_token: Node token for the Agent maintenance area

    Returns:
        dict: {"success": bool, "backup_path": str, "message": str}
    """
    # Read existing config
    if not os.path.exists(config_path):
        return {"success": False, "backup_path": "", "message": f"Config not found: {config_path}"}

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    # Backup
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{config_path}.backup_{timestamp}"
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    # Add wiki section (preserve existing wiki fields if any)
    wiki = config.get("wiki", {})
    wiki["space_id"] = space_id
    wiki["raw_node_token"] = raw_node_token
    wiki["agent_node_token"] = agent_node_token
    config["wiki"] = wiki

    # Write updated config
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    return {
        "success": True,
        "backup_path": backup_path,
        "message": f"Config migrated. Backup saved to {backup_path}"
    }


def main():
    parser = argparse.ArgumentParser(
        description="Migrate lark-autocontext from folder mode to wiki mode"
    )
    parser.add_argument("--space-id", required=True, help="Feishu Wiki Space ID")
    parser.add_argument("--raw-node", required=True, help="Node token for raw docs area")
    parser.add_argument("--agent-node", required=True, help="Node token for Agent maintenance area")
    parser.add_argument("--config", default=None, help="Path to config.json (default: scripts/config.json)")

    args = parser.parse_args()

    if args.config is None:
        config_path = os.path.join(os.path.dirname(__file__), "config.json")
    else:
        config_path = args.config

    result = migrate_config(config_path, args.space_id, args.raw_node, args.agent_node)

    if result["success"]:
        print(f"[migrate] {result['message']}")
        print()
        print("Next steps:")
        print("  1. Run onboarding to verify: python scripts/onboarding.py")
        print("  2. Rebuild search index: python scripts/query_engine.py rebuild")
        print("  3. Test wiki scan: python scripts/scanner.py --wiki")
        print("  4. Test auto-sync: python scripts/auto_sync.py list-only")
    else:
        print(f"[migrate] ERROR: {result['message']}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
