"""
Scanner: Extract content from Feishu documents.
Two modes:
  1. Batch scan: python scanner.py (reads scan_config.json)
  2. Single doc: python scanner.py --doc "https://feishu.cn/docx/xxx"

Output: JSON with document content for Agent classification.
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

from cli import LarkCLI


def _get_wiki_connector():
    """Create a WikiConnector from config.json. Returns None if not configured."""
    try:
        config_path = os.path.join(os.path.dirname(__file__), "config.json")
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        wiki = config.get("wiki", {})
        if not wiki.get("space_id"):
            return None
        from wiki_connector import WikiConnector
        return WikiConnector(
            space_id=wiki["space_id"],
            raw_node_token=wiki.get("raw_node_token", ""),
            agent_node_token=wiki.get("agent_node_token", ""),
            identity=config.get("identity", "user"),
        )
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return None


def is_feishu_doc(url):
    return "feishu.cn/docx/" in url or "larksuite.com/docx/" in url

def is_feishu_sheet(url):
    return "feishu.cn/sheet/" in url or "larksuite.cn/sheet/" in url

def extract_doc_token(url):
    match = re.search(r'docx/([a-zA-Z0-9]+)', url)
    return match.group(1) if match else "N/A"

def extract_sheet_token(url):
    match = re.search(r'sheet/([a-zA-Z0-9]+)', url)
    return match.group(1) if match else "N/A"


def clean_feishu_content(raw: str) -> str:
    """Clean Feishu HTML/private tags and normalize to pure Markdown.

    Feishu's markdown export often contains residual HTML tags that would
    produce mixed HTML+Markdown in OKF files. This function converts or
    strips them:
      - <h1>~<h6> → Markdown headings
      - <strong>/<b> → **bold**
      - <em>/<i> → *italic*
      - <p>, <div>, <span> → strip tags, keep content
      - <br> → newline
      - <table>/<tr>/<td>/<th> → strip tags, keep text (tab-separated)
      - <ul>/<ol>/<li> → strip tags, keep text
      - <cite> → strip tag, keep content
      - <title> → # heading
      - <image> → removed
      - <callout>/<details> → unwrap
    """
    if not raw:
        return raw

    # Headings: <h1>X</h1> → # X, etc.
    for level in range(1, 7):
        raw = re.sub(rf'<h{level}[^>]*>(.*?)</h{level}>',
                     rf'{"#" * level} \1', raw, flags=re.DOTALL)

    # Inline formatting
    raw = re.sub(r'<(?:strong|b)[^>]*>(.*?)</(?:strong|b)>', r'**\1**', raw, flags=re.DOTALL)
    raw = re.sub(r'<(?:em|i)[^>]*>(.*?)</(?:em|i)>', r'*\1*', raw, flags=re.DOTALL)

    # Line break
    raw = re.sub(r'<br\s*/?>', '\n', raw)

    # Table cells: strip tags, keep text (tab-separated for readability)
    raw = re.sub(r'<t[dh][^>]*>(.*?)</t[dh]>', r'\1\t', raw, flags=re.DOTALL)
    raw = re.sub(r'</tr>', '\n', raw)
    raw = re.sub(r'<tr[^>]*>', '', raw)
    raw = re.sub(r'</?(?:table|thead|tbody|tfoot)[^>]*>', '', raw)

    # Lists: strip tags, keep content with bullet
    raw = re.sub(r'<li[^>]*>(.*?)</li>', r'- \1\n', raw, flags=re.DOTALL)
    raw = re.sub(r'</?(?:ul|ol)[^>]*>', '', raw)

    # Block-level containers: unwrap
    raw = re.sub(r'<(?:p|div|span|cite)[^>]*>(.*?)</(?:p|div|span|cite)>',
                 r'\1', raw, flags=re.DOTALL)

    # Feishu-specific tags
    raw = re.sub(r'<title>(.*?)</title>', r'# \1', raw, flags=re.DOTALL)
    raw = re.sub(r'<image[^>]*/>', '', raw)
    raw = re.sub(r'<image[^>]*>.*?</image>', '', raw, flags=re.DOTALL)
    raw = re.sub(r'<callout[^>]*>(.*?)</callout>', r'\1', raw, flags=re.DOTALL)
    raw = re.sub(r'<details[^>]*>(.*?)</details>', r'\1', raw, flags=re.DOTALL)

    # Strip any remaining HTML tags we didn't handle above
    raw = re.sub(r'<[^>]+>', '', raw)

    # Normalize whitespace
    raw = re.sub(r'\n{3,}', '\n\n', raw)
    raw = re.sub(r'[ \t]+\n', '\n', raw)
    return raw.strip()


def _normalize_changed_entry(raw, source_key):
    """Normalize a raw Feishu file/doc dict into the changed-entry shape."""
    doc_token = raw.get("token") or raw.get("obj_token") or raw.get("doc_token", "")
    url = raw.get("url") or f"https://feishu.cn/docx/{doc_token}"
    title = raw.get("name") or raw.get("title") or doc_token
    edited_time = raw.get("edit_time") or raw.get("edited_time") or raw.get("modified_time", "")
    return {
        "doc_token": doc_token,
        "url": url,
        "title": title,
        "edited_time": edited_time,
        "source": source_key,
    }


def list_changed(sources, since):
    """List documents that changed across all configured sources since `since`."""
    cli = LarkCLI()
    changed = []
    source_results = {}
    for src in sources:
        src_type = src.get("type")
        token = src.get("token", "")
        source_key = f"{src_type}:{token}"
        try:
            if src_type == "folder":
                files = cli.fetch_folder_files_since(token, since)
            elif src_type == "wiki":
                files = cli.fetch_wiki_changed_since(token, since)
            elif src_type == "bitable":
                files = [{"token": token, "name": src.get("name", token),
                          "edit_time": since}]
            else:
                files = []
            for f in files:
                changed.append(_normalize_changed_entry(f, source_key))
            source_results[source_key] = {"ok": True, "error": None}
        except Exception as e:
            source_results[source_key] = {"ok": False, "error": str(e)}
    return {"changed": changed, "source_results": source_results}


def scan_single_doc(url, cli=None, use_wiki=False, wiki_connector=None):
    """Extract content from a single Feishu document URL.

    Args:
        url: Feishu doc URL (folder mode) or wiki node URL/token (wiki mode)
        cli: LarkCLI instance (folder mode)
        use_wiki: If True, use WikiConnector instead of LarkCLI
        wiki_connector: WikiConnector instance (if None and use_wiki=True,
            will try to create one from config.json)
    """
    if use_wiki:
        if wiki_connector is None:
            wiki_connector = _get_wiki_connector()
        if wiki_connector is None:
            return {"error": "Wiki mode not configured. Set wiki.space_id in config.json"}

        # Extract node_token from URL or use as-is.
        # Wiki URLs look like: https://feishu.cn/wiki/{node_token}
        node_token = url
        if "/wiki/" in url:
            match = re.search(r'wiki/([a-zA-Z0-9_]+)', url)
            node_token = match.group(1) if match else url

        try:
            content = wiki_connector.fetch_doc_content(node_token)
            meta = wiki_connector.fetch_doc_meta(node_token)
            return {
                "source_type": "wiki_doc",
                "doc_token": node_token,
                "node_token": node_token,
                "title": meta.title,
                "url": url,
                "content": content,
                "fetched_at": datetime.now().isoformat(),
                "last_modified": meta.modified_time,
            }
        except Exception as e:
            return {"error": str(e), "hint": f"Failed to fetch wiki node {node_token}"}

    # Legacy folder mode (existing behaviour unchanged)
    if cli is None:
        cli = LarkCLI()

    try:
        if is_feishu_doc(url):
            doc_token = extract_doc_token(url)
            content = cli.fetch_doc(doc_token)
            content = clean_feishu_content(content)
            title = cli.fetch_doc_title(doc_token)
            return {
                "source_type": "doc",
                "doc_token": doc_token,
                "title": title,
                "url": url,
                "content": content,
                "fetched_at": datetime.now().isoformat(),
                "last_modified": cli.fetch_doc_metadata(doc_token).get("edited_time") or datetime.now().isoformat()
            }
        elif is_feishu_sheet(url):
            sheet_token = extract_sheet_token(url)
            content = cli.fetch_sheet(sheet_token, "0")
            return {
                "source_type": "sheet",
                "doc_token": sheet_token,
                "title": sheet_token,
                "url": url,
                "content": content,
                "fetched_at": datetime.now().isoformat(),
                "last_modified": datetime.now().isoformat()
            }
        else:
            return {
                "source_type": "text",
                "doc_token": "N/A",
                "title": "Raw Text",
                "url": url,
                "content": url,
                "fetched_at": datetime.now().isoformat(),
                "last_modified": datetime.now().isoformat()
            }
    except Exception as e:
        return {
            "error": str(e),
            "hint": "If extraction failed, treat input as raw text."
        }


def scan_batch(config_path=None, cli=None, use_wiki=False, wiki_connector=None):
    """Scan all sources. In wiki mode, scans the raw docs area.

    Args:
        config_path: Path to scan_config.json (folder mode only)
        cli: LarkCLI instance (folder mode)
        use_wiki: If True, use WikiConnector to scan wiki raw area
        wiki_connector: WikiConnector instance
    """
    if use_wiki:
        if wiki_connector is None:
            wiki_connector = _get_wiki_connector()
        if wiki_connector is None:
            return {"error": "Wiki mode not configured"}

        documents = []
        errors = []
        try:
            docs = wiki_connector.list_raw_docs()
            for doc in docs:
                if doc.obj_type == "docx":
                    try:
                        content = wiki_connector.fetch_doc_content(doc.node_token)
                        documents.append({
                            "source_type": "wiki_doc",
                            "doc_token": doc.node_token,
                            "node_token": doc.node_token,
                            "title": doc.title,
                            "url": doc.url,
                            "content": content,
                            "source_name": "wiki_raw",
                            "fetched_at": datetime.now().isoformat(),
                            "last_modified": doc.modified_time,
                        })
                    except Exception as e:
                        errors.append({"node_token": doc.node_token, "error": str(e)})
        except Exception as e:
            return {"error": str(e)}

        return {
            "scanned_at": datetime.now().isoformat(),
            "total_documents": len(documents),
            "documents": documents,
            "errors": errors,
        }

    # Legacy folder mode (existing behaviour unchanged)
    if cli is None:
        cli = LarkCLI()

    if config_path is None:
        config_path = os.path.join(os.path.dirname(__file__), "scan_config.json")

    if not os.path.exists(config_path):
        return {"error": "scan_config.json not found. Create it from scan_config.json.example"}

    with open(config_path, 'r', encoding='utf-8') as f:
        scan_config = json.load(f)

    sources = scan_config.get("sources", [])
    documents = []
    errors = []

    for source in sources:
        source_type = source.get("type")
        token = source.get("token")
        name = source.get("name", "Unknown")

        try:
            if source_type == "wiki":
                nodes = cli.fetch_wiki_tree(token)
                for node in nodes:
                    node_type = node.get("obj_type", "")
                    node_token = node.get("obj_token", "")
                    node_title = node.get("title", node_token)

                    if node_type == "docx" and node_token:
                        try:
                            content = cli.fetch_doc(node_token)
                            content = clean_feishu_content(content)
                            documents.append({
                                "source_type": "doc",
                                "doc_token": node_token,
                                "title": node_title,
                                "url": f"https://feishu.cn/docx/{node_token}",
                                "content": content,
                                "source_name": name,
                                "fetched_at": datetime.now().isoformat(),
                                "last_modified": node.get("obj_edit_time", "")
                            })
                        except Exception as e:
                            errors.append({"token": node_token, "error": str(e)})

            elif source_type == "folder":
                files = cli.fetch_folder_files(token)
                for f_info in files:
                    file_type = f_info.get("type", "").upper()
                    file_token = f_info.get("token", "")
                    file_name = f_info.get("name", file_token)
                    file_url = f_info.get("url", f"https://feishu.cn/docx/{file_token}")

                    if file_type == "DOCX" and file_token:
                        try:
                            content = cli.fetch_doc(file_token)
                            content = clean_feishu_content(content)
                            documents.append({
                                "source_type": "doc",
                                "doc_token": file_token,
                                "title": file_name,
                                "url": file_url,
                                "content": content,
                                "source_name": name,
                                "fetched_at": datetime.now().isoformat(),
                                "last_modified": f_info.get("modified_time", "")
                            })
                        except Exception as e:
                            errors.append({"token": file_token, "error": str(e)})

                    elif file_type == "SHEET" and file_token:
                        try:
                            content = cli.fetch_sheet(file_token, "0")
                            documents.append({
                                "source_type": "sheet",
                                "doc_token": file_token,
                                "title": file_name,
                                "url": file_url,
                                "content": content,
                                "source_name": name,
                                "fetched_at": datetime.now().isoformat(),
                                "last_modified": f_info.get("modified_time", "")
                            })
                        except Exception as e:
                            errors.append({"token": file_token, "error": str(e)})

            elif source_type == "bitable":
                tables_output = cli.run(["base", "+table-list", "--base-token", token])
                tables_data = json.loads(tables_output)
                tables = tables_data.get("data", {}).get("tables", [])

                for table in tables:
                    table_id = table.get("id")
                    table_name = table.get("name", "Unknown")

                    records_output = cli.run([
                        "base", "+record-list",
                        "--base-token", token,
                        "--table-id", table_id
                    ])
                    records_data = json.loads(records_output)

                    fields = records_data.get("data", {}).get("fields", [])
                    rows = records_data.get("data", {}).get("data", [])

                    for row in rows:
                        record = {}
                        for i, field_name in enumerate(fields):
                            if i < len(row):
                                val = row[i]
                                if isinstance(val, list):
                                    val = ", ".join(str(v) for v in val)
                                record[field_name] = val

                        content = json.dumps(record, ensure_ascii=False, indent=2)
                        documents.append({
                            "source_type": "bitable_record",
                            "doc_token": f"{table_id}_{record.get('id', '')}",
                            "title": record.get("实体名称", table_name),
                            "url": f"https://feishu.cn/base/{token}?table={table_id}",
                            "content": content,
                            "source_name": name,
                            "fetched_at": datetime.now().isoformat(),
                            "last_modified": record.get("最后更新", "")
                        })

        except Exception as e:
            errors.append({"source": name, "error": str(e)})

    return {
        "scanned_at": datetime.now().isoformat(),
        "total_documents": len(documents),
        "documents": documents,
        "errors": errors
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Feishu document scanner")
    parser.add_argument('--doc', help='Scan a single Feishu document URL')
    parser.add_argument('--list-changed', action='store_true',
                        help='List changed documents since --since')
    parser.add_argument('--since', help='ISO 8601 timestamp for incremental scan')
    parser.add_argument('--wiki', action='store_true',
                        help='Use wiki mode (scan wiki raw docs area)')
    args = parser.parse_args()

    if args.list_changed:
        config_path = os.path.join(os.path.dirname(__file__), "scan_config.json")
        if not os.path.exists(config_path):
            print(json.dumps({"changed": [], "source_results": {},
                              "error": "scan_config.json missing"}, ensure_ascii=False))
            sys.exit(1)
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        sources = cfg.get("sources", [])
        result = list_changed(sources, since=args.since or "2000-01-01T00:00:00+08:00")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.doc:
        result = scan_single_doc(args.doc, use_wiki=args.wiki)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        # Batch mode (default)
        result = scan_batch(use_wiki=args.wiki)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
