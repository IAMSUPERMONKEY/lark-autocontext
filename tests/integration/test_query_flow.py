"""End-to-end integration test for the query/search flow.

Verifies the full pipeline:
  1. Create multiple OKF documents with different types, projects, tags
  2. Build FTS5 index via QueryEngine.rebuild_index()
  3. Keyword search finds relevant documents
  4. Type filter narrows results
  5. Deep read assembles context from full content
  6. No-deep-read mode returns snippets only
  7. CJK (Chinese) search works correctly
"""
import sys
import os
import json

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))

from query_engine import (  # noqa: E402
    QueryEngine, SearchFilters, DocMatch, SearchResult,
)


# ---------------------------------------------------------------------------
# Sample OKF documents for testing
# ---------------------------------------------------------------------------

OKF_MEETING = '''---
type: Meeting Minutes
title: "架构评审会议"
description: 讨论微服务架构拆分方案
timestamp: 2026-07-15T10:00:00+08:00
project: platform
tags: [架构, 微服务, 评审]
people: [张三, 李四]
concepts: [微服务, DDD]
---

# Summary

本次会议讨论了微服务架构拆分方案，决定采用 DDD 领域驱动设计方法。

# Key Points

- 确定采用 DDD 进行领域建模
- 识别出 3 个核心限界上下文
- 下周完成详细设计文档
'''

OKF_DESIGN_DOC = '''---
type: Design Doc
title: "微服务网关设计"
description: API 网关的技术选型和架构设计
timestamp: 2026-07-16T14:00:00+08:00
project: platform
tags: [网关, API, 架构]
people: [王五]
concepts: [网关, Kong]
---

# Summary

设计基于 Kong 的微服务 API 网关架构方案，支持限流、熔断和认证。

# Key Points

- 选型 Kong 作为网关
- 支持 OAuth2 认证
- 限流策略：令牌桶算法
'''

OKF_POSTMORTEM = '''---
type: Postmortem
title: "支付系统故障复盘"
description: 7月10日支付系统宕机2小时的复盘报告
timestamp: 2026-07-10T09:00:00+08:00
project: payment
tags: [故障, 支付, 复盘]
people: [赵六, 钱七]
concepts: [支付, 容灾]
---

# Summary

支付系统因数据库连接池耗尽导致宕机 2 小时，已修复并增加监控。

# Key Points

- 根因：连接池配置过小
- 修复：扩容连接池 + 增加告警
- 改进：引入熔断机制
'''


# ---------------------------------------------------------------------------
# Helper: create a bundle directory with OKF files
# ---------------------------------------------------------------------------

def _make_bundle(tmp_path, docs=None):
    """Create a bundle directory under ``tmp_path / "bundle"`` and write OKF files.

    Args:
        tmp_path: pytest ``tmp_path`` fixture (a ``pathlib.Path``).
        docs: dict of ``{filename: okf_content}``. Defaults to all 3 sample
            docs (meeting, design, postmortem).

    Returns:
        ``pathlib.Path`` to the bundle directory.
    """
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    if docs is None:
        docs = {
            "meeting.md": OKF_MEETING,
            "design.md": OKF_DESIGN_DOC,
            "postmortem.md": OKF_POSTMORTEM,
        }
    for filename, content in docs.items():
        (bundle / filename).write_text(content, encoding="utf-8")
    return bundle


# ---------------------------------------------------------------------------
# 1. test_rebuild_and_keyword_search
# ---------------------------------------------------------------------------

def test_rebuild_and_keyword_search(tmp_path):
    """rebuild_index() indexes all docs; keyword search finds relevant ones.

    Creates 3 OKF docs (meeting, design, postmortem) in a bundle, rebuilds
    the full index, then searches for ``微服务``. The meeting and design
    docs both mention ``微服务`` (in title/body/tags); the postmortem does
    not and must be absent from the results.
    """
    bundle = _make_bundle(tmp_path)
    engine = QueryEngine(str(bundle))

    count = engine.rebuild_index()
    assert count == 3, \
        f"rebuild_index should return 3, got {count}"

    result = engine.search("微服务")

    assert isinstance(result, SearchResult)
    assert result.total_found == 2, \
        f"expected 2 matches for '微服务', got {result.total_found}"
    titles = {m.title for m in result.matches}
    assert "架构评审会议" in titles, \
        "meeting doc should match '微服务'"
    assert "微服务网关设计" in titles, \
        "design doc should match '微服务'"
    assert "支付系统故障复盘" not in titles, \
        "postmortem doc should NOT match '微服务'"


