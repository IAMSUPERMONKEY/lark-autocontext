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


# Type → category directory mapping.
# OKF SPEC §1 Non-goals 明确不定义固定 taxonomy，所以这里只为少量"工程友好"
# 的别名兜底，其他 type 一律走 slugify_type() 自动派生目录名。
TYPE_TO_CATEGORY_ALIASES = {
    "Meeting Minutes": "meetings",
    "Requirement Doc": "requirements",
    "Review Report": "reviews",
    "Operation Plan": "plans",
    "Data Analysis": "analysis",
    "Competitor Research": "research",
    "Contract": "contracts",
    "Reference": "references",
    "Metric": "metrics",
    "Other": "misc",
}


def slugify_type(type_value):
    """Convert any free-form type value into a filesystem-safe category slug.

    Examples:
        "Meeting Minutes" -> "meeting-minutes"
        "ADR"             -> "adr"
        "Contract Clause" -> "contract-clause"
        "Postmortem"      -> "postmortem"
        "实验记录"        -> "实验记录"  (Chinese kept verbatim, only spaces collapsed)
    """
    if not type_value:
        return "misc"
    if type_value in TYPE_TO_CATEGORY_ALIASES:
        return TYPE_TO_CATEGORY_ALIASES[type_value]
    s = str(type_value).strip().lower()
    s = re.sub(r'[\s_/]+', '-', s)
    s = re.sub(r'[<>:"\\|?*]', '', s)
    s = s.strip('-') or "misc"
    return s


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
                   mentioned_title, mentioned_description, project, timestamp,
                   dir_slug=None):
    """Shared upsert logic for atomic entities (Person / Concept / arbitrary).

    Args:
        dir_slug: Override subdirectory name. When None, defaults are:
            Person -> "people", Concept -> "concepts",
            anything else -> slugify_type(entity_type) (plural-ish handled by slugify).
    """
    if entity_type == "Person":
        subdir = dir_slug or "people"
        profile_heading = "# Profile"
        desc_default = mentioned_description or "在知识库中出现的人物档案"
    elif entity_type == "Concept":
        subdir = dir_slug or "concepts"
        profile_heading = "# Definition"
        desc_default = mentioned_description or "业务概念档案"
    else:
        subdir = dir_slug or slugify_type(entity_type)
        profile_heading = "# Description"
        desc_default = mentioned_description or f"{entity_type} 实体档案"

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


