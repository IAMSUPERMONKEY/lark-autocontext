"""
OKF Writer: Generate OKF-compliant Markdown files from classified JSON.

Input: JSON with classification fields (from Agent) + raw content (from Scanner)
Output: .md file in bundle/projects/{project}/{category}/{filename}

Usage:
  python okf_writer.py '<classified_json>' '<raw_content>'
  
  classified_json example:
  {
    "project": "lark-autocontext",
    "type": "Meeting Minutes",
    "category": "meetings",
    "title": "2026-06-20 重构讨论",
    "description": "讨论 OKF 重构方案",
    "tags": ["重构", "OKF"],
    "people": ["张三", "李四"],
    "key_dates": [{"date": "2026-06-20", "event": "方案确定"}],
    "core_conclusion": "采用 Pipeline 架构...",
    "filename": "2026-06-20-重构讨论.md",
    "resource": "https://feishu.cn/docx/abc123"
  }
"""
import sys
import json
import os
import re
from datetime import datetime

if sys.platform == "win32" and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


# Type to category directory mapping
TYPE_TO_CATEGORY = {
    "Meeting Minutes": "meetings",
    "Requirement Doc": "requirements",
    "Review Report": "reviews",
    "Operation Plan": "plans",
    "Data Analysis": "analysis",
    "Competitor Research": "research",
    "Contract": "contracts",
    "Reference": "references",
    "Metric": "metrics",
    "Other": "misc"
}


def get_bundle_path():
    """Get bundle path from config.json."""
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    bundle_path = "./bundle"
    if os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        bundle_path = config.get("bundle_path", "./bundle")

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not os.path.isabs(bundle_path):
        bundle_path = os.path.join(project_root, bundle_path)
    return bundle_path


def sanitize_filename(name):
    """Remove characters unsafe for filenames."""
    return re.sub(r'[<>:"/\\|?*]', '_', name)


def generate_mentions(classified):
    """Build the `mentions` frontmatter array from classified_json."""
    mentions = []
    for person in classified.get("people") or []:
        if person:
            mentions.append(f"/people/{person}.md")
    for concept in classified.get("concepts") or []:
        if concept:
            mentions.append(f"/concepts/{concept}.md")
    project = classified.get("project")
    if project:
        mentions.append(f"/projects/{project}/index.md")
    return mentions


def generate_related_section(classified):
    """Build the '# Related' markdown section using absolute paths."""
    lines = ["# Related", ""]
    people = [p for p in (classified.get("people") or []) if p]
    concepts = [c for c in (classified.get("concepts") or []) if c]
    project = classified.get("project")
    if people:
        links = ", ".join(f"[{p}](/people/{p}.md)" for p in people)
        lines.append(f"* People: {links}")
    if concepts:
        links = ", ".join(f"[{c}](/concepts/{c}.md)" for c in concepts)
        lines.append(f"* Concepts: {links}")
    if project:
        lines.append(f"* Project: [{project}](/projects/{project}/index.md)")
    return "\n".join(lines)


def validate_description(desc):
    """Validate description per OKF SHOULD: meaningful sentence, ≤100 chars."""
    import re as _re
    if not desc:
        raise ValueError("description is required and must be non-empty")
    desc = desc.strip()
    if _re.match(r"^[A-Za-z][A-Za-z ]+ - .+$", desc):
        raise ValueError(
            f"description appears to be mechanical '{{type}} - {{title}}' pattern: {desc!r}. "
            "Provide a meaningful one-sentence summary."
        )
    if len(desc) > 100:
        desc = desc[:97] + "…"
    return desc


def _now_iso():
    from datetime import datetime
    return datetime.now().astimezone().isoformat()


def generate_frontmatter(classified):
    """Build YAML frontmatter from classified_json."""
    desc = validate_description(classified.get("description", ""))
    tags = classified.get("tags") or []
    people = classified.get("people") or []
    concepts = classified.get("concepts") or []
    timestamp = (
        classified.get("edited_time")
        or classified.get("timestamp")
        or _now_iso()
    )
    mentions = generate_mentions(classified)

    title = classified.get("title", "").replace('"', "'")
    lines = [
        "---",
        f"type: {classified.get('type', 'Other')}",
        f'title: "{title}"',
        f"description: {desc}",
    ]
    if classified.get("resource"):
        lines.append(f"resource: {classified['resource']}")
    if tags:
        lines.append(f"tags: [{', '.join(tags)}]")
    lines.append(f"timestamp: {timestamp}")
    if classified.get("project"):
        lines.append(f"project: {classified['project']}")
    if people:
        lines.append(f"people: [{', '.join(people)}]")
    if concepts:
        lines.append(f"concepts: [{', '.join(concepts)}]")
    if mentions:
        lines.append("mentions:")
        for m in mentions:
            lines.append(f"  - {m}")
    lines.append("---")
    return "\n".join(lines)


