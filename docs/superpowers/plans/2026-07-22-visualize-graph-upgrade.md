# Visualize Graph Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade viz.html graph visualization with inferred edges (shared tags + document order), hover highlight, zoom/pan tuning, and visual improvements.

**Architecture:** In-place enhancement of `scripts/visualize.py`. New `infer_edges()` Python function inserts between `scan_bundle_to_graph()` and `compute_cited_by()`. HTML template gets updated Cytoscape style selectors, interaction handlers, and a legend overlay. All changes in one file plus a new test file.

**Tech Stack:** Python 3, Cytoscape.js 3.28.1 (already bundled), pytest

**Spec:** `docs/superpowers/specs/2026-07-22-visualize-graph-upgrade-design.md`

---

### Task 1: Edge Inference — Shared Tags

**Files:**
- Modify: `scripts/visualize.py` (add `infer_edges` function after `scan_bundle_to_graph`, around line 168)
- Test: `tests/unit/test_visualize.py` (create new file)

- [ ] **Step 1: Write failing tests for tag-based edge inference**

```python
"""Unit tests for visualize.py edge inference and graph building."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))

from visualize import infer_edges


def test_infer_edges_shared_tags():
    """Two nodes sharing a tag get an inferred_tag edge."""
    graph = {
        "nodes": [
            {"id": "a.md", "label": "A", "type": "doc", "tags": ["OKR", "目标"], "timestamp": ""},
            {"id": "b.md", "label": "B", "type": "doc", "tags": ["OKR"], "timestamp": ""},
        ],
        "edges": [],
    }
    result = infer_edges(graph)
    tag_edges = [e for e in result["edges"] if e["kind"] == "inferred_tag"]
    assert len(tag_edges) == 1
    assert {tag_edges[0]["source"], tag_edges[0]["target"]} == {"a.md", "b.md"}


def test_infer_edges_no_shared_tags():
    """Nodes with no common tags get no inferred_tag edge."""
    graph = {
        "nodes": [
            {"id": "a.md", "label": "A", "type": "doc", "tags": ["OKR"], "timestamp": ""},
            {"id": "b.md", "label": "B", "type": "doc", "tags": ["KPI"], "timestamp": ""},
        ],
        "edges": [],
    }
    result = infer_edges(graph)
    tag_edges = [e for e in result["edges"] if e["kind"] == "inferred_tag"]
    assert len(tag_edges) == 0


def test_infer_edges_tag_chain_not_clique():
    """4 nodes sharing a tag produce 3 chain edges, not 6 clique edges."""
    graph = {
        "nodes": [
            {"id": "a.md", "label": "A", "type": "doc", "tags": ["OKR"], "timestamp": ""},
            {"id": "b.md", "label": "B", "type": "doc", "tags": ["OKR"], "timestamp": ""},
            {"id": "c.md", "label": "C", "type": "doc", "tags": ["OKR"], "timestamp": ""},
            {"id": "d.md", "label": "D", "type": "doc", "tags": ["OKR"], "timestamp": ""},
        ],
        "edges": [],
    }
    result = infer_edges(graph)
    tag_edges = [e for e in result["edges"] if e["kind"] == "inferred_tag"]
    assert len(tag_edges) == 3  # chain: A-B, B-C, C-D (not 6 = 4-choose-2)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/kitch/Desktop/lark-autocontext && python3 -m pytest tests/unit/test_visualize.py -v`
Expected: FAIL with `ImportError: cannot import name 'infer_edges'`

- [ ] **Step 3: Implement `infer_edges` with tag inference**

Add this function after `scan_bundle_to_graph()` (after line 168, before `compute_cited_by`):

