"""
Quick status check and guided setup for Lark AutoContext (OKF architecture).
Run: python scripts/onboarding.py
Run: python scripts/onboarding.py --quiet  (non-interactive, for Agent automation)
"""
import json
import os
import sys
import argparse

if sys.platform == "win32" and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from cli import LarkCLI


def _check_wiki_config(config: dict) -> list:
    """Check wiki configuration and return status lines.

    Returns a list of status strings. When ``wiki.space_id`` is set, reports
    the configured state plus WARN lines for any missing tokens. When
    ``space_id`` is absent/empty, reports folder mode.
    """
    lines = []
    wiki = config.get("wiki", {})
    if wiki.get("space_id"):
        lines.append("  [OK] Wiki mode: space_id configured")
        if wiki.get("raw_node_token"):
            lines.append("  [OK] Wiki raw_node_token configured")
        else:
            lines.append("  [WARN] Wiki raw_node_token not set")
        if wiki.get("agent_node_token"):
            lines.append("  [OK] Wiki agent_node_token configured")
        else:
            lines.append("  [WARN] Wiki agent_node_token not set")
    else:
        lines.append("  [INFO] Wiki mode: not configured (folder mode)")
    return lines


def _check_fts5() -> list:
    """Check SQLite FTS5 availability and return status lines.

    Probes an in-memory FTS5 virtual table creation. Returns an [OK] line
    on success or an [ERROR] line on failure.
    """
    import sqlite3
    lines = []
    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE test_fts5 USING fts5(content)")
        conn.close()
        lines.append("  [OK] SQLite FTS5 available")
    except Exception:
        lines.append("  [ERROR] SQLite FTS5 not available — search engine will not work")
    return lines


def _check_search_index(bundle_path: str) -> list:
    """Check for the search index database and return status lines."""
    lines = []
    index_dir = os.path.join(bundle_path, ".index")
    if os.path.exists(os.path.join(index_dir, "search.db")):
        lines.append("  [OK] Search index exists (.index/search.db)")
    else:
        lines.append("  [INFO] Search index not built yet (run: python scripts/query_engine.py rebuild)")
    return lines


def _check_sync_state(bundle_path: str) -> list:
    """Check for the sync state file and return status lines."""
    lines = []
    sync_state = os.path.join(bundle_path, ".sync_state.json")
    if os.path.exists(sync_state):
        lines.append("  [OK] Sync state exists (.sync_state.json)")
    else:
        lines.append("  [INFO] Sync state not initialized (will be created on first sync)")
    return lines


def check_status(quiet=False):
    """Check current setup status and guide the user."""
    cli = LarkCLI()
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    scan_config_path = os.path.join(os.path.dirname(__file__), "scan_config.json")

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bundle_path = os.path.join(project_root, "bundle")

    if not quiet:
        print("🧙 Lark AutoContext — 状态检查 (OKF 架构)")
        print("=" * 50)

    # Check 1: config.json
    if os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        if not quiet:
            print("✅ 配置文件: 存在")
            bundle_path_config = config.get("bundle_path", "./bundle")
            print(f"✅ Bundle 路径: {bundle_path_config}")
    else:
        if not quiet:
            print("❌ 配置文件: 不存在")
            print("   → 运行 init_bundle.py 自动创建")
        return False

    # Check 2: Bundle directory
    if os.path.exists(bundle_path):
        if not quiet:
            print("✅ OKF Bundle: 已初始化")
            projects_dir = os.path.join(bundle_path, "projects")
            if os.path.exists(projects_dir):
                projects = [d for d in os.listdir(projects_dir) if os.path.isdir(os.path.join(projects_dir, d))]
                print(f"   已有 {len(projects)} 个项目: {', '.join(projects) if projects else '(空)'}")
    else:
        if not quiet:
            print("❌ OKF Bundle: 未初始化")
            print("   → 运行: python scripts/init_bundle.py")
        return False

    # Check 3: scan_config.json
    if not quiet:
        if os.path.exists(scan_config_path):
            with open(scan_config_path, 'r', encoding='utf-8') as f:
                scan_config = json.load(f)
            sources = scan_config.get("sources", [])
            print(f"✅ 扫描配置: {len(sources)} 个数据源")
        else:
            print("⚠️  扫描配置: 不存在 (单文档保存仍可用，批量扫描需要配置)")
            print("   → 运行 python scripts/setup.py，粘贴飞书链接即可自动配置")

    # Check 4: lark-cli auth
    if not quiet:
        try:
            auth_output = cli.run(["auth", "status"], as_json=False)
            auth_data = json.loads(auth_output)
            note = auth_data.get("note", "")
            if "not logged in" in note.lower():
                print("⚠️  lark-cli: 未登录用户，仅可使用 bot 身份")
                print("   → 如需用户身份，运行: lark-cli auth login --recommend --no-wait")
            else:
                print("✅ lark-cli: 已认证")
        except Exception as e:
            print(f"⚠️  lark-cli: 检查失败 ({e})")

        # Check 5: Wiki config (Task 15)
        for line in _check_wiki_config(config):
            print(line)

        # Check 6: FTS5 availability (Task 15)
        for line in _check_fts5():
            print(line)

        # Check 7: Search index (Task 15)
        for line in _check_search_index(bundle_path):
            print(line)

        # Check 8: Sync state (Task 15)
        for line in _check_sync_state(bundle_path):
            print(line)

        print()
        print("📌 **使用方式:**")
        print("  1. '保存这个文档 <链接>' → 单文档提取 → AI分类 → OKF入库")
        print("  2. '扫描飞书文档' → 批量扫描 → AI分类 → OKF入库")
        print("  3. 'XX项目里关于XX的信息？' → 查询OKF Bundle")

    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Lark AutoContext onboarding check")
    parser.add_argument("--quiet", action="store_true",
                        help="Non-interactive mode for Agent automation")
    args = parser.parse_args()
    ok = check_status(quiet=args.quiet)
    sys.exit(0 if ok else 1)
