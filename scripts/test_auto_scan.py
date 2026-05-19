#!/usr/bin/env python3
"""
Smoke tests for auto scan state, scoring, and parsing logic.
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

from ingest_candidates import build_context_payload, rescan_targets
from scan_drive import extract_search_results
from scan_state import append_event, latest_list, latest_records, update_record
from score_candidates import classify_candidate


class AutoScanTests(unittest.TestCase):
    def test_high_confidence_candidate_is_queued(self):
        raw = {
            "title": "星选咖啡 Q2 复盘会议纪要",
            "url": "https://feishu.cn/docx/AbCd1234",
            "doc_type": "docx",
            "edit_time": "2026-05-18T10:00:00+00:00",
        }
        candidate = classify_candidate(raw, now=datetime(2026, 5, 19, tzinfo=timezone.utc))

        self.assertEqual(candidate["action"], "auto_ingest")
        self.assertEqual(candidate["status"], "queued")
        self.assertGreaterEqual(candidate["score"], 55)
        self.assertEqual(candidate["token"], "AbCd1234")

    def test_low_value_template_goes_to_review_or_defer(self):
        raw = {
            "title": "个人临时草稿模板",
            "url": "https://feishu.cn/docx/Tpl1234",
            "doc_type": "docx",
            "edit_time": "2026-05-18T10:00:00+00:00",
        }
        candidate = classify_candidate(raw, now=datetime(2026, 5, 19, tzinfo=timezone.utc))

        self.assertNotEqual(candidate["action"], "auto_ingest")
        self.assertIn(candidate["status"], {"needs_review", "defer"})

    def test_state_latest_record_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "scan_state.jsonl")
            first = append_event({"id": "abc", "status": "queued", "title": "A"}, state_path)
            update_record(first, {"status": "auto_ingested"}, state_path)

            latest = latest_records(state_path)
            self.assertEqual(latest["abc"]["status"], "auto_ingested")
            self.assertEqual(len(latest_list(state_path)), 1)

    def test_extract_search_results_accepts_nested_items(self):
        payload = {
            "data": {
                "items": [{"title": "A"}, {"title": "B"}],
                "page_token": "next-token",
            }
        }
        items, page_token = extract_search_results(payload)

        self.assertEqual([item["title"] for item in items], ["A", "B"])
        self.assertEqual(page_token, "next-token")

    def test_rule_based_payload_uses_title_and_content(self):
        candidate = {
            "title": "星选咖啡 Q2 复盘",
            "url": "https://feishu.cn/docx/AbCd1234",
            "token": "AbCd1234",
        }
        extracted = {
            "content": "# 星选咖啡 Q2 复盘\n\n核心结论：会员券成本需要下调，6月15日开始执行。",
            "doc_token": "AbCd1234",
        }
        payload = build_context_payload(candidate, extracted)

        self.assertEqual(payload["project_name"], "星选咖啡")
        self.assertEqual(payload["doc_type"], "复盘报告")
        self.assertEqual(payload["doc_token"], "AbCd1234")
        self.assertIn("会员券成本", payload["core_conclusion"])
        self.assertIn("6月15日", payload["key_time"])

    def test_rescan_url_adds_queued_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "scan_state.jsonl")
            ids = rescan_targets(["https://feishu.cn/docx/Manual123"], state_path=state_path)
            latest = latest_records(state_path)

            self.assertEqual(len(ids), 1)
            self.assertEqual(latest[ids[0]]["status"], "queued")
            self.assertEqual(latest[ids[0]]["action"], "auto_ingest")


if __name__ == "__main__":
    unittest.main()