```python
def infer_edges(graph):
    """Add inferred edges based on shared tags and document order.

    Adds edges with kind='inferred_tag' or kind='inferred_order' to
    graph['edges']. Does not duplicate edges where an explicit or
    same-type inferred edge already exists between the same pair.
    """
    nodes = graph["nodes"]
    existing_edges = graph["edges"]

    # Build dedup set: (source, target, kind) and (target, source, kind)
    # for undirected kinds, to avoid duplicate edges in either direction.
    seen = set()
    for e in existing_edges:
        seen.add((e["source"], e["target"], e["kind"]))
        seen.add((e["target"], e["source"], e["kind"]))

    new_edges = []

    # --- 1. Shared tag edges (chain, not clique) ---
    # Group nodes by each tag, then within each group sort by label
    # and connect adjacent pairs.
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
            # Skip if explicit edge exists either direction
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
        # Skip if explicit edge exists either direction
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/kitch/Desktop/lark-autocontext && python3 -m pytest tests/unit/test_visualize.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
cd /Users/kitch/Desktop/lark-autocontext
git add scripts/visualize.py tests/unit/test_visualize.py
git commit -m "feat: add infer_edges with shared-tag edge inference"
```

---

### Task 2: Edge Inference — Document Order + Dedup

**Files:**
- Modify: `scripts/visualize.py` (no change needed if Task 1 included order logic, but add tests)
- Test: `tests/unit/test_visualize.py`

- [ ] **Step 1: Write failing tests for order edges and dedup**

Append to `tests/unit/test_visualize.py`:

```python
def test_infer_edges_order_by_timestamp():
    """Nodes sorted by timestamp get inferred_order edges between adjacent pairs."""
    graph = {
        "nodes": [
            {"id": "a.md", "label": "A", "type": "doc", "tags": [], "timestamp": "2026-01-01"},
            {"id": "b.md", "label": "B", "type": "doc", "tags": [], "timestamp": "2026-02-01"},
            {"id": "c.md", "label": "C", "type": "doc", "tags": [], "timestamp": "2026-03-01"},
        ],
        "edges": [],
    }
    result = infer_edges(graph)
    order_edges = [e for e in result["edges"] if e["kind"] == "inferred_order"]
    assert len(order_edges) == 2
    # Direction: earlier -> later
    assert order_edges[0]["source"] == "a.md"
    assert order_edges[0]["target"] == "b.md"
    assert order_edges[1]["source"] == "b.md"
    assert order_edges[1]["target"] == "c.md"


def test_infer_edges_order_fallback_filename():
    """Nodes without timestamp fall back to filename sort."""
    graph = {
        "nodes": [
            {"id": "c.md", "label": "C", "type": "doc", "tags": [], "timestamp": ""},
            {"id": "a.md", "label": "A", "type": "doc", "tags": [], "timestamp": ""},
            {"id": "b.md", "label": "B", "type": "doc", "tags": [], "timestamp": ""},
        ],
        "edges": [],
    }
    result = infer_edges(graph)
    order_edges = [e for e in result["edges"] if e["kind"] == "inferred_order"]
    assert len(order_edges) == 2
    assert order_edges[0]["source"] == "a.md"
    assert order_edges[0]["target"] == "b.md"


def test_infer_edges_no_duplicate_explicit():
    """Pair with existing explicit edge gets no inferred edge."""
    graph = {
        "nodes": [
            {"id": "a.md", "label": "A", "type": "doc", "tags": ["OKR"], "timestamp": "2026-01-01"},
            {"id": "b.md", "label": "B", "type": "doc", "tags": ["OKR"], "timestamp": "2026-02-01"},
        ],
        "edges": [{"source": "a.md", "target": "b.md", "kind": "explicit"}],
    }
    result = infer_edges(graph)
    inferred = [e for e in result["edges"] if e["kind"] != "explicit"]
    assert len(inferred) == 0


def test_infer_edges_no_duplicate_same_kind():
    """Pair doesn't get duplicate inferred_tag edges."""
    graph = {
        "nodes": [
            {"id": "a.md", "label": "A", "type": "doc", "tags": ["OKR", "目标"], "timestamp": ""},
            {"id": "b.md", "label": "B", "type": "doc", "tags": ["OKR"], "timestamp": ""},
        ],
        "edges": [],
    }
    result = infer_edges(graph)
    tag_edges = [e for e in result["edges"] if e["kind"] == "inferred_tag"]
    assert len(tag_edges) == 1  # not 2, even though they share 2 tags
```

- [ ] **Step 2: Run tests to verify they pass** (implementation already done in Task 1)

Run: `cd /Users/kitch/Desktop/lark-autocontext && python3 -m pytest tests/unit/test_visualize.py -v`
Expected: PASS (7 tests)