# ---------------------------------------------------------------------------
# 2. test_type_filter
# ---------------------------------------------------------------------------

def test_type_filter(tmp_path):
    """SearchFilters(doc_type=...) narrows results to the specified type.

    ``架构`` matches both the meeting (Meeting Minutes) and design (Design
    Doc) docs. Filtering by ``doc_type="Design Doc"`` must keep only the
    design doc. Filtering by ``doc_type="Postmortem"`` with the same query
    must return zero results (postmortem does not contain ``架构``).
    Separately, ``支付`` matches only the postmortem; filtering by
    ``doc_type="Postmortem"`` must return exactly that doc.
    """
    bundle = _make_bundle(tmp_path)
    engine = QueryEngine(str(bundle))
    engine.rebuild_index()

    # "架构" matches meeting + design; filter to Design Doc only.
    result_design = engine.search(
        "架构", filters=SearchFilters(doc_type="Design Doc"))
    assert result_design.total_found == 1, \
        f"type filter 'Design Doc' should leave 1 doc, got {result_design.total_found}"
    assert result_design.matches[0].title == "微服务网关设计"
    assert result_design.matches[0].doc_type == "Design Doc"

    # "支付" matches postmortem; filter to Postmortem.
    result_pm = engine.search(
        "支付", filters=SearchFilters(doc_type="Postmortem"))
    assert result_pm.total_found == 1, \
        f"type filter 'Postmortem' should leave 1 doc, got {result_pm.total_found}"
    assert result_pm.matches[0].title == "支付系统故障复盘"
    assert result_pm.matches[0].doc_type == "Postmortem"

    # "架构" with Postmortem filter -> 0 (postmortem does not contain 架构).
    result_empty = engine.search(
        "架构", filters=SearchFilters(doc_type="Postmortem"))
    assert result_empty.total_found == 0, \
        "no postmortem doc contains '架构', filter should yield 0"


# ---------------------------------------------------------------------------
# 3. test_project_filter
# ---------------------------------------------------------------------------

def test_project_filter(tmp_path):
    """SearchFilters(project=...) narrows results to the specified project.

    The meeting and design docs belong to ``platform``; the postmortem
    belongs to ``payment``. Searching ``架构`` (matches meeting + design)
    with ``project="platform"`` must return both. Searching ``架构`` with
    ``project="payment"`` must return 0. Searching ``支付`` (matches only
    postmortem) with ``project="payment"`` must return the postmortem.
    """
    bundle = _make_bundle(tmp_path)
    engine = QueryEngine(str(bundle))
    engine.rebuild_index()

    # "架构" matches meeting + design, both in platform.
    result_platform = engine.search(
        "架构", filters=SearchFilters(project="platform"))
    assert result_platform.total_found == 2, \
        f"project filter 'platform' should leave 2 docs, got {result_platform.total_found}"
    titles = {m.title for m in result_platform.matches}
    assert "架构评审会议" in titles
    assert "微服务网关设计" in titles

    # "架构" with payment filter -> 0 (no payment doc contains 架构).
    result_empty = engine.search(
        "架构", filters=SearchFilters(project="payment"))
    assert result_empty.total_found == 0, \
        "no payment doc contains '架构', filter should yield 0"

    # "支付" matches postmortem (project=payment).
    result_payment = engine.search(
        "支付", filters=SearchFilters(project="payment"))
    assert result_payment.total_found == 1, \
        f"project filter 'payment' should leave 1 doc, got {result_payment.total_found}"
    assert result_payment.matches[0].title == "支付系统故障复盘"


# ---------------------------------------------------------------------------
# 4. test_deep_read_context
# ---------------------------------------------------------------------------

def test_deep_read_context(tmp_path):
    """deep_read=True assembles context from full content of matching docs.

    Searching ``架构`` with ``deep_read=True`` must populate
    ``SearchResult.context`` with the assembled full content of the
    matching docs (meeting and/or design). Each ``DocMatch.full_content``
    must also be non-None.
    """
    bundle = _make_bundle(tmp_path)
    engine = QueryEngine(str(bundle))
    engine.rebuild_index()

    result = engine.search("架构", deep_read=True)

    assert result.total_found >= 1, \
        "at least 1 doc should match '架构'"
    assert result.context, \
        "context should be non-empty with deep_read=True"

    # Context should contain body content from at least one matching doc.
    has_meeting_content = (
        "微服务架构拆分" in result.context or "DDD" in result.context
    )
    has_design_content = (
        "Kong" in result.context or "API 网关" in result.context
    )
    assert has_meeting_content or has_design_content, \
        "context should contain content from at least one matching doc"

    # Each match should have full_content populated.
    for m in result.matches:
        assert m.full_content is not None, \
            "full_content should be populated when deep_read=True"


