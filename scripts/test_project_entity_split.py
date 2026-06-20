#!/usr/bin/env python3
"""
Test: Verify project_name vs entity_name split.
Simulates extracting from a doc and ensuring the table uses project_name.
"""
import json
import subprocess
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from cli import LarkCLI

# Configuration
BASE_TOKEN = json.load(open(os.path.join(os.path.dirname(__file__), "config.json")))["base_token"]

# Test scenario: A new doc under the same project "lark-autocontext"
test_extraction = {
    "project_name": "lark-autocontext",  # Should reuse existing table
    "entity_name": "Q2 运营数据复盘",  # New entity
    "entity_type": "项目",
    "current_status": "数据分析",
    "risk_level": "🟡 中",
    "progress": 60,
    "priority": "高",
    "start_date": "2026-04-01",
    "end_date": "2026-06-30",
    "core_summary": "Q2 lark-autocontext 运营数据复盘，包含用户活跃度、商户转化率分析",
    "key_people": "张三, 李四",
    "doc_token": "TEST_Q2_OPS"
}

cli = LarkCLI()

print("=" * 60)
print("🧪 Testing Project/Entity Name Split")
print("=" * 60)

# Step 1: Get or create table using project_name
print(f"\n📋 Step 1: Get/Create table for project='{test_extraction['project_name']}'")
result = subprocess.run(
    ["python", os.path.join(os.path.dirname(__file__), "get_or_create_table.py"),
     BASE_TOKEN, test_extraction["project_name"]],
    capture_output=True, text=True
)
print(result.stdout.strip())

table_id = result.stdout.strip().split("\n")[-1]
print(f"✅ Table ID: {table_id}")

# Step 2: Write record
print(f"\n📝 Step 2: Writing entity '{test_extraction['entity_name']}' to table...")
data = {
    "实体名称": test_extraction["entity_name"],
    "实体类型": test_extraction["entity_type"],
    "当前状态": test_extraction["current_status"],
    "风险等级": test_extraction["risk_level"],
    "进度 (%)": test_extraction["progress"],
    "优先级": test_extraction["priority"],
    "开始日期": test_extraction["start_date"],
    "截止日期": test_extraction["end_date"],
    "核心摘要": test_extraction["core_summary"],
    "关键人员": test_extraction["key_people"],
    "文档 Token": test_extraction["doc_token"]
}

result = subprocess.run(
    ["python", os.path.join(os.path.dirname(__file__), "write_context.py"),
     json.dumps(data, ensure_ascii=False), BASE_TOKEN, table_id],
    capture_output=True, text=True
)
print(result.stdout)
if result.returncode != 0:
    print(f"❌ Error: {result.stderr}")
    sys.exit(1)

# Step 3: Verify table has 2 records now
print(f"\n🔍 Step 3: Verifying table contents...")
records = cli.run(["base", "+record-list", "--base-token", BASE_TOKEN, "--table-id", table_id])
data = json.loads(records)
total = data.get("data", {}).get("total", 0)
print(f"📊 Table '{test_extraction['project_name']}' now has {total} records")

if total >= 2:
    print("\n✅ TEST PASSED: Multiple entities under one project table!")
else:
    print(f"\n⚠️  Expected 2+ records, got {total}")

print("\n🔗 View: https://your-tenant.feishu.cn/base/" + BASE_TOKEN + "?table=" + table_id)
