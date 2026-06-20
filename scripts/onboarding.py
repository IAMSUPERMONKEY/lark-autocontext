"""
Quick status check and guided setup for Business Context Wizard.
Run: python scripts/onboarding.py
"""
import json
import os
import sys
from datetime import datetime

# Cross-platform: Windows console defaults to GBK, force UTF-8 for emoji output
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from cli import LarkCLI

def check_status():
    """Check current setup status and guide the user."""
    cli = LarkCLI()
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    
    print("🧙 Business Context Wizard — 状态检查")
    print("=" * 50)
    
    # Check 1: config.json
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            config = json.load(f)
        print("✅ 配置文件: 存在")
        base_token = config.get("base_token")
        if base_token:
            print(f"✅ Base Token: {base_token[:10]}...")
        else:
            print("❌ Base Token: 为空")
            print("   → 运行 init_base.py 自动创建 Base")
            return
    else:
        print("❌ 配置文件: 不存在")
        print("   → 运行 init_base.py 自动创建")
        return
    
    # Check 2: lark-cli auth
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
    
    # Check 3: Base accessibility
    try:
        tables_output = cli.run(["base", "+table-list", "--base-token", base_token])
        tables_data = json.loads(tables_output)
        tables = tables_data.get("data", {}).get("tables", [])
        print(f"✅ Base 可访问: 已有 {len(tables)} 个项目表")
        for t in tables:
            print(f"   - {t.get('name', '?')} (ID: {t.get('id', '?')})")
    except Exception as e:
        err_msg = str(e)
        if len(err_msg) > 200:
            err_msg = err_msg[:200] + "..."
        print(f"❌ Base 无法访问: {err_msg}")
        print("   → 检查 token 是否过期，或重新运行 init_base.py")
    
    print()
    print("📌 **使用方式:**")
    print("  1. 发文档链接 + '帮我存一下' → 自动入库")
    print("  2. /context-wizard [链接] → 同上")
    print("  3. 'XX项目里关于XX的决策？' → 搜索回答")

if __name__ == "__main__":
    check_status()