- [ ] **Step 3: Commit**

```bash
cd /Users/kitch/Desktop/lark-autocontext
git add tests/unit/test_visualize.py
git commit -m "test: add order, dedup, and chain tests for infer_edges"
```

---

### Task 3: Wire `infer_edges` into Pipeline

**Files:**
- Modify: `scripts/visualize.py:557` (the `main()` function)

- [ ] **Step 1: Update `main()` to call `infer_edges`**

Change line 557 in `main()`:

```python
# Old:
graph = compute_cited_by(scan_bundle_to_graph(args.bundle))

# New:
graph = scan_bundle_to_graph(args.bundle)
graph = infer_edges(graph)
graph = compute_cited_by(graph)
```

- [ ] **Step 2: Run full test suite to check for regressions**

Run: `cd /Users/kitch/Desktop/lark-autocontext && python3 -m pytest tests/ --tb=short`
Expected: PASS (all existing tests + 7 new tests)

- [ ] **Step 3: Commit**

```bash
cd /Users/kitch/Desktop/lark-autocontext
git add scripts/visualize.py
git commit -m "feat: wire infer_edges into visualize pipeline"
```

---

### Task 4: Visual Upgrade — Layout, Edge Styles, Node Styles

**Files:**
- Modify: `scripts/visualize.py` — `HTML_TEMPLATE` string (layout config ~line 317, style array ~line 329, node sizes ~line 265)

- [ ] **Step 1: Update layout parameters**

In the `HTML_TEMPLATE`, find the `layoutCfg` assignment (~line 316-322) and replace with:

```javascript
const layoutCfg = hasBilkent
  ? {name:"cose-bilkent", animate:false, randomize:true,
     idealEdgeLength:280, nodeRepulsion:45000, edgeElasticity:0.45,
     gravity:0.35, numIter:3500, tile:true, padding:60}
  : {name:"cose", animate:false, randomize:true,
     idealEdgeLength:300, nodeRepulsion:()=>52000,
     nodeOverlap:60, padding:60, gravity:45, numIter:3000};
```

- [ ] **Step 2: Update node size mapping**

Find `SIZES_BY_TYPE` (~line 265) and replace with:

```javascript
const SIZES_BY_TYPE = {"Project":55, "Person":50, "Concept":50, "Design Doc":45, "Meeting Minutes":42};
```

- [ ] **Step 3: Update degree scaling formula**

Find the node size computation (~line 298) and replace:

```javascript
// Old:
size: (SIZES_BY_TYPE[n.type] || 38) + Math.min((degree[n.id]||0) * 3, 20),

// New:
size: (SIZES_BY_TYPE[n.type] || 40) + Math.min((degree[n.id]||0) * 4, 24),
```

- [ ] **Step 4: Update node default style**

Find the `node` style selector (~line 330-348) and update these properties:

```javascript
{selector:"node", style:{
  "background-color":"data(color)",
  "label":"data(label)",
  "font-size":14, "font-weight":600,
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
  "border-width":3, "border-color":"#fff",
}},
```

- [ ] **Step 5: Add edge style selectors for inferred edges**

After the existing `edge` selector (~line 353-358), add three new selectors. Replace the existing edge style block and `.faded` selectors with:

```javascript
    // Explicit edges: solid + arrow, the strongest visual weight.
    {selector:"edge[kind='explicit']", style:{
      "width":2, "line-color":"#64748b",
      "target-arrow-color":"#64748b",
      "target-arrow-shape":"triangle",
      "curve-style":"bezier", "arrow-scale":0.9,
    }},
    // Inferred tag edges: dashed, blue, no arrow (undirected).
    {selector:"edge[kind='inferred_tag']", style:{
      "width":1.5, "line-color":"#3b82f6",
      "line-style":"dashed",
      "target-arrow-shape":"none",
      "curve-style":"bezier",
    }},
    // Inferred order edges: dotted, green, with arrow (directed).
    {selector:"edge[kind='inferred_order']", style:{
      "width":1.5, "line-color":"#10b981",
      "line-style":"dotted",
      "target-arrow-color":"#10b981",
      "target-arrow-shape":"triangle",
      "curve-style":"bezier", "arrow-scale":0.7,
    }},
    // Default edge fallback (for any unknown kind).
    {selector:"edge", style:{
      "width":2, "line-color":"#64748b",
      "target-arrow-color":"#64748b",
      "target-arrow-shape":"triangle",
      "curve-style":"bezier", "arrow-scale":0.9,
    }},
    {selector:".faded", style:{"opacity":0.12}},
    {selector:"edge.faded", style:{"opacity":0.05}},
```

