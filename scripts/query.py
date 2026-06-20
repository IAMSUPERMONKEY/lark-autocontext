"""
Query Engine: Search the OKF Bundle for context.

Three modes:
  1. Project-scoped: python query.py --project <name> --keyword <keyword>
  2. Global search:  python query.py --keyword <keyword>
  3. Type filter:    python query.py --type <type>

Output: JSON with matching concept documents.
"""
import sys
import json
import os
import re

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


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


def parse_frontmatter(content):
    """Parse YAML frontmatter from Markdown content. Returns dict."""
    if not content.startswith("---"):
        return {}

    end_idx = content.find("---", 3)
    if end_idx == -1:
        return {}

    frontmatter_str = content[3:end_idx].strip()
    result = {}

    for line in frontmatter_str.split('\n'):
        line = line.strip()
        if ':' in line:
            key, _, value = line.partition(':')
            key = key.strip()
            value = value.strip()

            # Parse list values: [item1, item2]
            if value.startswith('[') and value.endswith(']'):
                items = [item.strip().strip('"').strip("'") for item in value[1:-1].split(',')]
                result[key] = [item for item in items if item]
            # Parse quoted strings
            elif value.startswith('"') and value.endswith('"'):
                result[key] = value[1:-1]
            else:
                result[key] = value

    return result


def extract_body_preview(content, max_chars=200):
    """Extract body text (after frontmatter) for preview."""
    if content.startswith("---"):
        end_idx = content.find("---", 3)
        if end_idx != -1:
            body = content[end_idx + 3:].strip()
        else:
            body = content
    else:
        body = content

    # Remove markdown headers for cleaner preview
    body = re.sub(r'^#+\s+', '', body, flags=re.MULTILINE)
    body = body.replace('\n', ' ').strip()

    if len(body) > max_chars:
        body = body[:max_chars] + "..."
    return body


def scan_bundle(bundle_path, project_filter=None, type_filter=None, keyword=None):
    """Scan the OKF Bundle and return matching documents."""
    results = []
    projects_dir = os.path.join(bundle_path, "projects")

    if not os.path.exists(projects_dir):
        return results

    # Determine search root
    if project_filter:
        search_root = os.path.join(projects_dir, project_filter)
        if not os.path.exists(search_root):
            return results
    else:
        search_root = projects_dir

    # Walk through all .md files (excluding index.md)
    for root, dirs, files in os.walk(search_root):
        for fname in files:
            if not fname.endswith('.md') or fname == 'index.md':
                continue

            filepath = os.path.join(root, fname)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
            except:
                continue

            frontmatter = parse_frontmatter(content)

            # Apply type filter
            if type_filter and frontmatter.get('type', '') != type_filter:
                continue

            # Apply keyword filter
            if keyword:
                keyword_lower = keyword.lower()
                searchable_text = (
                    frontmatter.get('title', '') + ' ' +
                    frontmatter.get('description', '') + ' ' +
                    ' '.join(frontmatter.get('tags', [])) + ' ' +
                    ' '.join(frontmatter.get('people', [])) + ' ' +
                    content
                ).lower()

                if keyword_lower not in searchable_text:
                    continue

            # Build result entry
            relative_path = os.path.relpath(filepath, bundle_path)
            concept_id = relative_path.replace('\\', '/').replace('.md', '')

            result = {
                "concept_id": concept_id,
                "title": frontmatter.get('title', fname),
                "type": frontmatter.get('type', 'Unknown'),
                "description": frontmatter.get('description', ''),
                "tags": frontmatter.get('tags', []),
                "timestamp": frontmatter.get('timestamp', ''),
                "resource": frontmatter.get('resource', ''),
                "project": frontmatter.get('project', ''),
                "people": frontmatter.get('people', []),
                "body_preview": extract_body_preview(content),
                "file_path": relative_path.replace('\\', '/')
            }
            results.append(result)

    # Sort by timestamp descending
    results.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    return results


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Query OKF Bundle")
    parser.add_argument('--project', help='Filter by project name')
    parser.add_argument('--keyword', help='Search keyword')
    parser.add_argument('--type', help='Filter by document type')
    args = parser.parse_args()

    bundle_path = get_bundle_path()

    if not os.path.exists(bundle_path):
        print(json.dumps({
            "error": "Bundle not initialized. Run: python scripts/init_bundle.py"
        }, ensure_ascii=False))
        sys.exit(1)

    results = scan_bundle(
        bundle_path,
        project_filter=args.project,
        type_filter=args.type,
        keyword=args.keyword
    )

    # Determine mode
    if args.project:
        mode = "project"
    elif args.type:
        mode = "type"
    else:
        mode = "global"

    output = {
        "query": args.keyword or args.type or args.project or "",
        "mode": mode,
        "count": len(results),
        "results": results
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