def _sanitize_entity_name(name):
    """Strip filesystem-unsafe chars from entity name."""
    return re.sub(r'[<>:"/\\|?*]', '', name).strip()


def _parse_existing_mentions(text):
    """Return raw lines under # Mentioned In until next H1."""
    lines = text.splitlines()
    in_section = False
    out = []
    for line in lines:
        if line.startswith("# Mentioned In"):
            in_section = True
            continue
        if in_section:
            if line.startswith("# "):
                break
            if line.startswith("* ") or line.startswith("- "):
                out.append(line)
    return out


def _extract_section(text, heading):
    """Extract content under a specific H1 heading (exclusive of next H1)."""
    lines = text.splitlines()
    capture = False
    captured = []
    for line in lines:
        if line.strip() == heading:
            capture = True
            continue
        if capture and line.startswith("# "):
            break
        if capture:
            captured.append(line)
    return "\n".join(captured).strip()


def _upsert_entity(bundle_path, entity_type, name, mentioned_concept_id,
                   mentioned_title, mentioned_description, project, timestamp):
    """Shared upsert logic for Person and Concept entities."""
    if entity_type == "Person":
        subdir = "people"
        profile_heading = "# Profile"
        desc_default = "在 lark-autocontext 知识库中出现的人物档案"
    else:
        subdir = "concepts"
        profile_heading = "# Definition"
        desc_default = "业务概念档案"

    safe_name = _sanitize_entity_name(name)
    if not safe_name:
        return None

    entity_dir = os.path.join(bundle_path, subdir)
    os.makedirs(entity_dir, exist_ok=True)
    entity_path = os.path.join(entity_dir, f"{safe_name}.md")

    # Read existing
    profile_content = ""
    existing_mentions = []
    existing_tags = set()
    existing_timestamp = ""
    if os.path.exists(entity_path):
        with open(entity_path, "r", encoding="utf-8") as f:
            existing_text = f.read()
        profile_content = _extract_section(existing_text, profile_heading)
        existing_mentions = _parse_existing_mentions(existing_text)
        tag_match = re.search(r'tags:\s*\[(.*?)\]', existing_text)
        if tag_match:
            existing_tags = {t.strip() for t in tag_match.group(1).split(",") if t.strip()}
        ts_match = re.search(r'timestamp:\s*(\S+)', existing_text)
        if ts_match:
            existing_timestamp = ts_match.group(1)

    if project:
        existing_tags.add(project)
    if timestamp > existing_timestamp:
        new_timestamp = timestamp
    else:
        new_timestamp = existing_timestamp or timestamp

    # New mention line
    new_mention_line = (
        f"* [{mentioned_title}](/{mentioned_concept_id}.md) - {mentioned_description}"
    )

    # Dedupe by concept_id link
    link_marker = f"](/{mentioned_concept_id}.md)"
    deduped = [m for m in existing_mentions if link_marker not in m]
    deduped.insert(0, new_mention_line)

    # Build frontmatter
    tags_str = ", ".join(sorted(existing_tags))
    fm_lines = [
        "---",
        f"type: {entity_type}",
        f"title: {name}",
        f"description: {desc_default}",
    ]
    if tags_str:
        fm_lines.append(f"tags: [{tags_str}]")
    fm_lines.append(f"timestamp: {new_timestamp}")
    fm_lines.append("---")

    body_parts = [
        "\n".join(fm_lines),
        "",
        profile_heading,
        "",
        profile_content if profile_content else "<!-- 占位区，供后续人工补充，脚本永不覆盖 -->",
        "",
        "# Mentioned In",
        "",
        "\n".join(deduped),
        "",
    ]
    with open(entity_path, "w", encoding="utf-8") as f:
        f.write("\n".join(body_parts))
    return entity_path


