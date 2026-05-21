"""
Search for a project context table and retrieve its records.
Returns a list of records (context history) for the specified project.
"""
import sys
import json
import os
from cli import LarkCLI

def search_project_context(app_token, project_name):
    cli = LarkCLI()
    
    # Auto-load config if token missing
    if not app_token:
        app_token = cli.get_base_token()
        if not app_token:
            return json.dumps({
                "error": "❌ 未配置 Base Token。请先运行: python scripts/init_base.py"
            }, ensure_ascii=False)
    
    # 1. Find the table ID by name
    output = cli.run(["base", "+table-list", "--base-token", app_token])
    data = json.loads(output)
    tables = data.get("data", {}).get("tables", [])
    
    target_table_id = None
    for t in tables:
        if project_name == t.get("name", ""):
            target_table_id = t["id"]
            break
    if len(project_name) >= 4:
        for t in tables:
            if target_table_id:
                break
            if project_name in t.get("name", ""):
                target_table_id = t["id"]
                break
            
    if not target_table_id:
        return json.dumps({"error": f"Project table '{project_name}' not found."}, ensure_ascii=False)

    # 2. Retrieve records from that table
    records_output = cli.run([
        "base", "+record-list",
        "--base-token", app_token,
        "--table-id", target_table_id,
        "--format", "json"
    ])
    records_data = json.loads(records_output)
    
    # Parse the shortcut command response format
    # Fields and data are parallel arrays
    fields = records_data.get("data", {}).get("fields", [])
    rows = records_data.get("data", {}).get("data", [])
    
    simple_records = []
    for row in rows:
        record = {}
        for i, field_name in enumerate(fields):
            if i < len(row):
                # Handle select field (array)
                val = row[i]
                if isinstance(val, list):
                    val = ", ".join(val)
                record[field_name] = val
        simple_records.append(record)

    return json.dumps({
        "project": project_name,
        "table_id": target_table_id,
        "context_history": simple_records
    }, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python search_context.py <project_name> [app_token]")
        sys.exit(1)
    
    project_name = sys.argv[1]
    app_token = sys.argv[2] if len(sys.argv) > 2 else None
    
    print(search_project_context(app_token, project_name))
