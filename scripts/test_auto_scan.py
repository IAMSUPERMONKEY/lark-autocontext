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

from ingest_candidates import build_context_payload, extract_raw_content, rescan_targets
from scan_state import append_event
from scan_drive import extract_search_results
from scan_state import append_event, latest_list, latest_records, update_record
from score_candidates import classify_candidate, infer_topic_names


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

    def test_low_value_template_is_ingested_with_low_score(self):
        raw = {
            "title": "个人临时草稿模板",
            "url": "https://feishu.cn/docx/Tpl1234",
            "doc_type": "docx",
            "edit_time": "2026-05-18T10:00:00+00:00",
        }
        candidate = classify_candidate(raw, now=datetime(2026, 5, 19, tzinfo=timezone.utc))

        self.assertEqual(candidate["action"], "auto_ingest")
        self.assertEqual(candidate["status"], "queued")
        self.assertLess(candidate["score"], 55)

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

    def test_lark_search_shape_reads_highlighted_title(self):
        raw = {
            "entity_type": "DOC",
            "title_highlighted": "<em>甄选625方案讨论会</em> - 会议纪要",
            "result_meta": {
                "doc_types": "DOCX",
                "token": "FH6wdQrXlouYIkx54V6c2H4Jnjd",
                "url": "https://supermonkey.feishu.cn/docx/FH6wdQrXlouYIkx54V6c2H4Jnjd",
                "update_time_iso": "2026-05-19T16:50:38+08:00",
                "last_open_time_iso": "2026-05-19T18:12:54+08:00",
                "owner_name": "夏靖龙",
            },
        }
        candidate = classify_candidate(raw, now=datetime(2026, 5, 20, tzinfo=timezone.utc))

        self.assertEqual(candidate["title"], "甄选625方案讨论会 - 会议纪要")
        self.assertEqual(candidate["doc_type"], "docx")
        self.assertEqual(candidate["action"], "auto_ingest")

    def test_sheet_candidates_are_queued_for_post_review(self):
        raw = {
            "entity_type": "WIKI",
            "title_highlighted": "超猩甄选组织激励方案-运营组",
            "result_meta": {
                "doc_types": "SHEET",
                "token": "WikiToken",
                "url": "https://supermonkey.feishu.cn/wiki/WikiToken",
                "update_time_iso": "2026-05-07T12:11:46+08:00",
                "last_open_time_iso": "2026-05-07T15:07:18+08:00",
            },
        }
        candidate = classify_candidate(raw, now=datetime(2026, 5, 20, tzinfo=timezone.utc))

        self.assertEqual(candidate["doc_type"], "sheet")
        self.assertEqual(candidate["action"], "auto_ingest")
        self.assertEqual(candidate["status"], "queued")

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
        self.assertEqual(payload["record_status"], "有效")
        self.assertEqual(payload["ingest_method"], "自动扫描")
        self.assertIn("会员券成本", payload["core_conclusion"])
        self.assertIn("6月15日", payload["key_time"])

    def test_project_name_strips_campaign_suffix(self):
        candidate = {
            "title": "甄选625方案讨论会 - 会议纪要",
            "url": "https://feishu.cn/docx/AbCd1234",
            "token": "AbCd1234",
        }
        extracted = {
            "content": "<title>甄选625方案讨论会 - 会议纪要</title>\n<callout>核心结论：活动形式不大改。</callout>",
            "doc_token": "AbCd1234",
        }
        payload = build_context_payload(candidate, extracted)

        self.assertEqual(payload["project_name"], "甄选")
        self.assertNotIn("<title>", payload["core_conclusion"])

    def test_rescan_url_adds_queued_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "scan_state.jsonl")
            ids = rescan_targets(["https://feishu.cn/docx/Manual123"], state_path=state_path)
            latest = latest_records(state_path)

            self.assertEqual(len(ids), 1)
            self.assertEqual(latest[ids[0]]["status"], "queued")
            self.assertEqual(latest[ids[0]]["action"], "auto_ingest")

    def test_rescan_url_reuses_existing_candidate_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "scan_state.jsonl")
            existing = append_event({
                "id": "doc-1",
                "url": "https://feishu.cn/docx/Manual123",
                "token": "Manual123",
                "title": "甄选625方案讨论会 - 会议纪要",
                "doc_type": "docx",
                "status": "auto_ingested",
            }, state_path=state_path)
            ids = rescan_targets([existing["url"]], state_path=state_path)
            latest = latest_records(state_path)

            self.assertEqual(ids, ["doc-1"])
            self.assertEqual(latest["doc-1"]["title"], "甄选625方案讨论会 - 会议纪要")
            self.assertEqual(latest["doc-1"]["status"], "queued")

    def test_metadata_only_content_for_unreadable_candidate(self):
        candidate = {
            "title": "甄选数据",
            "url": "https://feishu.cn/base/Base123",
            "token": "Base123",
            "doc_type": "bitable",
        }

        extracted = extract_raw_content(candidate)

        self.assertTrue(extracted["metadata_only"])
        self.assertIn("仅记录元数据", extracted["content"])
        self.assertEqual(extracted["doc_token"], "Base123")

    def test_topic_names_include_existing_matching_topics(self):
        topics = infer_topic_names(
            "超猩甄选服务运营报告-4月",
            "本报告包含商户运营、企服运营和甄选数据。",
            known_project_terms=["甄选", "企服", "业务上下文"],
        )

        self.assertEqual(topics[0], "超猩甄选服务运营报告")
        self.assertIn("甄选", topics)
        self.assertIn("企服", topics)
        self.assertNotIn("业务上下文", topics)


if __name__ == "__main__":
    unittest.main()