def upsert_person(bundle_path, name, mentioned_concept_id, mentioned_title,
                  mentioned_description, project, timestamp):
    return _upsert_entity(bundle_path, "Person", name, mentioned_concept_id,
                          mentioned_title, mentioned_description, project, timestamp)


def upsert_concept(bundle_path, name, mentioned_concept_id, mentioned_title,
                   mentioned_description, project, timestamp):
    return _upsert_entity(bundle_path, "Concept", name, mentioned_concept_id,
                          mentioned_title, mentioned_description, project, timestamp)


TYPES_WITH_DECISIONS = {"Meeting Minutes", "Review Report"}
TYPES_WITH_ACTION_ITEMS = {"Meeting Minutes", "Requirement Doc"}


def generate_body(classified, raw_content):
    """Build the OKF-structured markdown body."""
    sections = []

    summary = (classified.get("summary") or "").strip()
    if summary:
        sections.append(f"# Summary\n{summary}")

    key_points = classified.get("key_points") or []
    if key_points:
        kp = ["# Key Points"] + [f"- {p}" for p in key_points if p]
        sections.append("\n".join(kp))

    doc_type = classified.get("type", "")
    decisions = classified.get("decisions") or []
    if decisions and doc_type in TYPES_WITH_DECISIONS:
        dec = ["# Decisions"]
        for d in decisions:
            dec.append(
                f"- **决策**: {d.get('decision', '')} "
                f"**负责人**: {d.get('owner', '')} "
                f"**截止**: {d.get('deadline', '')}"
            )
        sections.append("\n".join(dec))

    action_items = classified.get("action_items") or []
    if action_items and doc_type in TYPES_WITH_ACTION_ITEMS:
        ai = ["# Action Items"]
        for a in action_items:
            owner = f" — @{a.get('owner', '')}" if a.get('owner') else ""
            due = f" — {a.get('due', '')}" if a.get('due') else ""
            ai.append(f"- [ ] {a.get('task', '')}{owner}{due}")
        sections.append("\n".join(ai))

    if raw_content and raw_content.strip():
        sections.append(f"# Source Content\n{raw_content.strip()}")

    has_entities = (
        bool(classified.get("people") or [])
        or bool(classified.get("concepts") or [])
        or bool(classified.get("project"))
    )
    if has_entities:
        sections.append(generate_related_section(classified))

    citations = ["# Citations"]
    resource = classified.get("resource", "")
    if resource:
        citations.append(f"[1] [飞书原文]({resource})")
    sections.append("\n".join(citations))

    return "\n\n".join(sections) + "\n"


def find_existing_file(bundle_path, resource):
    """Find existing file by resource (doc_token) in frontmatter."""
    if not resource:
        return None

    projects_dir = os.path.join(bundle_path, "projects")
    if not os.path.exists(projects_dir):
        return None

    for root, dirs, files in os.walk(projects_dir):
        for fname in files:
            if not fname.endswith('.md') or fname == 'index.md':
                continue
            filepath = os.path.join(root, fname)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                if f"resource: {resource}" in content:
                    return filepath
            except:
                continue
    return None


