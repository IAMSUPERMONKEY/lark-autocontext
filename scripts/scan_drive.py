#!/usr/bin/env python3
"""
Discover Feishu Drive candidates for Context Wizard.

Daily mode favors recent activity. Weekly mode advances a historical backfill
cursor so old documents receive steady processing budget.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from scan_state import (
    DEFAULT_CURSOR_PATH,
    DEFAULT_STATE_PATH,
    append_event,
    latest_records,
    load_cursor,
    now_iso,
    save_cursor,
)
from score_candidates import classify_candidate


DOC_TYPES = "docx,doc,sheet,bitable,wiki,slides"
PAGE_SIZE = 20


def friendly_cli_error(stderr: str, stdout: str = "") -> str:
    payload = stdout.strip() or stderr.strip()
    try:
        data = json.loads(payload)
        error = data.get("error", {})
        message = error.get("message") or error.get("hint") or payload
        hint = error.get("hint")
        if hint and hint not in message:
            message = f"{message}. {hint}"
        return message
    except json.JSONDecodeError:
        return payload or "lark-cli command failed"


def scan_failure_hint(message: str) -> str:
    lower = message.lower()
    if "search:docs:read" in message:
        if "pending approval" in lower or "under review" in lower:
            return "飞书正在审批 search:docs:read 权限；审批通过后重试扫描。"
        return '请追加授权搜索权限: lark-cli auth login --scope "search:docs:read" --no-wait'
    if "not configured" in lower:
        return "请先运行: lark-cli config init --new"
    if "not logged in" in lower or "auth" in lower:
        return "请先运行: lark-cli auth login --recommend --no-wait"
    return "请检查 lark-cli 配置、用户身份和飞书权限后重试。"


def run_search(args: List[str]) -> Dict[str, Any]:
    cmd = ["lark-cli", "drive", "+search", "--as", "user"] + args
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(friendly_cli_error(result.stderr, result.stdout))
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON from lark-cli drive +search: {exc}") from exc


def _find_items(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, dict):
        for key in ("items", "docs", "files", "results", "data"):
            value = data.get(key)
            if isinstance(value, list) and all(isinstance(item, dict) for item in value):
                return value
        for value in data.values():
            found = _find_items(value)
            if found:
                return found
    return []


def _find_page_token(data: Any) -> str:
    if isinstance(data, dict):
        for key in ("page_token", "next_page_token", "nextPageToken"):
            value = data.get(key)
            if value:
                return str(value)
        for value in data.values():
            found = _find_page_token(value)
            if found:
                return found
    return ""


def extract_search_results(data: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], str]:
    return _find_items(data), _find_page_token(data)


def iso_date(dt: datetime) -> str:
    return dt.date().isoformat()


def weekly_window(cursor_path: str = DEFAULT_CURSOR_PATH) -> Tuple[str, str, str]:
    cursor = load_cursor(cursor_path)
    page_token = str(cursor.get("page_token") or "")
    if cursor.get("window_start") and cursor.get("window_end"):
        return str(cursor["window_start"]), str(cursor["window_end"]), page_token

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=30)
    return iso_date(start), iso_date(end), page_token


def advance_weekly_cursor(
    window_start: str,
    window_end: str,
    next_page_token: str,
    cursor_path: str = DEFAULT_CURSOR_PATH,
) -> None:
    if next_page_token:
        save_cursor({
            "window_start": window_start,
            "window_end": window_end,
            "page_token": next_page_token,
        }, cursor_path=cursor_path)
        return

    try:
        current_start = datetime.fromisoformat(window_start).replace(tzinfo=timezone.utc)
    except ValueError:
        current_start = datetime.now(timezone.utc) - timedelta(days=30)
    next_end = current_start
    next_start = next_end - timedelta(days=30)
    save_cursor({
        "window_start": iso_date(next_start),
        "window_end": iso_date(next_end),
        "page_token": "",
    }, cursor_path=cursor_path)


def build_search_args(mode: str, page_token: str = "", cursor_path: str = DEFAULT_CURSOR_PATH) -> Tuple[List[str], Dict[str, str]]:
    base_args = [
        "--query", "",
        "--doc-types", DOC_TYPES,
        "--page-size", str(PAGE_SIZE),
        "--format", "json",
    ]
    metadata: Dict[str, str] = {}
    if mode == "daily":
        base_args.extend(["--edited-since", "2d", "--sort", "edit_time"])
    elif mode == "weekly":
        window_start, window_end, cursor_token = weekly_window(cursor_path)
        metadata = {"window_start": window_start, "window_end": window_end}
        base_args.extend([
            "--edited-since", window_start,
            "--edited-until", window_end,
            "--sort", "edit_time_asc",
        ])
        page_token = page_token or cursor_token
    else:
        raise ValueError(f"Unknown scan mode: {mode}")

    if page_token:
        base_args.extend(["--page-token", page_token])
    return base_args, metadata


def discover(
    mode: str,
    limit: int,
    state_path: str = DEFAULT_STATE_PATH,
    cursor_path: str = DEFAULT_CURSOR_PATH,
    ingest: bool = True,
) -> Dict[str, Any]:
    lane = "freshness" if mode == "daily" else "backfill"
    search_args, metadata = build_search_args(mode, cursor_path=cursor_path)
    latest = latest_records(state_path)

    payload = run_search(search_args)
    items, next_page_token = extract_search_results(payload)
    if limit:
        items = items[:limit]

    report = {
        "mode": mode,
        "lane": lane,
        "seen": len(items),
        "queued": 0,
        "needs_review": 0,
        "deferred": 0,
        "skipped_unchanged": 0,
        "failed": 0,
        "ingested": 0,
        "next_page_token": next_page_token,
        "window": metadata,
    }

    for raw in items:
        candidate = classify_candidate(raw, lane=lane)
        previous = latest.get(candidate["id"])
        if previous and previous.get("fingerprint") == candidate.get("fingerprint"):
            previous_status = previous.get("status")
            if previous_status in {"auto_ingested", "skipped"} or previous_status == candidate.get("status"):
                report["skipped_unchanged"] += 1
                continue

        if candidate["status"] == "queued":
            report["queued"] += 1
        elif candidate["status"] == "needs_review":
            report["needs_review"] += 1
        else:
            report["deferred"] += 1
        append_event(candidate, state_path=state_path)

    if mode == "weekly":
        advance_weekly_cursor(
            metadata.get("window_start", ""),
            metadata.get("window_end", ""),
            next_page_token,
            cursor_path=cursor_path,
        )

    if ingest and report["queued"]:
        try:
            from ingest_candidates import ingest_candidates

            ingest_report = ingest_candidates(lane=lane, limit=20 if mode == "daily" else 50, state_path=state_path)
            report["ingested"] = ingest_report.get("ingested", 0)
            report["failed"] += ingest_report.get("failed", 0)
            report["needs_review"] += ingest_report.get("needs_review", 0)
        except Exception as exc:
            report["failed"] += 1
            report["ingest_error"] = str(exc)

    report["finished_at"] = now_iso()
    return report


def print_report(report: Dict[str, Any]) -> None:
    print("本次扫描:")
    print(f"- 模式: {report['mode']}")
    print(f"- 发现候选: {report['seen']}")
    print(f"- 自动入库: {report['ingested']}")
    print(f"- 待确认: {report['needs_review']}")
    print(f"- 新增入库队列: {report['queued']}")
    print(f"- 跳过/未变化: {report['skipped_unchanged']}")
    print(f"- 暂缓: {report['deferred']}")
    print(f"- 失败: {report['failed']}")
    if report.get("window"):
        window = report["window"]
        print(f"- 历史回填窗口: {window.get('window_start')}..{window.get('window_end')}")
    if report.get("ingest_error"):
        print(f"- 入库错误: {report['ingest_error']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan Feishu Drive for Context Wizard candidates.")
    parser.add_argument("--mode", choices=["daily", "weekly"], required=True)
    parser.add_argument("--limit", type=int, default=0, help="Maximum search results to process from this page.")
    parser.add_argument("--state-path", default=DEFAULT_STATE_PATH)
    parser.add_argument("--cursor-path", default=DEFAULT_CURSOR_PATH)
    parser.add_argument("--discover-only", action="store_true", help="Only discover and score; do not ingest queued candidates.")
    args = parser.parse_args()

    try:
        report = discover(
            mode=args.mode,
            limit=args.limit,
            state_path=args.state_path,
            cursor_path=args.cursor_path,
            ingest=not args.discover_only,
        )
    except RuntimeError as exc:
        message = str(exc)
        print(f"❌ 扫描失败: {message}")
        print(f"   下一步: {scan_failure_hint(message)}")
        sys.exit(1)
    print_report(report)


if __name__ == "__main__":
    main()
