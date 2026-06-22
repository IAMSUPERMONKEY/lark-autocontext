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

SKIP_FILENAMES = {"index.md", "log.md"}


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
    """Scan bundle directory, return {nodes, edges}.
    Skips index.md / log.md (directory indices and changelog, not knowledge nodes).

    Edges:
      - "explicit": frontmatter `mentions` + body markdown links
      - "tag": derived from shared tags (weak, dashed in UI)
      - "type": derived from same type (very weak, only when no other edges exist
                between the pair, to avoid dense clutter)
    """
    nodes = []
    explicit_edges = []
    seen_explicit = set()

    for md_path in Path(bundle_dir).rglob("*.md"):
        if md_path.name in SKIP_FILENAMES:
            continue
        raw = md_path.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(raw)
        nid = _norm_id(bundle_dir, md_path)
        title = fm.get("title") or md_path.stem
        ntype = fm.get("type") or "doc"
        tags = fm.get("tags") or []
        if not isinstance(tags, list):
            tags = [str(tags)]
        nodes.append({
            "id": nid,
            "label": title,
            "type": ntype,
            "description": fm.get("description", ""),
            "resource": fm.get("resource", ""),
            "tags": tags,
            "path": str(md_path),
            "body": body,
        })

        targets = set()
        mentions = fm.get("mentions", [])
        if isinstance(mentions, list):
            for ref in mentions:
                if ref:
                    targets.add(ref.lstrip("/"))
        for link in extract_links(body):
            r = _resolve_link(bundle_dir, nid, link)
            if r and Path(r).name not in SKIP_FILENAMES:
                targets.add(r)

        for t in targets:
            key = (nid, t)
            if key in seen_explicit:
                continue
            seen_explicit.add(key)
            explicit_edges.append({"source": nid, "target": t, "kind": "explicit"})

    derived_edges = _derive_implicit_edges(nodes, seen_explicit)
    return {"nodes": nodes, "edges": explicit_edges + derived_edges}


