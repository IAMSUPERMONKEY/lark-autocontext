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
        # Fallback: simple key: value parsing (used when pyyaml is not installed).
        # Handles `tags: [a, b, c]` flow sequences so derived edges work.
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if v.startswith("[") and v.endswith("]"):
                    inner = v[1:-1].strip()
                    fm[k] = [x.strip().strip('"').strip("'")
                             for x in inner.split(",") if x.strip()] if inner else []
                else:
                    fm[k] = v
    return fm, text[m.end():]


def _normalize_tags(raw):
    """Coerce tags into a clean list[str].

    Handles three malformed cases observed in the wild:
      1. None / "" → []
      2. "[a, b, c]" (string with brackets, from buggy fallback parser) → ["a","b","c"]
      3. ["[a, b, c]"] (list containing a single bracketed string) → ["a","b","c"]
    """
    if not raw:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return [str(raw)]
    out = []
    for item in raw:
        s = str(item).strip()
        if s.startswith("[") and s.endswith("]"):
            inner = s[1:-1].strip()
            if inner:
                out.extend(x.strip().strip('"').strip("'")
                           for x in inner.split(",") if x.strip())
        elif s:
            out.append(s.strip('"').strip("'"))
    return out


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

    Edges: only "explicit" — frontmatter `mentions` + body markdown links.
    OKF SPEC §5.3 treats all links as untyped relationships; we do not derive
    pseudo-edges from shared tags or shared type (those mask true sparsity).
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
        tags = _normalize_tags(fm.get("tags"))
        nodes.append({
            "id": nid,
            "label": title,
            "type": ntype,
            "description": fm.get("description", ""),
            "resource": fm.get("resource", ""),
            "tags": tags,
            "timestamp": fm.get("timestamp", ""),
            "project": fm.get("project", ""),
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

    # OKF SPEC §5.3：A link from concept A to B 表达一种 untyped 关系。
    # 我们只采纳 frontmatter `mentions` + 正文 markdown 链接，不再派生 tag/type 边
    # ——派生边会产生"伪结构"，掩盖真实的稀疏问题；正确做法是让 Agent 在
    # 抽取阶段创建真实的 mentions 链接（见 SKILL.md §5）。

    # 过滤掉引用不存在节点的边（target 指向 index.md / 已删除文件 / 外部链接）
    # 否则 cytoscape 初始化时会因找不到目标节点而报错
    node_ids = {n["id"] for n in nodes}
    valid_edges = [e for e in explicit_edges
                   if e["source"] in node_ids and e["target"] in node_ids]
    return {"nodes": nodes, "edges": valid_edges}


def infer_edges(graph):
    """Add inferred edges based on shared tags and document order.

    Adds edges with kind='inferred_tag' or kind='inferred_order' to
    graph['edges']. Does not duplicate edges where an explicit or
    same-type inferred edge already exists between the same pair.
    """
    nodes = graph["nodes"]
    existing_edges = graph["edges"]

    # Build dedup set: (source, target, kind) and (target, source, kind)
    seen = set()
    for e in existing_edges:
        seen.add((e["source"], e["target"], e["kind"]))
        seen.add((e["target"], e["source"], e["kind"]))

    new_edges = []

    # --- 1. Shared tag edges (chain, not clique) ---
    tag_groups = {}
    for n in nodes:
        for tag in n.get("tags", []):
            tag_groups.setdefault(tag, []).append(n)
    for tag, group in tag_groups.items():
        if len(group) < 2:
            continue
        group_sorted = sorted(group, key=lambda n: n["label"])
        for i in range(len(group_sorted) - 1):
            a, b = group_sorted[i], group_sorted[i + 1]
            key = (a["id"], b["id"], "inferred_tag")
            rev_key = (b["id"], a["id"], "inferred_tag")
            if (a["id"], b["id"], "explicit") in seen or \
               (b["id"], a["id"], "explicit") in seen:
                continue
            if key in seen or rev_key in seen:
                continue
            new_edges.append({
                "source": a["id"], "target": b["id"],
                "kind": "inferred_tag",
            })
            seen.add(key)
            seen.add(rev_key)

    # --- 2. Document order edges (by timestamp, fallback filename) ---
    def _sort_key(n):
        ts = n.get("timestamp", "")
        return (ts, n["id"]) if ts else ("", n["id"])

    sorted_nodes = sorted(nodes, key=_sort_key)
    for i in range(len(sorted_nodes) - 1):
        a, b = sorted_nodes[i], sorted_nodes[i + 1]
        key = (a["id"], b["id"], "inferred_order")
        if key in seen:
            continue
        if (a["id"], b["id"], "explicit") in seen or \
           (b["id"], a["id"], "explicit") in seen:
            continue
        new_edges.append({
            "source": a["id"], "target": b["id"],
            "kind": "inferred_order",
        })
        seen.add(key)

    graph["edges"] = existing_edges + new_edges
    return graph


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
// 颜色策略（对齐 OKF SPEC：consumer 必须容忍未知 type）
// 1) 少量明显类别给手动调色板，保证图谱核心节点视觉稳定
// 2) 其他任何 type 一律走 stableColor() 哈希取色 —— 永远不会"全灰"
const COLORS_BY_TYPE = {
  "Person":            "#50e3c2",
  "Concept":           "#bd10e0",
  "Project":           "#ff6b9d",
  "Meeting Minutes":   "#4a90e2",
  "Requirement Doc":   "#7ed321",
  "Review Report":     "#9013fe",
  "Operation Plan":    "#5b8def",
  "Data Analysis":     "#36c5b0",
  "Reference":         "#9b9b9b",
  "Metric":            "#f5a623",
  "Contract":          "#c97064",
  "Other":             "#9b9b9b"
};

// HSL 哈希调色：同一 type 在任何 bundle 里都取同一颜色，可读且去重
function stableColor(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = ((h << 5) - h) + s.charCodeAt(i);
    h |= 0;
  }
  const hue = Math.abs(h) % 360;
  // 中等饱和、中等亮度，避免荧光色和死灰
  return `hsl(${hue}, 62%, 55%)`;
}
function colorForType(t) {
  if (!t) return "#9b9b9b";
  return COLORS_BY_TYPE[t] || stableColor(t);
}

const SIZES_BY_TYPE = {"Project":55, "Person":50, "Concept":45, "Meeting Minutes":40};

// Compute node degree (for size scaling)
const degree = {};
DATA.edges.forEach(e => {
  degree[e.source] = (degree[e.source]||0) + 1;
  degree[e.target] = (degree[e.target]||0) + 1;
});

// Build enriched two-line label: Title + subtitle (newline via JS template literal)
// subtitle prefers tags[0..1], falls back to date(timestamp) or project.
function _shortDate(ts){
  if(!ts) return "";
  const m = String(ts).match(/^(\\d{4}-\\d{2}-\\d{2})/);
  return m ? m[1] : "";
}
function _subtitle(n){
  const tags = (n.tags||[]).filter(Boolean).slice(0,2);
  if(tags.length) return tags.join(" · ");
  const d = _shortDate(n.timestamp);
  if(d) return d;
  if(n.project) return n.project;
  return "";
}
function _composeLabel(n){
  const sub = _subtitle(n);
  return sub ? `${n.label}\n${sub}` : n.label;
}

const elements = [
  ...DATA.nodes.map(n => ({data:{
    id:n.id, label:_composeLabel(n), title:n.label, type:n.type,
    color: colorForType(n.type),
    size: (SIZES_BY_TYPE[n.type] || 38) + Math.min((degree[n.id]||0) * 3, 20),
  }})),
  ...DATA.edges.map(e => ({data:{
    source:e.source, target:e.target,
    kind: e.kind || "explicit",
    tag: e.tag || "",
  }})),
];

// 检测 cose-bilkent 是否可用（try/catch 防止插件未加载时抛异常中断 JS）
let hasBilkent = false;
try {
  hasBilkent = (typeof cytoscape !== "undefined") &&
               cytoscape.prototype && cytoscape("layout","cose-bilkent");
} catch (e) {
  hasBilkent = false;
}
// 布局配置：优先 cose-bilkent，不可用时 fallback 到内置 cose
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
      "text-wrap":"wrap", "text-max-width":140,
      "line-height":1.25,
      "text-valign":"center", "text-halign":"right",
      "text-margin-x":8,
      "color":"#222",
      "text-background-color":"#fff",
      "text-background-opacity":0.9,
      "text-background-padding":3,
      "text-background-shape":"roundrectangle",
      "text-border-width":1,
      "text-border-color":"#e5e5e5",
      "text-border-opacity":1,
      "width":"data(size)", "height":"data(size)",
      "border-width":2, "border-color":"#fff",
    }},
    {selector:"node:selected", style:{
      "border-color":"#0969da", "border-width":3,
    }},
    // Explicit edges: solid + arrow, the strongest visual weight.
    {selector:"edge", style:{
      "width":2, "line-color":"#64748b",
      "target-arrow-color":"#64748b",
      "target-arrow-shape":"triangle",
      "curve-style":"bezier", "arrow-scale":0.9,
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
  `${DATA.nodes.length} 节点 · ${DATA.edges.length} 边 · ${typeSet.size} 类型`;

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
    <h2>${escapeHtml(n.label)} <span class="type-tag" style="background:${colorForType(n.type)};color:#fff">${escapeHtml(n.type)}</span></h2>
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


def _json_default(obj):
    """JSON encoder fallback: handle datetime objects from YAML parsing."""
    import datetime
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


# Inline version of HTML_TEMPLATE with JS libraries embedded directly
# (for offline / file:// protocol support)
HTML_TEMPLATE_INLINE = HTML_TEMPLATE.replace(
    '<script src="https://unpkg.com/cytoscape@3.28.1/dist/cytoscape.min.js"></script>',
    '<script>__CYTOSCAPE_JS__</script>'
).replace(
    '<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>',
    '<script>__MARKED_JS__</script>'
)


# Local JS library paths (bundled in scripts/lib/)
_LIB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib")

# Map CDN URLs to local filenames
_LOCAL_JS_FILES = {
    "https://unpkg.com/cytoscape@3.28.1/dist/cytoscape.min.js": "cytoscape.min.js",
    "https://cdn.jsdelivr.net/npm/marked/marked.min.js": "marked.min.js",
}

# Cache for loaded JS libraries (avoid re-reading on every render)
_JS_CACHE = {}

def _fetch_js(url):
    """Load JS library content: local file first, then CDN fallback.

    Priority:
    1. Read from scripts/lib/ (bundled, no network needed)
    2. Download from CDN (requires network)
    3. Return None (caller falls back to CDN <script> tag)
    """
    if url in _JS_CACHE:
        return _JS_CACHE[url]

    # Try local bundled file first
    local_filename = _LOCAL_JS_FILES.get(url)
    if local_filename:
        local_path = os.path.join(_LIB_DIR, local_filename)
        if os.path.exists(local_path):
            try:
                with open(local_path, "r", encoding="utf-8") as f:
                    content = f.read()
                _JS_CACHE[url] = content
                return content
            except Exception:
                pass

    # Fallback: download from CDN
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode("utf-8")
        _JS_CACHE[url] = content
        return content
    except Exception:
        _JS_CACHE[url] = None
        return None


def render_html(graph):
    """Render graph to single-file HTML.

    JS libraries (cytoscape, marked) are inlined for offline viewing.
    Falls back to CDN <script> tags if download fails.
    """
    cyto_js = _fetch_js("https://unpkg.com/cytoscape@3.28.1/dist/cytoscape.min.js")
    marked_js = _fetch_js("https://cdn.jsdelivr.net/npm/marked/marked.min.js")

    if cyto_js and marked_js:
        # Both libraries downloaded successfully — inline them for offline use
        html = HTML_TEMPLATE_INLINE.replace("__CYTOSCAPE_JS__", cyto_js)
        html = html.replace("__MARKED_JS__", marked_js)
    else:
        # Fallback: use CDN script tags (requires internet)
        html = HTML_TEMPLATE

    return html.replace("__DATA__", json.dumps(graph, ensure_ascii=False, default=_json_default))


def main():
    p = argparse.ArgumentParser(description="OKF Bundle Visualizer")
    p.add_argument("--bundle", default="bundle", help="Path to bundle directory")
    p.add_argument("--out", default="viz.html", help="Output HTML file path")
    args = p.parse_args()

    graph = scan_bundle_to_graph(args.bundle)
    graph = infer_edges(graph)
    graph = compute_cited_by(graph)
    Path(args.out).write_text(render_html(graph), encoding="utf-8")
    print(f"[visualize] wrote {args.out} ({len(graph['nodes'])} nodes, {len(graph['edges'])} edges)")


if __name__ == "__main__":
    main()
