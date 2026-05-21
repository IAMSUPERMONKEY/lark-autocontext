"""
Candidate normalization and scoring for Feishu Drive scan results.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from hashlib import sha1
from typing import Any, Dict, Iterable, List, Optional, Tuple

from scan_state import candidate_id


AUTO_INGEST_THRESHOLD = 55
REVIEW_THRESHOLD = 25

BUSINESS_TITLE_TERMS = [
    "会议纪要",
    "复盘",
    "需求",
    "方案",
    "决策",
    "合同",
    "协议",
    "数据分析",
    "竞品",
    "OKR",
    "okr",
    "roadmap",
    "review",
]

LOW_VALUE_TERMS = [
    "模板",
    "草稿",
    "draft",
    "临时",
    "test",
    "测试",
    "副本",
]

SUPPORTED_DISCOVERY_TYPES = {"docx", "doc", "sheet", "bitable", "wiki", "slides"}
AUTO_READ_TYPES = {"docx", "doc"}


def _deep_values(data: Any, keys: Iterable[str]) -> Optional[Any]:
    if isinstance(data, dict):
        for key in keys:
            if key in data and data[key] not in (None, ""):
                return data[key]
        for value in data.values():
            found = _deep_values(value, keys)
            if found not in (None, ""):
                return found
    elif isinstance(data, list):
        for value in data:
            found = _deep_values(value, keys)
            if found not in (None, ""):
                return found
    return None


def _first(data: Dict[str, Any], *keys: str) -> Optional[Any]:
    return _deep_values(data, keys)


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return ", ".join(_as_str(item) for item in value if item is not None)
    if isinstance(value, dict):
        return _as_str(_first(value, "name", "title", "text", "value"))
    return str(value)


def _clean_text(value: Any) -> str:
    text = _as_str(value)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


def _parse_time(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if text.isdigit():
        return _parse_time(int(text))
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_time(value: Any) -> str:
    parsed = _parse_time(value)
    if not parsed:
        return _as_str(value)
    return parsed.replace(microsecond=0).isoformat()


def _days_since(value: Any, now: Optional[datetime] = None) -> Optional[float]:
    parsed = _parse_time(value)
    if not parsed:
        return None
    now = now or datetime.now(timezone.utc)
    return max((now - parsed).total_seconds() / 86400, 0)


def _token_from_url(url: str) -> str:
    patterns = [
        r"/docx?/([A-Za-z0-9]+)",
        r"/sheet/([A-Za-z0-9]+)",
        r"/base/([A-Za-z0-9]+)",
        r"/wiki/([A-Za-z0-9]+)",
        r"/slides/([A-Za-z0-9]+)",
        r"/file/([A-Za-z0-9]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return ""


def _type_from_url(url: str) -> str:
    if "/docx/" in url:
        return "docx"
    if "/doc/" in url:
        return "doc"
    if "/sheet/" in url:
        return "sheet"
    if "/base/" in url:
        return "bitable"
    if "/wiki/" in url:
        return "wiki"
    if "/slides/" in url:
        return "slides"
    return ""


def normalize_candidate(raw: Dict[str, Any], lane: str = "freshness") -> Dict[str, Any]:
    url = _as_str(_first(raw, "url", "web_url", "link", "share_url", "docs_url"))
    token = _as_str(
        _first(
            raw,
            "token",
            "file_token",
            "doc_token",
            "docx_token",
            "sheet_token",
            "obj_token",
            "resource_token",
            "wiki_token",
        )
    )
    if not token and url:
        token = _token_from_url(url)

    result_meta = raw.get("result_meta") if isinstance(raw.get("result_meta"), dict) else {}
    doc_type = _as_str(
        result_meta.get("doc_types")
        or _first(
            raw,
            "doc_type",
            "doc_types",
            "type",
            "file_type",
            "obj_type",
            "resource_type",
            "entity_type",
        )
    ).lower()
    if not doc_type and url:
        doc_type = _type_from_url(url)
    doc_type = doc_type.replace("docs", "doc").replace("bitable_base", "bitable")

    title = _clean_text(_first(raw, "title", "title_highlighted", "name", "file_name", "docs_title")) or "(untitled)"
    updated_at = _iso_time(_first(
        raw,
        "updated_at",
        "update_time",
        "update_time_iso",
        "edit_time",
        "edited_time",
        "modified_time",
    ))
    created_at = _iso_time(_first(raw, "created_at", "create_time"))
    opened_at = _iso_time(_first(raw, "opened_at", "open_time", "last_open_time", "last_open_time_iso"))
    creator = _clean_text(_first(raw, "creator", "owner", "owner_name", "owner_id", "creator_id", "user_name"))
    fingerprint_source = "|".join([doc_type, token, url, updated_at, title])
    fingerprint = sha1(fingerprint_source.encode("utf-8")).hexdigest()

    candidate = {
        "token": token,
        "url": url,
        "doc_type": doc_type,
        "title": title,
        "creator": creator,
        "created_at": created_at,
        "updated_at": updated_at,
        "opened_at": opened_at,
        "fingerprint": fingerprint,
        "lane": lane,
        "raw": raw,
    }
    candidate["id"] = candidate_id(candidate)
    return candidate


def score_candidate(
    candidate: Dict[str, Any],
    known_project_terms: Optional[List[str]] = None,
    now: Optional[datetime] = None,
) -> Tuple[int, str, List[str]]:
    score = 0
    reasons: List[str] = []
    title = candidate.get("title", "")
    searchable = " ".join(
        str(candidate.get(key, ""))
        for key in ("title", "url", "doc_type", "creator")
    )
    doc_type = candidate.get("doc_type", "")

    if doc_type in SUPPORTED_DISCOVERY_TYPES:
        score += 15
        reasons.append(f"type:{doc_type}")
    else:
        score -= 20
        reasons.append("unsupported-type")

    age = _days_since(candidate.get("updated_at") or candidate.get("created_at"), now=now)
    if age is not None:
        if age <= 7:
            score += 25
            reasons.append("recent")
        elif age <= 30:
            score += 15
            reasons.append("active-month")
        elif age <= 365:
            score += 6
            reasons.append("historical")
    else:
        score -= 8
        reasons.append("missing-time")

    if any(term in searchable for term in BUSINESS_TITLE_TERMS):
        score += 22
        reasons.append("business-title")

    for term in known_project_terms or []:
        if term and term in searchable:
            score += 15
            reasons.append(f"known-project:{term}")
            break

    if candidate.get("opened_at"):
        score += 5
        reasons.append("opened")

    if any(term in title for term in LOW_VALUE_TERMS):
        score -= 30
        reasons.append("low-value-title")

    if not candidate.get("token") and not candidate.get("url"):
        score -= 25
        reasons.append("missing-source")

    if doc_type not in AUTO_READ_TYPES:
        score -= 10
        reasons.append("review-required-type")

    if doc_type in SUPPORTED_DISCOVERY_TYPES and (candidate.get("token") or candidate.get("url")):
        action = "auto_ingest"
    elif score >= REVIEW_THRESHOLD:
        action = "needs_review"
    else:
        action = "defer"

    return score, action, reasons


def classify_candidate(
    raw: Dict[str, Any],
    lane: str = "freshness",
    known_project_terms: Optional[List[str]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    candidate = normalize_candidate(raw, lane=lane)
    score, action, reasons = score_candidate(candidate, known_project_terms=known_project_terms, now=now)
    candidate.update({
        "score": score,
        "action": action,
        "status": "queued" if action == "auto_ingest" else action,
        "reason": ", ".join(reasons),
        "attempt_count": candidate.get("attempt_count", 0),
    })
    return candidate


def infer_doc_type_from_title(title: str) -> str:
    if "会议" in title or "纪要" in title:
        return "会议纪要"
    if "需求" in title:
        return "需求文档"
    if "复盘" in title or "review" in title.lower():
        return "复盘报告"
    if "方案" in title:
        return "运营方案"
    if "合同" in title or "协议" in title:
        return "合作协议"
    if "数据" in title or "分析" in title:
        return "数据分析"
    if "竞品" in title:
        return "竞品调研"
    return "其他"


def extract_time_hints(text: str) -> str:
    patterns = [
        r"\b20\d{2}[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?",
        r"\b\d{1,2}月\d{1,2}日",
        r"\bQ[1-4]\b",
        r"\b\d{3,4}\s*(?:大促|项目|活动)",
    ]
    hints: List[str] = []
    for pattern in patterns:
        hints.extend(re.findall(pattern, text, flags=re.IGNORECASE))
    return ", ".join(dict.fromkeys(hints[:5]))


def infer_tags(title: str, content: str = "") -> str:
    source = f"{title}\n{content[:2000]}"
    tags = [term for term in BUSINESS_TITLE_TERMS if term in source]
    return ", ".join(dict.fromkeys(tags[:8]))


def infer_project_name(title: str) -> str:
    separators = [" - ", "-", "—", "_", "｜", "|", "：", ":", " "]
    clean_title = title.strip() or "未命名项目"
    head = clean_title
    for separator in separators:
        if separator in clean_title:
            head = clean_title.split(separator, 1)[0].strip()
            break

    cleanup_patterns = [
        r"\s*20\d{2}.*$",
        r"\s*Q[1-4].*$",
        r"\s*\d{3,4}.*$",
        r"\s*(?:周例会|例会|讨论会|会议|复盘|方案|需求|纪要).*$",
    ]
    for pattern in cleanup_patterns:
        reduced = re.sub(pattern, "", head, flags=re.IGNORECASE).strip()
        if reduced != head and len(reduced) >= 2:
            head = reduced
            break

    return (head or clean_title)[:40]


def infer_topic_names(
    title: str,
    content: str = "",
    known_project_terms: Optional[List[str]] = None,
) -> List[str]:
    """Return one or more topic tables where this document should appear."""
    source = f"{title}\n{content[:2000]}"
    topics: List[str] = []

    primary = infer_project_name(title)
    if primary:
        topics.append(primary)

    for term in known_project_terms or []:
        term = term.strip()
        if len(term) < 2 or term in {"数据表", "业务上下文"}:
            continue
        if term in source and term not in topics:
            topics.append(term)

    return topics[:5] or ["未命名项目"]