def _derive_implicit_edges(nodes, seen_explicit):
    """Derive weak edges from shared tags / same type so a sparse bundle
    still has visual structure.

    Strategy:
      1. For each tag, connect all node pairs sharing it as `kind=tag`.
      2. For each type, if two nodes have NO explicit/tag edge between them,
         add a `kind=type` edge — but cap per-type to avoid O(N^2) clutter.
    Both source and target are stored undirected (we emit a single direction
    deterministically: the lexicographically smaller id first).
    """
    derived = []
    seen_pair = set()

    def _pair_key(a, b):
        return (a, b) if a < b else (b, a)

    for a, b in seen_explicit:
        seen_pair.add(_pair_key(a, b))

    # Tag-based edges
    tag_to_nodes = {}
    for n in nodes:
        for tag in n.get("tags", []) or []:
            tag_to_nodes.setdefault(tag, []).append(n["id"])

    for tag, ids in tag_to_nodes.items():
        if len(ids) < 2:
            continue
        ids = sorted(ids)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                key = _pair_key(ids[i], ids[j])
                if key in seen_pair:
                    continue
                seen_pair.add(key)
                derived.append({"source": ids[i], "target": ids[j],
                                "kind": "tag", "tag": tag})

    # Type-based edges (only if a type has 2-6 nodes; cap to a chain to avoid clutter)
    type_to_nodes = {}
    for n in nodes:
        type_to_nodes.setdefault(n["type"], []).append(n["id"])

    for ntype, ids in type_to_nodes.items():
        if len(ids) < 2 or len(ids) > 6:
            continue
        ids = sorted(ids)
        # Chain-link (i -> i+1) so we get a backbone, not a clique
        for i in range(len(ids) - 1):
            key = _pair_key(ids[i], ids[i + 1])
            if key in seen_pair:
                continue
            seen_pair.add(key)
            derived.append({"source": ids[i], "target": ids[i + 1],
                            "kind": "type", "tag": ntype})

    return derived


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
<script src="https://unpkg.com/layout-base@2.0.1/layout-base.js"></script>
<script src="https://unpkg.com/cose-base@2.2.0/cose-base.js"></script>
<script src="https://unpkg.com/cytoscape-cose-bilkent@4.1.0/cytoscape-cose-bilkent.js"></script>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
  *{box-sizing:border-box}
  body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
       display:flex;flex-direction:column;height:100vh;background:#fafafa;color:#222}
  #header{display:flex;align-items:center;gap:12px;padding:10px 16px;
          background:#fff;border-bottom:1px solid #e5e5e5;flex-shrink:0}
  #header h1{margin:0;font-size:16px;font-weight:600}
  #stats{font-size:12px;color:#666;margin-right:auto}
  #search{padding:6px 10px;border:1px solid #ddd;border-radius:6px;font-size:13px;width:220px}
  #type-filter{padding:6px 10px;border:1px solid #ddd;border-radius:6px;font-size:13px;background:#fff}
  #reset{padding:6px 12px;border:1px solid #ddd;border-radius:6px;background:#fff;cursor:pointer;font-size:13px}
  #reset:hover{background:#f0f0f0}
  #main{flex:1;display:flex;overflow:hidden}
  #cy{flex:2;background:#fafafa}
  #side{flex:1;max-width:420px;min-width:300px;overflow:auto;padding:18px;
        background:#fff;border-left:1px solid #e5e5e5}
  #side h2{margin:0 0 8px;font-size:18px}
  #side h3{margin:18px 0 8px;font-size:13px;color:#666;text-transform:uppercase;letter-spacing:0.5px}
  #side .meta{font-size:12px;color:#666;line-height:1.7}
  #side .meta code{background:#f5f5f5;padding:2px 6px;border-radius:3px;font-size:11px}
  #side .desc{color:#444;font-size:13px;margin:8px 0;font-style:italic}
  #side .body{font-size:13px;line-height:1.6}
  #side .body pre{background:#f7f7f7;padding:10px;border-radius:4px;overflow:auto}
  #side .body code{background:#f5f5f5;padding:2px 4px;border-radius:3px}
  #side .cited a{display:block;padding:4px 0;color:#0969da;text-decoration:none;font-size:12px}
  #side .cited a:hover{text-decoration:underline}
  .type-tag{display:inline-block;padding:2px 8px;border-radius:10px;background:#eef;
            font-size:11px;font-weight:500;vertical-align:middle;margin-left:6px;color:#333}
  .tag-pill{display:inline-block;padding:2px 8px;border-radius:10px;background:#f0f0f0;
            font-size:11px;margin:2px 4px 2px 0;color:#555}
  .empty{color:#999;font-style:italic;font-size:13px}
</style></head>
<body>
<div id="header">
  <h1>OKF Bundle</h1>
  <span id="stats"></span>
  <select id="type-filter"><option value="">所有类型</option></select>
  <input id="search" placeholder="🔍 搜索 label / id / tag...">
  <button id="reset">重置</button>
</div>
<div id="main">
  <div id="cy"></div>
  <div id="side"><div class="empty">点击节点查看详情</div></div>
</div>
<script>
const DATA = __DATA__;
const COLORS = {
  meeting:"#4a90e2", decision:"#e25c4a", "action-item":"#f5a623",
  requirement:"#7ed321", review:"#9013fe", person:"#50e3c2",
  concept:"#bd10e0", project:"#ff6b9d", doc:"#9b9b9b"
};
const SIZES = {project:55, person:50, concept:45, decision:42, meeting:40};

// Compute node degree (for size scaling)
const degree = {};
DATA.edges.forEach(e => {
  degree[e.source] = (degree[e.source]||0) + 1;
  degree[e.target] = (degree[e.target]||0) + 1;
});

const elements = [
  ...DATA.nodes.map(n => ({data:{
    id:n.id, label:n.label, type:n.type,
    color: COLORS[n.type] || COLORS.doc,
    size: (SIZES[n.type] || 38) + Math.min((degree[n.id]||0) * 3, 20),
  }})),
  ...DATA.edges.map(e => ({data:{
    source:e.source, target:e.target,
    kind: e.kind || "explicit",
    tag: e.tag || "",
  }})),
];

const hasBilkent = (typeof cytoscape !== "undefined") &&
                   cytoscape.prototype && cytoscape("layout","cose-bilkent");
// cose-bilkent params tuned for sparse Chinese-label bundles:
// large nodeRepulsion + idealEdgeLength prevents label collision.
const layoutCfg = hasBilkent
  ? {name:"cose-bilkent", animate:false, randomize:true,
     idealEdgeLength:180, nodeRepulsion:24000, edgeElasticity:0.45,
     gravity:0.25, numIter:3000, tile:true, padding:60}
  : {name:"cose", animate:false, randomize:true,
     idealEdgeLength:200, nodeRepulsion:()=>32000,
     nodeOverlap:60, padding:60, gravity:40, numIter:2500};

const cy = cytoscape({
  container: document.getElementById("cy"),
  elements,
  layout: layoutCfg,
  wheelSensitivity: 0.2,
  style:[
    {selector:"node", style:{
      "background-color":"data(color)",
      "label":"data(label)",
      "font-size":12, "font-weight":500,
      "text-wrap":"wrap", "text-max-width":110,
      "text-valign":"center", "text-halign":"right",
      "text-margin-x":6,
      "color":"#222",
      "text-background-color":"#fff",
      "text-background-opacity":0.85,
      "text-background-padding":2,
      "text-background-shape":"roundrectangle",
      "width":"data(size)", "height":"data(size)",
      "border-width":2, "border-color":"#fff",
    }},
    {selector:"node:selected", style:{
      "border-color":"#0969da", "border-width":3,
    }},
    {selector:"edge", style:{
      "width":1.4, "line-color":"#94a3b8",
      "target-arrow-color":"#94a3b8",
      "target-arrow-shape":"triangle",
      "curve-style":"bezier", "arrow-scale":0.8,
    }},
    {selector:'edge[kind = "tag"]', style:{
      "line-style":"dashed", "line-color":"#cbd5e0",
      "target-arrow-shape":"none",
      "width":1, "opacity":0.6,
    }},
    {selector:'edge[kind = "type"]', style:{
      "line-style":"dotted", "line-color":"#e2e8f0",
      "target-arrow-shape":"none",
      "width":1, "opacity":0.5,
    }},
    {selector:".faded", style:{"opacity":0.12}},
    {selector:"edge.faded", style:{"opacity":0.05}},
  ],
});

const byId = Object.fromEntries(DATA.nodes.map(n=>[n.id,n]));

// Populate type filter and stats
const typeSet = new Set(DATA.nodes.map(n => n.type));
const typeFilter = document.getElementById("type-filter");
[...typeSet].sort().forEach(t => {
  const opt = document.createElement("option");
  opt.value = t; opt.textContent = t;
  typeFilter.appendChild(opt);
});
document.getElementById("stats").textContent =
  `${DATA.nodes.length} 节点 · ${DATA.edges.length} 边` +
  (DATA.edges.some(e=>e.kind && e.kind!=="explicit")
    ? `（含 ${DATA.edges.filter(e=>e.kind && e.kind!=="explicit").length} 条隐式：虚线=同 tag，点线=同 type）`
    : "") +
  ` · ${typeSet.size} 类型`;

function escapeHtml(s){
  return String(s).replace(/[&<>"']/g, c =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;","'":"&#39;"})[c]);
}

function renderDetail(n){
  const tagsHtml = (n.tags||[]).length
    ? (n.tags||[]).map(t=>`<span class="tag-pill">${escapeHtml(t)}</span>`).join("")
    : '<span class="empty">无</span>';
  const citedHtml = (n.cited_by||[]).length
    ? `<div class="cited">${(n.cited_by||[])
        .map(c=>`<a href="#" data-id="${escapeHtml(c)}">↳ ${escapeHtml(c)}</a>`).join("")}</div>`
    : '<div class="empty">无</div>';
  const resourceHtml = n.resource
    ? `<a href="${escapeHtml(n.resource)}" target="_blank">${escapeHtml(n.resource)}</a>`
    : '<span class="empty">无</span>';

  document.getElementById("detail-container").innerHTML = `
    <h2>${escapeHtml(n.label)} <span class="type-tag" style="background:${COLORS[n.type]||'#eef'};color:#fff">${escapeHtml(n.type)}</span></h2>
    ${n.description ? `<div class="desc">${escapeHtml(n.description)}</div>` : ''}
    <div class="meta">
      <div><strong>ID:</strong> <code>${escapeHtml(n.id)}</code></div>
      <div><strong>Resource:</strong> ${resourceHtml}</div>
      <div><strong>Tags:</strong> ${tagsHtml}</div>
    </div>
    <h3>正文</h3>
    <div class="body">${marked.parse(n.body || "")}</div>
    <h3>被引用 (${(n.cited_by||[]).length})</h3>
    ${citedHtml}
  `;

  // Wire cited-by links to focus the referenced node
  document.querySelectorAll("#detail-container .cited a").forEach(a => {
    a.addEventListener("click", ev => {
      ev.preventDefault();
      const id = a.dataset.id;
      const target = cy.getElementById(id);
      if (target.length){
        cy.elements().unselect();
        target.select();
        cy.animate({center:{eles:target}, zoom:1.5}, {duration:300});
        const node = byId[id];
        if (node) renderDetail(node);
      }
    });
  });
}

document.getElementById("side").innerHTML = '<div id="detail-container"><div class="empty">点击节点查看详情</div></div>';

cy.on("tap","node",(e)=>{
  const n = byId[e.target.id()];
  if (n) renderDetail(n);
});

function applyFilters(){
  const q = document.getElementById("search").value.trim().toLowerCase();
  const t = document.getElementById("type-filter").value;
  cy.elements().removeClass("faded");
  if(!q && !t) return;
  cy.nodes().forEach(node=>{
    const d = node.data();
    const n = byId[d.id] || {};
    const matchQ = !q || d.label.toLowerCase().includes(q)
                       || d.id.toLowerCase().includes(q)
                       || (n.tags||[]).some(tag=>tag.toLowerCase().includes(q));
    const matchT = !t || d.type === t;
    if(!(matchQ && matchT)) node.addClass("faded");
  });
  cy.edges().forEach(edge=>{
    if(edge.source().hasClass("faded") || edge.target().hasClass("faded"))
      edge.addClass("faded");
  });
}

document.getElementById("search").addEventListener("input", applyFilters);
document.getElementById("type-filter").addEventListener("change", applyFilters);
document.getElementById("reset").addEventListener("click", ()=>{
  document.getElementById("search").value = "";
  document.getElementById("type-filter").value = "";
  cy.elements().removeClass("faded");
  cy.fit(undefined, 50);
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
