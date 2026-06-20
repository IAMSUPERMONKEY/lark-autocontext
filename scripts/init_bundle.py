"""
Initialize the OKF Bundle directory structure.
Creates: bundle/index.md, bundle/log.md, bundle/projects/index.md
"""
import os
import sys
import json
from datetime import datetime

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


def init_bundle(bundle_path=None):
    """Create the OKF Bundle directory structure."""
    if bundle_path is None:
        config_path = os.path.join(os.path.dirname(__file__), "config.json")
        bundle_path = "./bundle"
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                config = json.load(f)
            bundle_path = config.get("bundle_path", "./bundle")

    # Resolve relative to project root (parent of scripts/)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not os.path.isabs(bundle_path):
        bundle_path = os.path.join(project_root, bundle_path)

    dirs_to_create = [
        bundle_path,
        os.path.join(bundle_path, "projects"),
        os.path.join(bundle_path, "concepts"),
        os.path.join(bundle_path, "people"),
    ]

    for d in dirs_to_create:
        os.makedirs(d, exist_ok=True)

    # Create index.md files
    index_files = {
        os.path.join(bundle_path, "index.md"): "# Lark AutoContext OKF Bundle\n\n## Projects\n\n* [View all projects](projects/index.md)\n\n## Concepts\n\n* [View concepts](concepts/index.md)\n",
        os.path.join(bundle_path, "projects", "index.md"): "# Projects\n\nNo projects yet. Run a scan or save a document to create one.\n",
        os.path.join(bundle_path, "concepts", "index.md"): "# Concepts\n\nNo concepts yet.\n",
        os.path.join(bundle_path, "people", "index.md"): "# People\n\nNo people yet.\n",
    }

    for filepath, content in index_files.items():
        if not os.path.exists(filepath):
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)

    # Create log.md
    log_path = os.path.join(bundle_path, "log.md")
    if not os.path.exists(log_path):
        with open(log_path, 'w', encoding='utf-8') as f:
            f.write(f"# Change Log\n\n## {datetime.now().strftime('%Y-%m-%d')}\n\n* **Initialization**: Bundle created\n")

    print(f"✅ OKF Bundle initialized at: {bundle_path}")
    print(f"   - index.md, log.md created")
    print(f"   - projects/, concepts/, people/ directories created")
    return bundle_path


if __name__ == "__main__":
    init_bundle()