# ---------------------------------------------------------------------------
# 5. test_no_deep_read
# ---------------------------------------------------------------------------

def test_no_deep_read(tmp_path):
    """deep_read=False returns snippets only, no context or full content.

    With ``deep_read=False``, ``SearchResult.context`` must be empty and
    each ``DocMatch.full_content`` must be ``None``. Snippets must still
    be populated so the caller can browse results without reading full
    files.
    """
    bundle = _make_bundle(tmp_path)
    engine = QueryEngine(str(bundle))
    engine.rebuild_index()

    result = engine.search("架构", deep_read=False)

    assert result.total_found >= 1, \
        "at least 1 doc should match '架构'"
    assert result.context == "", \
        "context should be empty when deep_read=False"

    for m in result.matches:
        assert m.full_content is None, \
            "full_content should be None when deep_read=False"
        assert m.snippet, \
            "snippet should still be populated when deep_read=False"


# ---------------------------------------------------------------------------
# 6. test_cjk_single_char_search
# ---------------------------------------------------------------------------

def test_cjk_single_char_search(tmp_path):
    """Single Chinese character search works via CJK spacing workaround.

    The postmortem doc contains ``支付`` in its title, description, body,
    and tags. After CJK spacing, ``支付`` is indexed as ``支 付`` (two
    separate tokens). A single-character query ``支`` must therefore match
    via FTS5 -- this is the whole point of the CJK-spacing preprocessing
    applied to both the indexed body_text and the query.
    """
    bundle = _make_bundle(tmp_path)
    engine = QueryEngine(str(bundle))
    engine.rebuild_index()

    result = engine.search("支")

    assert result.total_found >= 1, \
        f"single CJK char '支' should find the postmortem doc, got {result.total_found}"
    titles = {m.title for m in result.matches}
    assert "支付系统故障复盘" in titles, \
        "postmortem doc (contains 支付) should be found by single char '支'"


# ---------------------------------------------------------------------------
# 7. test_search_scoring
# ---------------------------------------------------------------------------

def test_search_scoring(tmp_path):
    """Search results are sorted by score descending; top result is relevant.

    Searching ``架构`` matches at least the meeting and design docs.
    Results must be sorted by composite score in descending order, and
    the top result must be one of the docs that prominently features
    ``架构``.
    """
    bundle = _make_bundle(tmp_path)
    engine = QueryEngine(str(bundle))
    engine.rebuild_index()

    result = engine.search("架构")

    assert result.total_found >= 2, \
        f"at least 2 docs should match '架构', got {result.total_found}"

    # Verify sorted by score descending.
    scores = [m.score for m in result.matches]
    assert scores == sorted(scores, reverse=True), \
        f"scores should be in descending order, got {scores}"

    # Top result should be a doc that prominently features 架构.
    top = result.matches[0]
    assert top.title in ("架构评审会议", "微服务网关设计"), \
        f"top result should be a relevant doc, got {top.title!r}"
    assert top.score > 0, \
        f"top score should be positive, got {top.score}"


# ---------------------------------------------------------------------------
# 8. test_incremental_index_update
# ---------------------------------------------------------------------------