Note: Cytoscape applies the *last matching* selector, so the generic `edge` selector must come after the kind-specific ones to act as a fallback. Actually, Cytoscape uses specificity — `edge[kind='explicit']` is more specific than `edge`. So the order above is fine: specific selectors first, generic fallback last.

- [ ] **Step 6: Run full test suite**

Run: `cd /Users/kitch/Desktop/lark-autocontext && python3 -m pytest tests/ --tb=short`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
cd /Users/kitch/Desktop/lark-autocontext
git add scripts/visualize.py
git commit -m "feat: upgrade layout params, edge styles, node styles"
```

---

### Task 5: Interaction Upgrade — Hover Highlight

**Files:**
- Modify: `scripts/visualize.py` — `HTML_TEMPLATE` JS section (after the `cy.on("tap","node",...)` handler ~line 427)

- [ ] **Step 1: Add hover highlight JS**

After the existing `cy.on("tap","node",...)` block (~line 430), add:

```javascript
// --- Hover highlight: neighbors stay visible, rest dims ---
cy.on("mouseover","node",function(evt){
  var node = evt.target;
  var neighborhood = node.closedNeighborhood();
  cy.elements().difference(neighborhood).style("opacity",0.2);
  node.style("border-color","#f59e0b").style("border-width","4px");
  neighborhood.edges().style("width",3);
});
cy.on("mouseout","node",function(evt){
  cy.elements().style("opacity",1);
  // Reset border and width to defaults
  cy.nodes().style("border-color","#fff").style("border-width","3px");
  cy.edges().style("width",2);
  // Restore kind-specific widths
  cy.edges("[kind='inferred_tag']").style("width",1.5);
  cy.edges("[kind='inferred_order']").style("width",1.5);
});
```

- [ ] **Step 2: Run full test suite**

Run: `cd /Users/kitch/Desktop/lark-autocontext && python3 -m pytest tests/ --tb=short`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
cd /Users/kitch/Desktop/lark-autocontext
git add scripts/visualize.py
git commit -m "feat: add hover highlight interaction"
```

---

### Task 6: Interaction Upgrade — Zoom/Pan + Legend

**Files:**
- Modify: `scripts/visualize.py` — `HTML_TEMPLATE` (cytoscape init options ~line 328, CSS section ~line 215, HTML body ~line 226)

- [ ] **Step 1: Update Cytoscape init options for zoom/pan**

Find the `cytoscape({...})` call (~line 324) and update the options. Replace `wheelSensitivity: 0.2` with:

```javascript
const cy = cytoscape({
  container: document.getElementById("cy"),
  elements,
  layout: layoutCfg,
  wheelSensitivity: 0.3,
  minZoom: 0.3,
  maxZoom: 3.0,
  zoomingEnabled: true,
  panningEnabled: true,
  style:[
```

- [ ] **Step 2: Add double-click-to-reset handler**

After the hover handlers (added in Task 5), add:

```javascript
// Double-click on empty canvas resets view
cy.on("tap",function(evt){
  if(evt.target === cy){
    cy.animate({fit:{eles:cy.elements(),padding:50}},{duration:300});
  }
});
```

- [ ] **Step 3: Add legend CSS**

In the `<style>` section (before `</style>` ~line 216), add:

```css
  #legend{position:fixed;bottom:16px;left:16px;background:rgba(255,255,255,0.95);
          border:1px solid #e5e5e5;border-radius:8px;padding:10px 14px;
          font-size:12px;color:#444;z-index:100;box-shadow:0 2px 8px rgba(0,0,0,0.08)}
  #legend h4{margin:0 0 6px;font-size:11px;color:#999;text-transform:uppercase;letter-spacing:0.5px}
  #legend .item{display:flex;align-items:center;gap:8px;margin:3px 0}
  #legend .line{width:28px;height:0;border-top-width:2px;border-top-style:solid}
  #legend .line.explicit{border-top-color:#64748b;border-top-style:solid}
  #legend .line.tag{border-top-color:#3b82f6;border-top-style:dashed}
  #legend .line.order{border-top-color:#10b981;border-top-style:dotted}
```

