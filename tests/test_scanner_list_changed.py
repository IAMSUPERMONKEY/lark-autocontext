"""Tests for scanner --list-changed mode."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))


def test_list_changed_returns_dict_shape():
    from scanner import list_changed
    sources = [{"type": "folder", "token": "fldcnFAKE", "name": "Test Folder"}]
    result = list_changed(sources, since="2099-01-01T00:00:00+08:00")
    assert "changed" in result
    assert isinstance(result["changed"], list)
    assert "source_results" in result


def test_normalize_changed_entry_shape():
    from scanner import _normalize_changed_entry
    raw = {
        "token": "DOCABC",
        "url": "https://feishu.cn/docx/DOCABC",
        "name": "Test Doc",
        "edit_time": "2026-06-20T14:30:00+08:00"
    }
    entry = _normalize_changed_entry(raw, source_key="folder:fldcnFAKE")
    assert entry["doc_token"] == "DOCABC"
    assert entry["url"] == "https://feishu.cn/docx/DOCABC"
    assert entry["title"] == "Test Doc"
    assert entry["edited_time"] == "2026-06-20T14:30:00+08:00"
    assert entry["source"] == "folder:fldcnFAKE"
