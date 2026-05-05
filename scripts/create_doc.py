"""
Create a Feishu Document from AI summary content.
Returns the document URL.
"""
import sys
import json
import re
from cli import LarkCLI

def create_doc(title, content):
    cli = LarkCLI()
    
    # Create Feishu Doc using v2 API with markdown format
    output = cli.run(["docs", "+create", "--api-version", "v2", "--title", title, "--content", content, "--doc-format", "markdown"])
    
    # Parse output to find document_id
    try:
        data = json.loads(output)
        doc_id = data.get("data", {}).get("document", {}).get("document_id")
        if doc_id:
            # Use the tenant domain from lark-cli config, fallback to generic format
            return f"https://your-tenant.feishu.cn/docx/{doc_id}"
    except:
        # Fallback regex
        match = re.search(r'(docx/[a-zA-Z0-9]+)', output)
        if match:
            return f"https://your-tenant.feishu.cn/{match.group(1)}"
            
    return "Creation Failed"

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python create_doc.py '<Title>' '<Content (JSON escaped)>'")
        sys.exit(1)
    
    title = sys.argv[1]
    content = sys.argv[2] # Expecting raw text/markdown content
    
    url = create_doc(title, content)
    print(url)