def test_incremental_index_update(tmp_path):
    """update_index() on a new file makes it searchable without full rebuild.

    Builds the index with 2 docs (meeting + design), verifies the
    postmortem is not yet findable, then writes the 3rd OKF file and
    calls ``update_index()`` on it. The postmortem must become findable
    immediately -- no full ``rebuild_index()`` needed.
    """
    bundle = _make_bundle(tmp_path, docs={
        "meeting.md": OKF_MEETING,
        "design.md": OKF_DESIGN_DOC,
    })
    engine = QueryEngine(str(bundle))
    engine.rebuild_index()

    # Postmortem is not yet indexed -> "支付" finds nothing.
    result_before = engine.search("支付")
    assert result_before.total_found == 0, \
        "postmortem should not be findable before update_index"

    # Write the 3rd doc and incrementally update the index.
    pm_path = bundle / "postmortem.md"
    pm_path.write_text(OKF_POSTMORTEM, encoding="utf-8")
    engine.update_index(str(pm_path))

    # Now "支付" should find the postmortem without a full rebuild.
    result_after = engine.search("支付")
    assert result_after.total_found == 1, \
        f"postmortem should be findable after update_index, got {result_after.total_found}"
    assert result_after.matches[0].title == "支付系统故障复盘"

    # The first 2 docs should still be findable (no data loss).
    result_meeting = engine.search("微服务")
    assert result_meeting.total_found == 2, \
        f"meeting + design should still be findable, got {result_meeting.total_found}"


def test_cjk_title_search(tmp_path):
    """CJK keyword search matches title field (not just body_text).

    This is a regression test for the bug where CJK spacing was only
    applied to body_text, not to title/description/tags/people. The fix
    applies CJK spacing to all FTS-indexed fields while keeping original
    text in the documents table for display.
    """
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "projects").mkdir()

    # Doc with Chinese keyword in title but NOT in body
    doc = bundle / "projects" / "title_test.md"
    doc.write_text(
        '---\n'
        'type: Reference\n'
        'title: "支付网关技术规范"\n'
        'description: API技术文档\n'
        'timestamp: 2026-07-20T10:00:00+08:00\n'
        'project: payment\n'
        'tags: [API, 文档]\n'
        'people: []\n'
        '---\n\n'
        '# Summary\n\n'
        'This document covers the gateway specification.\n'
        '\n'
        '# Key Points\n\n'
        '- Gateway API endpoints\n',
        encoding='utf-8',
    )

    from query_engine import QueryEngine
    engine = QueryEngine(str(bundle))
    count = engine.rebuild_index()
    assert count == 1

    # Search for "支付" which appears ONLY in the title, not in body
    result = engine.search("支付")
    assert result.total_found >= 1,         "CJK keyword '支付' from title should be found"

    # Verify the returned title is ORIGINAL (not CJK-spaced)
    match = result.matches[0]
    assert match.title == "支付网关技术规范",         f"Title should be original (unspaced), got: {match.title!r}"


def test_cjk_description_search(tmp_path):
    """CJK keyword search matches description field."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "projects").mkdir()

    # Doc with Chinese keyword in description but NOT in title or body
    doc = bundle / "projects" / "desc_test.md"
    doc.write_text(
        '---\n'
        'type: Reference\n'
        'title: "Tech Spec"\n'
        'description: "优惠券系统设计文档"\n'
        'timestamp: 2026-07-20T10:00:00+08:00\n'
        'project: marketing\n'
        'tags: [spec]\n'
        'people: []\n'
        '---\n\n'
        '# Summary\n\n'
        'The system uses REST API.\n',
        encoding='utf-8',
    )

    from query_engine import QueryEngine
    engine = QueryEngine(str(bundle))
    engine.rebuild_index()

    # Search for "优惠券" which appears ONLY in description
    result = engine.search("优惠券")
    assert result.total_found >= 1,         "CJK keyword '优惠券' from description should be found"

    # Verify description is not spaced in results (title check is enough
    # since title/description use the same mechanism)
    match = result.matches[0]
    assert match.title == "Tech Spec",         f"Title should be original, got: {match.title!r}"


def test_cjk_tags_filter(tmp_path):
    """Tag filtering works with CJK tags (original, not spaced)."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "projects").mkdir()

    doc = bundle / "projects" / "tag_test.md"
    doc.write_text(
        '---\n'
        'type: Reference\n'
        'title: "Test Doc"\n'
        'description: "Test description"\n'
        'timestamp: 2026-07-20T10:00:00+08:00\n'
        'project: test\n'
        'tags: [架构, 微服务]\n'
        'people: []\n'
        '---\n\n'
        '# Summary\n\n'
        'Some content here.\n',
        encoding='utf-8',
    )

    from query_engine import QueryEngine, SearchFilters
    engine = QueryEngine(str(bundle))
    engine.rebuild_index()

    # Filter by CJK tag — should match because tags in documents table
    # are original (not CJK-spaced)
    result = engine.search("content", filters=SearchFilters(tags=["架构"]))
    assert result.total_found >= 1,         "Tag filter with CJK tag '架构' should match"
