"""OKF Bundle Visualizer: scan bundle, generate single-file HTML."""
import argparse
import json
import os
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

if sys.platform == "win32" and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

LINK_RE = re.compile(r'\[([^\]]+)\]\(([^)]+\.md)(?:#[^)]*)?\)')
FM_RE = re.compile(r'^---\s*\n(.*?)\n---\s*\n', re.DOTALL)


def _parse_frontmatter(text):
    """Parse YAML frontmatter, return (fm_dict, body_text)."""
    m = FM_RE.match(text)
    if not m:
        return {}, text
    fm = {}
    if yaml:
        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except Exception:
            fm = {}
    else:
        # Fallback: simple key: value parsing
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm, text[m.end():]


def extract_links(md_text):
    """Extract all markdown links to .md files."""
    return [m.group(2) for m in LINK_RE.finditer(md_text)]


def _norm_id(bundle_dir, abs_path):
    """Normalize a file path to a bundle-relative ID with forward slashes."""
    return str(Path(abs_path).resolve().relative_to(Path(bundle_dir).resolve())).replace("\\", "/")


def _resolve_link(bundle_dir, src_id, link):
    """Resolve a markdown link relative to src_id, return bundle-relative ID or None.
    Links starting with / are bundle-root-relative (OKF convention).
    Other links are relative to the source file's directory.
    """
    if link.startswith("/"):
        # Absolute bundle path: strip leading / and resolve from bundle root
        target = Path(bundle_dir) / link[1:]
    else:
        target = (Path(bundle_dir) / src_id).parent / link
    try:
        return _norm_id(bundle_dir, target)
    except (ValueError, OSError):
        return None


def scan_bundle_to_graph(bundle_dir):
    """Scan bundle directory, return {nodes, edges}."""
    nodes = []
    edges = []
    seen_edges = set()

    for md_path in Path(bundle_dir).rglob("*.md"):
        raw = md_path.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(raw)
        nid = _norm_id(bundle_dir, md_path)
        title = fm.get("title") or md_path.stem
        ntype = fm.get("type") or "doc"
        nodes.append({
            "id": nid,
            "label": title,
            "type": ntype,
            "path": str(md_path),
            "body": body,
        })

        targets = set()
        # From mentions frontmatter
        mentions = fm.get("mentions", [])
        if isinstance(mentions, list):
            for ref in mentions:
                if ref:
                    # Normalize: strip leading / for bundle-relative ID
                    targets.add(ref.lstrip("/"))
        # From body links
        for link in extract_links(body):
            r = _resolve_link(bundle_dir, nid, link)
            if r:
                targets.add(r)

        for t in targets:
            key = (nid, t)
            if key in seen_edges:
                continue
            seen_edges.add(key)
            edges.append({"source": nid, "target": t})

    return {"nodes": nodes, "edges": edges}


def compute_cited_by(graph):
    """Add cited_by list to each node (reverse edges)."""
    rev = {}
    for e in graph["edges"]:
        rev.setdefault(e["target"], []).append(e["source"])
    for n in graph["nodes"]:
        n["cited_by"] = rev.get(n["id"], [])
    return graph


HTML_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><title>OKF Bundle Visualizer</title>
<script src="https://unpkg.com/cytoscape@3.28.1/dist/cytoscape.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
  body{margin:0;font-family:system-ui,sans-serif;display:flex;height:100vh}
  #cy{flex:2;border-right:1px solid #ddd}
  #side{flex:1;overflow:auto;padding:16px}
  #search{width:100%;padding:6px;margin-bottom:8px}
  .type-tag{display:inline-block;padding:2px 6px;border-radius:4px;
            background:#eef;font-size:12px;margin-left:6px}
</style></head>
<body>
<div id="cy"></div>
<div id="side">
  <input id="search" placeholder="搜索节点（label / id）...">
  <div id="detail"><em>点击节点查看详情</em></div>
</div>
<script>
const DATA = __DATA__;
const COLORS = {meeting:"#4a90e2", decision:"#e25c4a", "action-item":"#f5a623",
                requirement:"#7ed321", review:"#9013fe", person:"#50e3c2",
                concept:"#bd10e0", doc:"#9b9b9b"};
const elements = [
  ...DATA.nodes.map(n => ({data:{id:n.id, label:n.label, type:n.type}})),
  ...DATA.edges.map(e => ({data:{source:e.source, target:e.target}})),
];
const cy = cytoscape({
  container: document.getElementById("cy"),
  elements,
  layout: {name:"cose", animate:false, idealEdgeLength:120},
  style:[
    {selector:"node", style:{
      "background-color":(ele)=>COLORS[ele.data("type")]||"#9b9b9b",
      "label":"data(label)","font-size":10,"text-wrap":"wrap","text-max-width":80}},
    {selector:"edge", style:{
      "width":1,"line-color":"#bbb","target-arrow-color":"#bbb",
      "target-arrow-shape":"triangle","curve-style":"bezier"}},
    {selector:".faded", style:{"opacity":0.15}},
  ],
});
const byId = Object.fromEntries(DATA.nodes.map(n=>[n.id,n]));
cy.on("tap","node",(e)=>{
  const n = byId[e.target.id()];
  const cited = (n.cited_by||[]).map(c=>"- "+c).join("\\n") || "（无）";
  document.getElementById("detail").innerHTML =
    "<h2>"+n.label+" <span class='type-tag'>"+n.type+"</span></h2>"+
    "<p><code>"+n.id+"</code></p>"+
    marked.parse(n.body || "")+
    "<hr><h3>被引用</h3><pre>"+cited+"</pre>";
});
document.getElementById("search").addEventListener("input",(ev)=>{
  const q = ev.target.value.trim().toLowerCase();
  cy.nodes().removeClass("faded");
  if(!q) return;
  cy.nodes().forEach(n=>{
    const d = n.data();
    if(!(d.label.toLowerCase().includes(q) || d.id.toLowerCase().includes(q))){
      n.addClass("faded");
    }
  });
});
</script></body></html>"""


def render_html(graph):
    """Render graph to single-file HTML."""
    return HTML_TEMPLATE.replace("__DATA__", json.dumps(graph, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser(description="OKF Bundle Visualizer")
    p.add_argument("--bundle", default="bundle", help="Path to bundle directory")
    p.add_argument("--out", default="viz.html", help="Output HTML file path")
    args = p.parse_args()

    graph = compute_cited_by(scan_bundle_to_graph(args.bundle))
    Path(args.out).write_text(render_html(graph), encoding="utf-8")
    print(f"[visualize] wrote {args.out} ({len(graph['nodes'])} nodes, {len(graph['edges'])} edges)")


if __name__ == "__main__":
    main()
