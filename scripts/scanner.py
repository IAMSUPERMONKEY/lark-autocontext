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
    """Clean Feishu private tags and normalize whitespace.

    - <title>X</title> → # X
    - <callout ...>X</callout> → X
    - <image .../> → removed entirely
    - <details ...>X</details> → X
    - 3+ blank lines → 1 blank line
    """
    if not raw:
        return raw
    raw = re.sub(r'<title>(.*?)</title>', r'# \1', raw, flags=re.DOTALL)
    raw = re.sub(r'<image[^>]*/>', '', raw)
    raw = re.sub(r'<image[^>]*>.*?</image>', '', raw, flags=re.DOTALL)
    raw = re.sub(r'<callout[^>]*>(.*?)</callout>', r'\1', raw, flags=re.DOTALL)
    raw = re.sub(r'<details[^>]*>(.*?)</details>', r'\1', raw, flags=re.DOTALL)
    raw = re.sub(r'\n{3,}', '\n\n', raw)
    return raw.strip()


def scan_single_doc(url, cli=None):
    """Extract content from a single Feishu document URL."""
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
                "last_modified": datetime.now().isoformat()
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


def scan_batch(config_path=None, cli=None):
    """Scan all sources defined in scan_config.json."""
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
    if len(sys.argv) < 2:
        # Batch mode
        result = scan_batch()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif sys.argv[1] == "--doc":
        if len(sys.argv) < 3:
            print("Usage: python scanner.py --doc <feishu_url>")
            sys.exit(1)
        result = scan_single_doc(sys.argv[2])
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("Usage:")
        print("  python scanner.py              # Batch scan from scan_config.json")
        print("  python scanner.py --doc <url>  # Single document extraction")
        sys.exit(1)


if __name__ == "__main__":
    main()
