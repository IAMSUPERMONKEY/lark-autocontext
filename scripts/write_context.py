"""
Write business context to the Feishu Base.
Takes JSON data and upserts it into the table.
"""
import sys
import json
import os

# Cross-platform: Windows console defaults to GBK, force UTF-8 for emoji output
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from datetime import datetime
from cli import LarkCLI

def main():
    # Usage: python write_context.py '<json>' [app_token] [table_id]
    # If app_token/table_id missing, try config.json
    
    if len(sys.argv) < 2:
        print("Usage: python write_context.py '<json_data>' [app_token] [table_id]")
        sys.exit(1)

    data = json.loads(sys.argv[1])
    
    cli = LarkCLI()
    arg2 = sys.argv[2] if len(sys.argv) > 2 else None
    arg3 = sys.argv[3] if len(sys.argv) > 3 else None
    
    # Smart argument parsing:
    # If arg2 starts with "tbl", it's likely a table_id (and app_token is from config).
    # If arg2 is long alphanumeric but not starting with "tbl", it might be app_token.
    
    if arg2 and arg2.startswith("tbl"):
        app_token = cli.get_base_token()
        table_id = arg2
    else:
        app_token = arg2
        table_id = arg3
        if not app_token:
            app_token = cli.get_base_token()
    
    if not app_token:
        print("❌ 未配置 Base Token。请先运行以下命令初始化：")
        print("   python scripts/init_base.py")
        sys.exit(1)
    
    if not table_id:
        print("❌ 未找到项目表。请先确保项目表已创建，或提供 table_id 参数。")
        sys.exit(1)
    
    # Map new schema
    mapping = {
        "实体名称": data.get("entity_name"),
        "实体类型": data.get("entity_type"),
        "文档类型": data.get("doc_type"),
        "核心结论": data.get("core_conclusion"),
        "关键时间": data.get("key_time"),
        "涉及人员": data.get("people"),
        "标签": data.get("tags"),
        "关联文档": data.get("source_link"),
        "文档 Token": data.get("doc_token"),
        "最后更新": datetime.now().strftime("%Y-%m-%d"),
        "状态": data.get("record_status") or "有效",
        "扫描分数": data.get("scan_score"),
        "扫描原因": data.get("scan_reason"),
        "入库方式": data.get("ingest_method") or ("自动扫描" if data.get("candidate_id") else "手动保存"),
        "候选 ID": data.get("candidate_id"),
        "主题表": data.get("topic_name") or data.get("project_name"),
    }
    # Clean None values
    record_data = {k: v for k, v in mapping.items() if v is not None}
    
    # Auto-fill source link if missing but token exists
    if record_data.get("文档 Token") and not record_data.get("关联文档"):
        token = record_data["文档 Token"]
        if token != "N/A" and not token.startswith("TEST_"):
            record_data["关联文档"] = f"https://your-tenant.feishu.cn/docx/{token}"
    
    print(f"📤 Creating record in Base {app_token}, Table {table_id}...")
    output = cli.run(["base", "+record-upsert", "--base-token", app_token, "--table-id", table_id,
                      "--json", json.dumps(record_data, ensure_ascii=False)])
    print("✅ Done.")
    print(output)

if __name__ == "__main__":
    main()