def update_index_md(dir_path, title, filename, description):
    """Update or create index.md in a directory."""
    index_path = os.path.join(dir_path, "index.md")
    entry = f"* [{title}]({filename}) - {description}\n"

    if os.path.exists(index_path):
        with open(index_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Check if entry already exists
        if f"]({filename})" in content:
            # Update existing entry
            lines = content.split('\n')
            updated_lines = []
            for line in lines:
                if f"]({filename})" in line:
                    updated_lines.append(entry.strip())
                else:
                    updated_lines.append(line)
            content = '\n'.join(updated_lines)
        else:
            # Append new entry
            content = content.rstrip() + '\n' + entry

        with open(index_path, 'w', encoding='utf-8') as f:
            f.write(content)
    else:
        # Create new index.md
        category_name = os.path.basename(dir_path)
        header = f"# {category_name.title()}\n\n"
        with open(index_path, 'w', encoding='utf-8') as f:
            f.write(header + entry)


def update_log_md(bundle_path, action, file_path, title):
    """Append entry to log.md."""
    log_path = os.path.join(bundle_path, "log.md")
    today = datetime.now().strftime('%Y-%m-%d')
    relative_path = os.path.relpath(file_path, bundle_path)
    entry = f"* **{action}**: {title} ([{relative_path}]({relative_path}))\n"

    if os.path.exists(log_path):
        with open(log_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Check if today's section exists
        if f"## {today}" in content:
            # Insert entry after today's header
            content = content.replace(f"## {today}\n", f"## {today}\n{entry}")
        else:
            # Add new day section
            content = content.rstrip() + f"\n\n## {today}\n\n{entry}"

        with open(log_path, 'w', encoding='utf-8') as f:
            f.write(content)
    else:
        with open(log_path, 'w', encoding='utf-8') as f:
            f.write(f"# Change Log\n\n## {today}\n\n{entry}")


def write_okf_document(classified_data, raw_content=""):
    """
    Write an OKF-compliant Markdown file to the Bundle.

    Args:
        classified_data: Dict with project, type, title, tags, etc.
        raw_content: Original document content from Scanner

    Returns:
        Dict with file_path and action (created/updated)
    """
    bundle_path = get_bundle_path()

    # Ensure bundle exists
    if not os.path.exists(bundle_path):
        return {"error": "Bundle not initialized. Run: python scripts/init_bundle.py"}

    project = classified_data.get('project', 'misc')
    doc_type = classified_data.get('type', 'Other')
    category = classified_data.get('category') or TYPE_TO_CATEGORY.get(doc_type, 'misc')
    title = classified_data.get('title', 'Untitled')
    filename = sanitize_filename(classified_data.get('filename', f"{title}.md"))
    description = classified_data.get('description', '')
    resource = classified_data.get('resource', '')

    # Check for existing file (deduplication)
    existing_file = find_existing_file(bundle_path, resource)
    action = "Update" if existing_file else "Creation"

    # Determine target path
    if existing_file:
        target_path = existing_file
    else:
        project_dir = os.path.join(bundle_path, "projects", project, category)
        os.makedirs(project_dir, exist_ok=True)
        target_path = os.path.join(project_dir, filename)

    # Generate file content
    frontmatter = generate_frontmatter(classified_data)
    body = generate_body(classified_data, raw_content)
    file_content = frontmatter + "\n\n" + body + "\n"

    # Write file
    with open(target_path, 'w', encoding='utf-8') as f:
        f.write(file_content)

    # Auto-upsert entities (people / concepts)
    concept_id_for_link = os.path.relpath(target_path, bundle_path).replace(os.sep, "/").replace(".md", "")
    for person in classified_data.get("people") or []:
        upsert_person(bundle_path, person, concept_id_for_link,
                      classified_data.get("title", ""), classified_data.get("description", ""),
                      classified_data.get("project", ""),
                      classified_data.get("edited_time") or classified_data.get("timestamp", ""))
    for concept_name in classified_data.get("concepts") or []:
        upsert_concept(bundle_path, concept_name, concept_id_for_link,
                       classified_data.get("title", ""), classified_data.get("description", ""),
                       classified_data.get("project", ""),
                       classified_data.get("edited_time") or classified_data.get("timestamp", ""))

    # Update index.md in the category directory
    target_dir = os.path.dirname(target_path)
    update_index_md(target_dir, title, filename, description)

    # Update project index.md if it's a new project
    project_index = os.path.join(bundle_path, "projects", project, "index.md")
    if not os.path.exists(project_index):
        with open(project_index, 'w', encoding='utf-8') as f:
            f.write(f"# {project}\n\n* [{title}]({category}/{filename}) - {description}\n")
    else:
        # Check if project is listed in projects/index.md
        projects_index = os.path.join(bundle_path, "projects", "index.md")
        if os.path.exists(projects_index):
            with open(projects_index, 'r', encoding='utf-8') as f:
                content = f.read()
            if f"]({project}/index.md)" not in content and f"]({project}/)" not in content:
                with open(projects_index, 'a', encoding='utf-8') as f:
                    f.write(f"\n* [{project}]({project}/index.md)\n")

    # Update log.md
    update_log_md(bundle_path, action, target_path, title)

    return {
        "action": action,
        "file_path": os.path.relpath(target_path, bundle_path),
        "absolute_path": target_path,
        "title": title
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python okf_writer.py '<classified_json>' [raw_content]")
        print("")
        print("classified_json example:")
        print('  {"project":"my-project","type":"Meeting Minutes","title":"周会","tags":["会议"]}')
        sys.exit(1)

    classified_data = json.loads(sys.argv[1])
    raw_content = sys.argv[2] if len(sys.argv) > 2 else ""

    result = write_okf_document(classified_data, raw_content)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
