"""
Shared Feishu Base schema for Context Wizard project tables.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple


CONTEXT_FIELDS: List[Tuple[str, str, Optional[List[str]], Optional[Dict[str, Any]]]] = [
    ("实体名称", "text", None, None),
    ("实体类型", "select", ["项目", "客户", "合作伙伴", "产品"], None),
    ("文档类型", "select", ["会议纪要", "需求文档", "复盘报告", "运营方案", "合作协议", "数据分析", "竞品调研", "其他"], None),
    ("核心结论", "text", None, None),
    ("关键时间", "text", None, None),
    ("涉及人员", "text", None, None),
    ("标签", "text", None, None),
    ("关联文档", "url", None, None),
    ("文档 Token", "text", None, None),
    ("最后更新", "datetime", None, None),
    ("状态", "select", ["有效", "待复核", "失效"], None),
    ("扫描分数", "number", None, {"type": "plain", "precision": 0}),
    ("扫描原因", "text", None, None),
    ("入库方式", "select", ["手动保存", "自动扫描", "用户批准", "手动重扫"], None),
    ("候选 ID", "text", None, None),
    ("主题表", "text", None, None),
]


def field_payload(
    name: str,
    field_type: str,
    options: Optional[List[str]] = None,
    style: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if field_type == "url":
        payload: Dict[str, Any] = {"name": name, "type": "text", "style": {"type": "url"}}
    else:
        payload = {"name": name, "type": field_type}
    if options:
        payload["multiple"] = False
        payload["options"] = [{"name": option} for option in options]
    if style:
        payload["style"] = style
    return payload


def list_field_names(cli: Any, app_token: str, table_id: str) -> List[str]:
    output = cli.run([
        "base",
        "+field-list",
        "--base-token",
        app_token,
        "--table-id",
        table_id,
        "--limit",
        "200",
    ])
    data = json.loads(output)
    fields = data.get("data", {}).get("fields", [])
    return [field.get("name", "") for field in fields if field.get("name")]


def ensure_context_fields(cli: Any, app_token: str, table_id: str) -> None:
    """Create missing Context Wizard fields on an existing or new project table."""
    try:
        existing_names = set(list_field_names(cli, app_token, table_id))
    except Exception as exc:
        print(f"[WARN] Could not list fields for schema check: {exc}")
        existing_names = set()

    for name, field_type, options, style in CONTEXT_FIELDS:
        if name in existing_names:
            continue
        payload = field_payload(name, field_type, options=options, style=style)
        cli.run([
            "base",
            "+field-create",
            "--base-token",
            app_token,
            "--table-id",
            table_id,
            "--json",
            json.dumps(payload, ensure_ascii=False),
        ])
