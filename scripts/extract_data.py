"""
Extract business context from a Feishu Doc/Sheet link or raw text.
Outputs raw content for Sub-Agent to parse and extract structured fields.
Supported inputs: Feishu Doc links, Feishu Sheet links, raw text.
"""
import sys
import json
import re
from cli import LarkCLI

def is_feishu_doc(url):
    return "feishu.cn/docx/" in url or "larksuite.com/docx/" in url

def is_feishu_sheet(url):
    return "feishu.cn/sheet/" in url or "larksuite.cn/sheet/" in url

def extract_doc_token(url):
    match = re.search(r'docx/([a-zA-Z0-9]+)', url)
    return match.group(1) if match else "N/A"

def main():
    if len(sys.argv) < 2:
        print("Usage: python extract_data.py <feishu_link_or_text>")
        sys.exit(1)

    target = sys.argv[1]
    cli = LarkCLI()

    try:
        if is_feishu_doc(target):
            doc_token = extract_doc_token(target)
            content = cli.fetch_doc(doc_token)
            result = {"source_type": "doc", "doc_token": doc_token, "content": content}
        elif is_feishu_sheet(target):
            sheet_token = re.search(r'sheet/([a-zA-Z0-9]+)', target).group(1)
            content = cli.fetch_sheet(sheet_token, "0")
            result = {"source_type": "sheet", "doc_token": "N/A", "content": content}
        else:
            # Raw text or image description
            result = {"source_type": "text", "doc_token": "N/A", "content": target}

        print(json.dumps(result, ensure_ascii=False))

    except Exception as e:
        print(json.dumps({
            "error": str(e),
            "hint": "If extraction failed (e.g. token expired or doc not accessible), fall back to treating the input as raw text: return {\"source_type\": \"text\", \"doc_token\": \"N/A\", \"content\": target}"
        }, ensure_ascii=False))

if __name__ == "__main__":
    main()
