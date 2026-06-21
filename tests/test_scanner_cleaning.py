"""Tests for Feishu private-tag cleaning in scanner.py."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from scanner import clean_feishu_content


def test_clean_callout_tag():
    raw = '<callout emoji="🚀">整体框架</callout>'
    assert clean_feishu_content(raw) == '整体框架'


def test_clean_title_tag():
    raw = '<title>2026-06-20 周会</title>\n正文内容'
    cleaned = clean_feishu_content(raw)
    assert cleaned.startswith('# 2026-06-20 周会')
    assert '正文内容' in cleaned


def test_clean_image_tag():
    raw = '<image src="abc.png" />之后的文字'
    cleaned = clean_feishu_content(raw)
    assert '<image' not in cleaned
    assert '之后的文字' in cleaned


def test_collapse_blank_lines():
    raw = 'line1\n\n\n\n\nline2'
    assert clean_feishu_content(raw) == 'line1\n\nline2'


def test_preserves_markdown():
    raw = '# Heading\n\n- item1\n- item2\n\n```code```'
    assert clean_feishu_content(raw) == raw