def generate_directory_index(dir_path, heading=None):
    """Generate an OKF-conformant index.md for a directory by reading
    each child .md file's frontmatter description."""
    if heading is None:
        heading = os.path.basename(dir_path) or "Items"

    entries = []
    for name in sorted(os.listdir(dir_path)):
        if not name.endswith(".md") or name in ("index.md", "log.md"):
            continue
        path = os.path.join(dir_path, name)
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        title_match = re.search(r'^title:\s*"?([^"\n]+)"?\s*$', text, re.MULTILINE)
        desc_match = re.search(r'^description:\s*(.+)$', text, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else name.replace(".md", "")
        desc = desc_match.group(1).strip() if desc_match else ""
        entries.append(f"* [{title}]({name}) - {desc}")

    lines = [f"# {heading}", ""] + entries + [""]
    return "\n".join(lines)


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


def _update_root_types_seen(bundle_path, types_seen):
    """Maintain the root bundle/index.md, listing all top-level entity dirs.

    OKF SPEC: bundle/index.md is the root navigation. It should enumerate
    `projects/` plus any atomic-entity directories (people/, concepts/, and
    any custom slugified types like metrics/, contracts/, ...).

    This function is idempotent: it rewrites the root index based on what
    actually exists under `bundle_path` plus any newly created `types_seen`.
    """
    index_path = os.path.join(bundle_path, "index.md")

    # Discover all entity directories under bundle/ (exclude internal ones).
    skip = {".git", ".obsidian", "projects"}
    entity_dirs = []
    if os.path.isdir(bundle_path):
        for name in sorted(os.listdir(bundle_path)):
            full = os.path.join(bundle_path, name)
            if not os.path.isdir(full):
                continue
            if name.startswith(".") or name in skip:
                continue
            entity_dirs.append(name)

    # Ensure freshly-seen types are reflected even if dir was just created.
    for t in types_seen or []:
        slug = slugify_type(t)
        if slug and slug not in entity_dirs and os.path.isdir(os.path.join(bundle_path, slug)):
            entity_dirs.append(slug)
    entity_dirs = sorted(set(entity_dirs))

    lines = ["# Bundle", ""]
    if os.path.isdir(os.path.join(bundle_path, "projects")):
        lines.append("* [Projects](projects/index.md) - 所有项目入口")
    for d in entity_dirs:
        sub_index = os.path.join(bundle_path, d, "index.md")
        link = f"{d}/index.md" if os.path.exists(sub_index) else f"{d}/"
        lines.append(f"* [{d}]({link})")
    lines.append("")

    with open(index_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _auto_visualize(bundle_path):
    """Auto-generate viz.html after each write. Non-blocking.

    Returns the relative path to viz.html on success, or None on failure.
    Failures are logged to stderr but do not break the write operation.
    """
    try:
        import subprocess
        viz_path = os.path.join(bundle_path, "viz.html")
        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        visualize_script = os.path.join(scripts_dir, "visualize.py")
        r = subprocess.run(
            [sys.executable, visualize_script,
             "--bundle", bundle_path, "--out", viz_path],
            capture_output=True, text=True, timeout=30
        )
        if r.returncode == 0:
            return os.path.relpath(viz_path, bundle_path)
        else:
            print(f"[okf_writer] visualize warning: {r.stderr.strip()}",
                  file=sys.stderr)
            return None
    except Exception as e:
        print(f"[okf_writer] visualize skipped: {e}", file=sys.stderr)
        return None


def _check_subagent_signature(classified_data):
    """Warn if classified JSON lacks the subagent signature.

    Per SKILL.md 铁律 1, classification MUST be done by a subagent, not the
    main conversation. Subagents are instructed to include
    `"_classified_by": "subagent"` in their output. If this field is missing,
    the classification was likely done by the main conversation (violation).

    This is a non-blocking warning: we still write the file, but the warning
    makes the violation visible in logs for later auditing.
    """
    sig = classified_data.get("_classified_by") if isinstance(classified_data, dict) else None
    if sig != "subagent":
        print(
            "\n"
            "⚠️⚠️⚠️ [okf_writer] SUBAGENT SIGNATURE MISSING ⚠️⚠️⚠️\n"
            "  classified JSON does not contain `_classified_by: \"subagent\"`.\n"
            "  This likely means classification was done by the main conversation,\n"
            "  which violates SKILL.md 铁律 1 (分类必须由 subagent 执行).\n"
            "  → Classification quality is NOT guaranteed.\n"
            "  → Please re-run with proper subagent classification.\n"
            "  See SKILL.md '🛑 STOP — 执行前必读' section.\n"
            "⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️\n",
            file=sys.stderr
        )


def write_okf_document(classified_data, raw_content="", skip_visualize=False):
    """
    Write an OKF-compliant Markdown file to the Bundle.

    Args:
        classified_data: Dict with project, type, title, tags, etc.
            Must contain `_classified_by: "subagent"` per SKILL.md 铁律 1.
        raw_content: Original document content from Scanner
        skip_visualize: If True, skip auto-viz (for batch mode — caller
            should call _auto_visualize() once after all writes).

    Returns:
        Dict with file_path and action (created/updated)
    """
    # Subagent signature check (SKILL.md 铁律 1 enforcement)
    _check_subagent_signature(classified_data)

    bundle_path = get_bundle_path()

    # Ensure bundle exists
    if not os.path.exists(bundle_path):
        return {"error": "Bundle not initialized. Run: python scripts/init_bundle.py"}

    project = classified_data.get('project', 'misc')
    doc_type = classified_data.get('type', 'Other')
    # category 由 type 自动派生（OKF SPEC 不预设固定 taxonomy）。
    # 如果 LLM 显式给了 category，尊重它；否则 slugify。
    category = classified_data.get('category') or slugify_type(doc_type)
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

    # Generic atomic-entity upsert (SKILL Classification Guide §5)
    # entities: [{"type": "Metric", "name": "GMV", "brief": "..."}, ...]
    extra_types_seen = set()
    for ent in classified_data.get("entities") or []:
        if not isinstance(ent, dict):
            continue
        ent_type = ent.get("type")
        ent_name = ent.get("name")
        if not ent_type or not ent_name:
            continue
        ent_brief = ent.get("brief") or ent.get("description") or ""
        ent_slug = slugify_type(ent_type)
        _upsert_entity(bundle_path, ent_type, ent_name, concept_id_for_link,
                       classified_data.get("title", ""), ent_brief,
                       classified_data.get("project", ""),
                       classified_data.get("edited_time") or classified_data.get("timestamp", ""),
                       dir_slug=ent_slug)
        extra_types_seen.add(ent_type)

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

    # Maintain root bundle/index.md (lists projects/ + all entity dirs)
    _update_root_types_seen(bundle_path, extra_types_seen | {"Person", "Concept"})

    # Auto-generate viz.html so the user always has a fresh visual output
    # after each write. Non-blocking: failures are logged but don't break the write.
    # In batch mode (skip_visualize=True), caller handles viz generation once at end.
    viz_path = None if skip_visualize else _auto_visualize(bundle_path)

    # After the OKF file is written to disk, update the search index
    # (best-effort). Index update failure should not break the main write flow.
    try:
        from query_engine import QueryEngine
        engine = QueryEngine(bundle_path)
        engine.update_index(target_path)
    except Exception as e:
        import logging
        logging.debug(f"Index update skipped: {e}")

    return {
        "action": action,
        "file_path": os.path.relpath(target_path, bundle_path),
        "absolute_path": target_path,
        "title": title,
        "viz_html": viz_path,
    }


def generate_index_pages(bundle_path=None):
    """Generate navigation index pages for the bundle.

    Scans all .md files in the bundle, groups them by project and type,
    and generates index.md files for navigation.

    Args:
        bundle_path: Path to the bundle directory. If None, uses default from config.

    Returns:
        dict: {"projects_index": path, "total_docs": count}
    """
    from wiki_connector import _parse_frontmatter

    if bundle_path is None:
        config_path = os.path.join(os.path.dirname(__file__), "config.json")
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            bundle_path = config.get("bundle_path", os.path.join(os.path.dirname(__file__), "..", "bundle"))
        except (FileNotFoundError, json.JSONDecodeError):
            bundle_path = os.path.join(os.path.dirname(__file__), "..", "bundle")

    # Scan all .md files (skip hidden dirs, index.md, log.md)
    docs_by_project = {}  # {project: [(path, title, type), ...]}
    docs_by_type = {}     # {type: [(path, title, project), ...]}
    all_people = {}       # {name: [paths]}
    all_concepts = {}     # {concept: [paths]}

    for root, dirs, files in os.walk(bundle_path):
        # Skip hidden directories
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for fname in files:
            if not fname.endswith('.md'):
                continue
            if fname in ('index.md', 'log.md'):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    file_content = f.read()
                fm, _ = _parse_frontmatter(file_content)
                title = fm.get("title", fname)
                doc_type = fm.get("type", "Other")
                project = fm.get("project", "ungrouped")
                people = fm.get("people", [])
                concepts = fm.get("concepts", [])

                rel_path = os.path.relpath(fpath, bundle_path)

                docs_by_project.setdefault(project, []).append((rel_path, title, doc_type))
                docs_by_type.setdefault(doc_type, []).append((rel_path, title, project))

                for p in (people if isinstance(people, list) else [people]):
                    if p:
                        all_people.setdefault(p, []).append(rel_path)
                for c in (concepts if isinstance(concepts, list) else [concepts]):
                    if c:
                        all_concepts.setdefault(c, []).append(rel_path)
            except Exception:
                continue

    # Generate projects index
    projects_index_path = os.path.join(bundle_path, "projects", "index.md")
    os.makedirs(os.path.dirname(projects_index_path), exist_ok=True)
    with open(projects_index_path, "w", encoding="utf-8") as f:
        f.write("# Projects Index\n\n")
        for project in sorted(docs_by_project.keys()):
            f.write(f"## {project}\n\n")
            for path, title, doc_type in sorted(docs_by_project[project]):
                f.write(f"- [{title}]({path}) ({doc_type})\n")
            f.write("\n")
        # People section
        if all_people:
            f.write("## People\n\n")
            for person in sorted(all_people.keys()):
                f.write(f"- {person} ({len(all_people[person])} docs)\n")
            f.write("\n")
        # Concepts section
        if all_concepts:
            f.write("## Concepts\n\n")
            for concept in sorted(all_concepts.keys()):
                f.write(f"- {concept} ({len(all_concepts[concept])} docs)\n")

    total_docs = sum(len(v) for v in docs_by_project.values())

    return {
        "projects_index": projects_index_path,
        "total_docs": total_docs,
    }



def main():
    """Entry point with multiple input modes (to avoid shell quoting issues).

    Modes (in priority order):
      1. --batch-file <path> [--no-visualize]
         Batch mode: read JSON array of [{classified, raw_content}, ...].
         Writes all docs, generates viz.html ONCE at end (unless --no-visualize).
      2. --classified-file <path> [--content-file <path>] [--no-visualize]
         Single doc mode: read classified JSON (and optional raw content) from files.
      3. --stdin
         Read JSON object from stdin: {"classified": {...}, "raw_content": "..."}
      4. <classified_json> [<raw_content>]
         Legacy positional args (kept for backward compatibility).
    """
    import argparse
    parser = argparse.ArgumentParser(
        description="OKF Writer: generate OKF Markdown from classified data.",
        add_help=False,
    )
    parser.add_argument("--batch-file",
                        help="Batch mode: path to JSON array of {classified, raw_content} objects")
    parser.add_argument("--classified-file", help="Path to a JSON file with classified data")
    parser.add_argument("--content-file", help="Path to a text file with raw content")
    parser.add_argument("--no-visualize", action="store_true",
                        help="Skip auto viz.html generation (useful in batch mode)")
    parser.add_argument("--stdin", action="store_true",
                        help='Read {"classified":{...},"raw_content":"..."} from stdin')
    parser.add_argument("-h", "--help", action="store_true")
    # Allow extra positional args for legacy mode.
    args, rest = parser.parse_known_args()

    if args.help:
        print(
            "Usage:\n"
            "  python okf_writer.py --batch-file batch.json [--no-visualize]\n"
            "  python okf_writer.py --classified-file classified.json [--content-file body.md]\n"
            "  cat payload.json | python okf_writer.py --stdin\n"
            "  python okf_writer.py '<classified_json>' [raw_content]   # legacy\n"
            "\n"
            "batch.json format:\n"
            '  [{"classified": {...}, "raw_content": "..."}, ...]\n'
            "\n"
            "classified_json example:\n"
            '  {"project":"my-project","type":"Meeting Minutes","title":"周会","tags":["会议"]}'
        )
        sys.exit(0)

    # === Batch mode ===
    if args.batch_file:
        with open(args.batch_file, "r", encoding="utf-8") as f:
            items = json.load(f)
        if not isinstance(items, list):
            print("Error: --batch-file expects a JSON array", file=sys.stderr)
            sys.exit(1)

        results = []
        bundle_path = get_bundle_path()
        for i, item in enumerate(items):
            classified = item.get("classified") or item
            raw = item.get("raw_content", "")
            # Support content_file reference (read file content)
            if not raw and item.get("content_file"):
                with open(item["content_file"], "r", encoding="utf-8") as cf:
                    raw = cf.read()
            r = write_okf_document(classified, raw, skip_visualize=True)
            results.append(r)
            print(f"[batch {i+1}/{len(items)}] {r.get('action','?')}: "
                  f"{r.get('title','?')} -> {r.get('file_path','?')}")

        # Generate viz.html ONCE after all writes (unless --no-visualize)
        viz_path = None
        if not args.no_visualize:
            print(f"[batch] generating viz.html for {len(results)} documents...")
            viz_path = _auto_visualize(bundle_path)

        print(json.dumps({
            "batch_count": len(results),
            "results": results,
            "viz_html": viz_path,
        }, ensure_ascii=False, indent=2))
        return

    # === Single doc mode ===
    classified_data = None
    raw_content = ""

    if args.classified_file:
        with open(args.classified_file, "r", encoding="utf-8") as f:
            classified_data = json.load(f)
        if args.content_file:
            with open(args.content_file, "r", encoding="utf-8") as f:
                raw_content = f.read()
    elif args.stdin:
        payload = json.load(sys.stdin)
        classified_data = payload.get("classified") or payload
        raw_content = payload.get("raw_content", "")
    elif rest:
        classified_data = json.loads(rest[0])
        raw_content = rest[1] if len(rest) > 1 else ""
    else:
        print("Error: missing input. Run with --help for usage.", file=sys.stderr)
        sys.exit(1)

    result = write_okf_document(classified_data, raw_content,
                                skip_visualize=args.no_visualize)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