- [ ] **Step 4: Add legend HTML**

In the HTML body, after `<div id="cy"></div>` (~line 226), add:

```html
  <div id="cy"></div>
  <div id="legend">
    <h4>Edge Types</h4>
    <div class="item"><div class="line explicit"></div>Explicit link</div>
    <div class="item"><div class="line tag"></div>Shared tag</div>
    <div class="item"><div class="line order"></div>Document order</div>
  </div>
```

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/kitch/Desktop/lark-autocontext && python3 -m pytest tests/ --tb=short`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd /Users/kitch/Desktop/lark-autocontext
git add scripts/visualize.py
git commit -m "feat: add zoom/pan tuning, legend overlay, double-click reset"
```

---

### Task 7: Integration Test + Manual Verification

**Files:**
- Test: `tests/unit/test_visualize.py` (append integration test)

- [ ] **Step 1: Write integration test**

Append to `tests/unit/test_visualize.py`:

```python
from visualize import scan_bundle_to_graph, infer_edges, compute_cited_by, render_html
import tempfile
import os


def test_integration_viz_html_contains_inferred_edges():
    """Full pipeline produces HTML with inferred edge data and interaction code."""
    # Create a minimal bundle with 3 docs sharing tags
    with tempfile.TemporaryDirectory() as bundle_dir:
        for name, title, tags, ts in [
            ("a.md", "Alpha", '["OKR", "目标"]', "2026-01-01"),
            ("b.md", "Beta", '["OKR"]', "2026-02-01"),
            ("c.md", "Gamma", '["OKR"]', "2026-03-01"),
        ]:
            with open(os.path.join(bundle_dir, name), "w") as f:
                f.write(f"""---
title: "{title}"
type: doc
tags: {tags}
timestamp: "{ts}"
---

# {title}

Some content.
""")
        graph = scan_bundle_to_graph(bundle_dir)
        graph = infer_edges(graph)
        graph = compute_cited_by(graph)

        # Verify inferred edges exist
        tag_edges = [e for e in graph["edges"] if e["kind"] == "inferred_tag"]
        order_edges = [e for e in graph["edges"] if e["kind"] == "inferred_order"]
        assert len(tag_edges) >= 1, f"Expected tag edges, got {tag_edges}"
        assert len(order_edges) >= 1, f"Expected order edges, got {order_edges}"

        # Verify HTML output
        html = render_html(graph)
        assert "inferred_tag" in html, "HTML missing inferred_tag"
        assert "inferred_order" in html, "HTML missing inferred_order"
        assert "mouseover" in html, "HTML missing hover handler"
        assert "legend" in html, "HTML missing legend"
        assert "45000" in html, "HTML missing new nodeRepulsion param"
        assert "280" in html, "HTML missing new idealEdgeLength param"
```

- [ ] **Step 2: Run all tests**

Run: `cd /Users/kitch/Desktop/lark-autocontext && python3 -m pytest tests/ --tb=short`
Expected: PASS (all tests including new integration test)

- [ ] **Step 3: Generate real viz.html for manual check**

Run: `cd /Users/kitch/Desktop/lark-autocontext/scripts && python3 visualize.py --bundle ../bundle --out ../bundle/viz.html`
Expected: Output showing N nodes and >0 edges

- [ ] **Step 4: Commit**

```bash
cd /Users/kitch/Desktop/lark-autocontext
git add tests/unit/test_visualize.py
git commit -m "test: add integration test for full visualize pipeline"
```

---

### Task 8: Push to Main

- [ ] **Step 1: Push all commits**

```bash
cd /Users/kitch/Desktop/lark-autocontext
unset GH_TOKEN
gh auth setup-git
git push origin main
```

- [ ] **Step 2: Verify all tests pass one final time**

Run: `cd /Users/kitch/Desktop/lark-autocontext && python3 -m pytest tests/ --tb=short`
Expected: All PASS
