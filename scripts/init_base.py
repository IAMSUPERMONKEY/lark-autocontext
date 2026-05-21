"""
Initialize the Feishu Base for Business Context Engine.
Creates the Base, Table, Fields, Views (Kanban, Gallery), and Dashboard.
"""
import json
import os
import subprocess
import sys

# Cross-platform: Windows console defaults to GBK, force UTF-8 for emoji output
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from cli import LarkCLI
from context_schema import CONTEXT_FIELDS, ensure_context_fields

def parse_json_output(output):
    """Parse JSON output from lark-cli."""
    try:
        return json.loads(output)
    except:
        return None

def run_init(base_name="业务上下文引擎", folder_token=None):
    cli = LarkCLI()
    
    print(f"🚀 Creating Base: {base_name}...")
    try:
        # Create Base
        base_output = cli.create_base(base_name)
        base_data = parse_json_output(base_output)
        if base_data and base_data.get("data", {}).get("base", {}).get("base_token"):
            app_token = base_data["data"]["base"]["base_token"]
            print(f"✅ Base created: {app_token}")
        else:
            print(f"❌ Failed to parse app_token. Output: {base_output}")
            return

        print("📋 Creating Table 'Context'...")
        table_output = cli.create_table(app_token, "业务上下文")
        table_data = parse_json_output(table_output)
        if table_data and table_data.get("data", {}).get("table", {}).get("id"):
            table_id = table_data["data"]["table"]["id"]
            print(f"✅ Table created: {table_id}")
        else:
            print(f"❌ Failed to parse table_id. Output: {table_output}")
            return

        print("🔧 Configuring Fields...")
        print(f"   - Ensuring {len(CONTEXT_FIELDS)} fields...")
        ensure_context_fields(cli, app_token, table_id)

        print("📊 Creating Views...")
        cli.create_view(app_token, table_id, "项目看板", "kanban")
        cli.create_view(app_token, table_id, "画册视图", "gallery")

        # Auto-generate config.json before optional dashboard setup so a
        # non-critical dashboard failure does not lose the created Base token.
        config_path = os.path.join(os.path.dirname(__file__), "config.json")
        with open(config_path, 'w') as f:
            json.dump({
                "base_token": app_token,
                "description": "业务上下文引擎 - 多维表格 Base Token",
                "last_updated": __import__('datetime').datetime.now().strftime("%Y-%m-%d")
            }, f, indent=2, ensure_ascii=False)
        print(f"✅ config.json saved: {config_path}")

        # Create Dashboard
        print("📊 Creating project dashboard...")
        dashboard_file = os.path.join(os.path.dirname(__file__), "create_dashboard.py")
        result = subprocess.run(
            [sys.executable, dashboard_file, app_token, "业务上下文"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", shell=(sys.platform == "win32")
        )
        if result.stdout:
            print(result.stdout)
        if result.stderr and "already exists" not in result.stderr.lower():
            print(f"[WARN] {result.stderr.strip()}")
        if result.returncode != 0:
            print("[WARN] Dashboard creation skipped (non-critical).")

        print("✨ Initialization Complete!")
        print(f"🔗 Base URL: https://your-tenant.feishu.cn/base/{app_token}")
        print("   （请将 your-tenant 替换为实际的飞书租户域名）")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    run_init()
