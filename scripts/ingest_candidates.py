#!/usr/bin/env python3
"""
Ingest or review candidates discovered by scan_drive.py.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from typing import Any, Dict, Iterable, List, Optional

from scan_state import DEFAULT_STATE_PATH, latest_list, latest_records, update_record
from score_candidates import (
    classify_candidate,
    extract_time_hints,
    infer_doc_type_from_title,
    infer_project_name,
    infer_topic_names,
    infer_tags,
)
from cli import LarkCLI


SCRIPT_DIR = os.path.dirname(__file__)


def run_script(script_name: str, args: List[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, os.path.join(SCRIPT_DIR, script_name)] + args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def source_for_candidate(candidate: Dict[str, Any]) -> str:
    if candidate.get("url"):
        return candidate["url"]
    token = candidate.get("token")
    doc_type = candidate.get("doc_type")
    if token and doc_type == "docx":
        return f"https://feishu.cn/docx/{token}"
    if token and doc_type == "doc":
        return f"https://feishu.cn/doc/{token}"
    if token and doc_type == "sheet":
        return f"https://feishu.cn/sheet/{token}"
    return token or candidate.get("title") or ""


def extract_raw_content(candidate: Dict[str, Any]) -> Dict[str, Any]:
    doc_type = candidate.get("doc_type", "")
    if doc_type not in {"docx", "doc", "sheet"}:
        return metadata_only_content(candidate, f"文档类型 {doc_type or '未知'} 暂不支持自动读取")
    source = source_for_candidate(candidate)
    if not source:
        return metadata_only_content(candidate, "候选缺少 URL 或 token，无法读取")
    if doc_type == "sheet" and "/sheet" not in source and "/sheets" not in source:
        return metadata_only_content(candidate, "Wiki 中的表格暂不自动读取")
    result = run_script("extract_data.py", [source])
    if result.returncode != 0:
        return metadata_only_content(candidate, result.stderr.strip() or result.stdout.strip() or "extract_data.py failed")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return metadata_only_content(candidate, f"extract_data.py returned invalid JSON: {exc}")
    if data.get("error"):
        return metadata_only_content(candidate, str(data["error"]))
    return data


def metadata_only_content(candidate: Dict[str, Any], reason: str) -> Dict[str, Any]:
    title = candidate.get("title") or "未命名文档"
    doc_type = candidate.get("doc_type") or "未知类型"
    url = candidate.get("url") or source_for_candidate(candidate)
    content = (
        f"{title}\n"
        f"自动扫描发现的 {doc_type} 资源。\n"
        f"读取状态：仅记录元数据。\n"
        f"原因：{reason}\n"
        f"链接：{url}"
    )
    return {
        "content": content,
        "doc_token": candidate.get("token") or "N/A",
        "metadata_only": True,
        "read_error": reason,
    }


def meaningful_excerpt(content: str, limit: int = 500) -> str:
    lines = []
    for line in content.splitlines():
        line = re.sub(r"</?(title|callout|text|heading|paragraph|bullet|ordered)[^>]*>", " ", line)
        line = re.sub(r"<[^>]+>", " ", line)
        clean = re.sub(r"\s+", " ", line).strip(" #|-")
        if len(clean) >= 8:
            lines.append(clean)
        if sum(len(item) for item in lines) >= limit:
            break
    excerpt = "；".join(lines)[:limit].strip()
    return excerpt or content[:limit].strip() or "自动扫描发现的业务上下文，需后续补充摘要。"


def build_context_payload(candidate: Dict[str, Any], extracted: Dict[str, Any]) -> Dict[str, Any]:
    title = candidate.get("title") or "未命名文档"
    content = str(extracted.get("content") or "")
    if title.startswith("http://") or title.startswith("https://"):
        title_match = re.search(r"<title>(.*?)</title>", content, flags=re.IGNORECASE | re.DOTALL)
        if title_match:
            title = re.sub(r"\s+", " ", title_match.group(1)).strip()
    source_link = candidate.get("url") or source_for_candidate(candidate)
    doc_token = extracted.get("doc_token") or candidate.get("token") or "N/A"
    return {
        "project_name": infer_project_name(title),
        "entity_name": title,
        "entity_type": "项目",
        "doc_type": infer_doc_type_from_title(title),
        "core_conclusion": meaningful_excerpt(content),
        "key_time": extract_time_hints(f"{title}\n{content}"),
        "people": "",
        "tags": infer_tags(title, content),
        "source_link": source_link,
        "doc_token": doc_token,
        "scan_score": candidate.get("score"),
        "scan_reason": candidate.get("reason"),
        "candidate_id": candidate.get("id"),
        "record_status": "有效",
        "ingest_method": "自动扫描",
    }


def known_topic_terms() -> List[str]:
    cli = LarkCLI()
    app_token = cli.get_base_token()
    if not app_token:
        return []
    try:
        output = cli.run(["base", "+table-list", "--base-token", app_token, "--limit", "100"])
        data = json.loads(output)
    except Exception:
        return []
    tables = data.get("data", {}).get("tables", [])
    return [table.get("name", "") for table in tables if table.get("name")]


def get_table_id(project_name: str) -> str:
    result = run_script("get_or_create_table.py", [project_name])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "get_or_create_table.py failed")
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    for line in reversed(lines):
        if line.startswith("tbl"):
            return line
    if not lines:
        raise RuntimeError("get_or_create_table.py did not return a table ID")
    raise RuntimeError(f"get_or_create_table.py did not return a table ID: {lines[-1]}")


def write_context(payload: Dict[str, Any], table_id: str) -> str:
    result = run_script("write_context.py", [json.dumps(payload, ensure_ascii=False), table_id])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "write_context.py failed")
    return result.stdout


def ingest_one(candidate: Dict[str, Any], state_path: str = DEFAULT_STATE_PATH) -> Dict[str, Any]:
    attempt_count = int(candidate.get("attempt_count") or 0) + 1
    try:
        extracted = extract_raw_content(candidate)
        payload = build_context_payload(candidate, extracted)
        required = ["project_name", "entity_name", "core_conclusion", "source_link"]
        missing = [key for key in required if not payload.get(key)]
        if missing:
            raise ValueError(f"抽取字段不完整: {', '.join(missing)}")
        topics = infer_topic_names(
            payload["entity_name"],
            str(extracted.get("content") or ""),
            known_project_terms=known_topic_terms(),
        )
        table_ids: Dict[str, str] = {}
        for topic in topics:
            topic_payload = dict(payload)
            topic_payload["project_name"] = topic
            topic_payload["topic_name"] = topic
            table_id = get_table_id(topic)
            write_context(topic_payload, table_id)
            table_ids[topic] = table_id
        return update_record(candidate, {
            "status": "auto_ingested",
            "attempt_count": attempt_count,
            "project_name": topics[0],
            "topic_names": topics,
            "entity_name": payload["entity_name"],
            "table_id": table_ids.get(topics[0]),
            "table_ids": table_ids,
            "reason": "auto ingested post-review",
            "last_error": extracted.get("read_error", ""),
        }, state_path=state_path)
    except ValueError as exc:
        return update_record(candidate, {
            "status": "needs_review",
            "attempt_count": attempt_count,
            "reason": "needs manual review",
            "last_error": str(exc),
        }, state_path=state_path)
    except Exception as exc:
        next_status = "needs_review" if attempt_count >= 3 else "failed"
        return update_record(candidate, {
            "status": next_status,
            "attempt_count": attempt_count,
            "reason": "ingestion failed",
            "last_error": str(exc),
        }, state_path=state_path)


def ingest_candidates(
    lane: Optional[str] = None,
    limit: int = 20,
    state_path: str = DEFAULT_STATE_PATH,
    ids: Optional[Iterable[str]] = None,
) -> Dict[str, int]:
    records = latest_list(state_path=state_path, status="queued", lane=lane)
    id_set = set(ids or [])
    if id_set:
        records = [record for record in records if record.get("id") in id_set]
    if limit:
        records = records[:limit]

    report = {"selected": len(records), "ingested": 0, "failed": 0, "needs_review": 0}
    for record in records:
        updated = ingest_one(record, state_path=state_path)
        if updated.get("status") == "auto_ingested":
            report["ingested"] += 1
        elif updated.get("status") == "needs_review":
            report["needs_review"] += 1
        else:
            report["failed"] += 1
    return report


def print_candidates(records: List[Dict[str, Any]]) -> None:
    if not records:
        print("没有待确认候选。")
        return
    for record in records:
        print(f"- {record.get('id')} [{record.get('doc_type')}] score={record.get('score')} {record.get('title')}")
        if record.get("reason"):
            print(f"  reason: {record['reason']}")
        if record.get("last_error"):
            print(f"  error: {record['last_error']}")
        if record.get("url"):
            print(f"  url: {record['url']}")


def mark_records(ids: List[str], status: str, state_path: str = DEFAULT_STATE_PATH) -> int:
    latest = latest_records(state_path)
    changed = 0
    for record_id in ids:
        record = latest.get(record_id)
        if not record:
            print(f"未找到候选: {record_id}")
            continue
        changes = {"status": status}
        if status == "queued":
            changes["action"] = "auto_ingest"
            changes["lane"] = "review"
        else:
            changes["reason"] = f"user marked {status}"
        update_record(record, changes, state_path=state_path)
        changed += 1
    return changed


def mark_all_review_records(status: str, state_path: str = DEFAULT_STATE_PATH) -> List[str]:
    records = latest_list(state_path=state_path, status="needs_review")
    changed_ids: List[str] = []
    for record in records:
        changes = {"status": status}
        if status == "queued":
            changes["action"] = "auto_ingest"
            changes["lane"] = "review"
        else:
            changes["reason"] = f"user marked {status}"
        updated = update_record(record, changes, state_path=state_path)
        changed_ids.append(updated["id"])
    return changed_ids


def rescan_targets(targets: List[str], state_path: str = DEFAULT_STATE_PATH) -> List[str]:
    latest = latest_records(state_path)
    changed: List[str] = []
    for target in targets:
        matching_record = latest.get(target)
        if not matching_record:
            for record in latest.values():
                if target in {record.get("url"), record.get("token")}:
                    matching_record = record
                    break
        if matching_record:
            record = matching_record
            updated = update_record(record, {
                "status": "queued",
                "action": "auto_ingest",
                "lane": "review",
                "reason": "user requested rescan",
            }, state_path=state_path)
            changed.append(updated["id"])
            continue

        raw: Dict[str, Any]
        if target.startswith("http://") or target.startswith("https://"):
            raw = {"url": target, "title": target}
        else:
            raw = {"token": target, "doc_type": "docx", "title": target}
        candidate = classify_candidate(raw, lane="review")
        candidate.update({
            "status": "queued",
            "action": "auto_ingest",
            "reason": "user requested rescan",
        })
        updated = update_record(candidate, {}, state_path=state_path)
        changed.append(updated["id"])
    return changed


def print_status(state_path: str = DEFAULT_STATE_PATH) -> None:
    records = latest_records(state_path)
    counts: Dict[str, int] = {}
    for record in records.values():
        status = record.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    print("扫描状态:")
    for status, count in sorted(counts.items()):
        print(f"- {status}: {count}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest and review Context Wizard scan candidates.")
    parser.add_argument("--state-path", default=DEFAULT_STATE_PATH)
    state_parent = argparse.ArgumentParser(add_help=False)
    state_parent.add_argument("--state-path", default=DEFAULT_STATE_PATH)
    subparsers = parser.add_subparsers(dest="command")

    ingest_parser = subparsers.add_parser("ingest", parents=[state_parent])
    ingest_parser.add_argument("--lane", choices=["freshness", "backfill", "review"])
    ingest_parser.add_argument("--limit", type=int, default=20)
    ingest_parser.add_argument("ids", nargs="*")

    review_parser = subparsers.add_parser("review", parents=[state_parent])
    review_parser.add_argument("--limit", type=int, default=20)

    approve_parser = subparsers.add_parser("approve", parents=[state_parent])
    approve_parser.add_argument("ids", nargs="*")
    approve_parser.add_argument("--all", action="store_true", help="Approve every needs_review candidate.")
    approve_parser.add_argument("--ingest", action="store_true")

    skip_parser = subparsers.add_parser("skip", parents=[state_parent])
    skip_parser.add_argument("ids", nargs="+")

    rescan_parser = subparsers.add_parser("rescan", parents=[state_parent])
    rescan_parser.add_argument("targets", nargs="+")
    rescan_parser.add_argument("--ingest", action="store_true")

    subparsers.add_parser("status", parents=[state_parent])

    args = parser.parse_args()
    command = args.command or "ingest"
    if command == "ingest":
        report = ingest_candidates(lane=args.lane, limit=args.limit, state_path=args.state_path, ids=args.ids)
        print("入库结果:")
        for key, value in report.items():
            print(f"- {key}: {value}")
    elif command == "review":
        records = latest_list(state_path=args.state_path, status="needs_review")[:args.limit]
        print_candidates(records)
    elif command == "approve":
        if args.all:
            approved_ids = mark_all_review_records("queued", state_path=args.state_path)
            changed = len(approved_ids)
        else:
            approved_ids = args.ids
            changed = mark_records(args.ids, "queued", state_path=args.state_path)
        print(f"已批准候选: {changed}")
        if args.ingest and changed:
            report = ingest_candidates(state_path=args.state_path, ids=approved_ids)
            print("入库结果:")
            for key, value in report.items():
                print(f"- {key}: {value}")
    elif command == "skip":
        changed = mark_records(args.ids, "skipped", state_path=args.state_path)
        print(f"已跳过候选: {changed}")
    elif command == "rescan":
        rescanned_ids = rescan_targets(args.targets, state_path=args.state_path)
        print(f"已加入重扫队列: {len(rescanned_ids)}")
        if args.ingest and rescanned_ids:
            report = ingest_candidates(state_path=args.state_path, ids=rescanned_ids)
            print("入库结果:")
            for key, value in report.items():
                print(f"- {key}: {value}")
    elif command == "status":
        print_status(state_path=args.state_path)


if __name__ == "__main__":
    main()
