# Visualize Graph Upgrade Design

**Date**: 2026-07-22
**Status**: Approved
**Author**: Brainstorming session

## Problem

The current `viz.html` graph visualization has two critical issues:

1. **Zero edges**: The OKF documents in the bundle have no explicit `mentions` fields or markdown cross-links, so `scan_bundle_to_graph()` produces 0 edges. The graph degenerates into a vertical list of disconnected nodes.
2. **Weak interaction**: No hover highlight, no zoom/pan tuning, layout parameters cause nodes to cluster together. The visual experience is far below WeKnora's wiki graph (which has custom force-directed SVG with drag-pin, neighbor highlight, Bloom expansion, etc.).

## Goal

Upgrade `visualize.py` to produce a graph that has meaningful connections between nodes and rich interaction, while keeping the single-file HTML output architecture and Cytoscape.js library.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Edge inference | Shared tags + document order | Simple, deterministic, provides immediate visual connections |
| JS library | Keep Cytoscape.js | Already integrated offline, supports all needed interactions natively |
| Implementation | In-place enhancement of `visualize.py` | Single-file scope, no new modules |
| Interaction priority | Hover highlight + zoom/pan | Most impactful for usability with minimal code |

## Design

### 1. Edge Inference (`infer_edges` function)

**Location**: New function inserted between `scan_bundle_to_graph()` and `compute_cited_by()` in the pipeline.

**Signature**:
```python
def infer_edges(graph: dict) -> dict:
    """Add inferred edges based on shared tags and document order.

    Adds edges with kind='inferred_tag' or kind='inferred_order' to
    graph['edges']. Does not duplicate edges where an explicit or
    same-type inferred edge already exists between the same pair.
    """
```

#### 1.1 Shared Tag Edges (`inferred_tag`)

- Iterate all node pairs. If two nodes share at least one tag, they are candidates.
- To prevent edge explosion on large bundles with common tags: within each tag group, sort nodes by label (alphabetical) and connect only adjacent pairs (chain, not clique).
- Edge kind: `"inferred_tag"`, no direction.

#### 1.2 Document Order Edges (`inferred_order`)

- Sort all nodes by frontmatter `timestamp` field (fallback: filename).
- Connect adjacent nodes in sorted order with a directed edge.
- Edge kind: `"inferred_order"`, directed (source = earlier, target = later).

#### 1.3 Deduplication

- Before adding an inferred edge, check if an edge (in either direction) with the same `kind` already exists between the pair. Skip if so.
- Before adding an inferred edge, check if an `explicit` edge exists between the pair. Skip if so (explicit takes precedence).
- Build a set of existing `(source, target, kind)` tuples for O(1) lookup.

### 2. Visual Upgrade

#### 2.1 Layout Parameters

Adjust `cose-bilkent` (primary) and `cose` (fallback) parameters:

| Parameter | Old | New | Reason |
|-----------|-----|-----|--------|
| `nodeRepulsion` | 24000 | 45000 | Spread nodes apart |
| `idealEdgeLength` | 180 | 280 | More space between connected nodes |
| `gravity` | 0.25 | 0.35 | Keep graph centered |
| `numIter` | 3000 | 3500 | Better convergence |

#### 2.2 Edge Styles

Three visual styles for the three edge kinds:

| Kind | Line Style | Color | Width | Arrow |
|------|-----------|-------|-------|-------|
| `explicit` | Solid | `#64748b` (slate) | 2px | Yes |
| `inferred_tag` | Dashed | `#3b82f6` (blue) | 1.5px | No |
| `inferred_order` | Dotted | `#10b981` (green) | 1.5px | Yes |

Cytoscape style selector: `edge[kind="inferred_tag"]` etc.

#### 2.3 Node Styles

- Increase base sizes: `Concept=50`, `Design Doc=45`, others=40 (was 45/38).
- Degree-based scaling: `base + min(degree * 4, 24)` (was `* 3, 20`).
- Border width: 3px (was default), border color = fill color darkened 20%.
- Label: 14px bold for title (was default), 11px `#94a3b8` for subtitle.

#### 2.4 Legend

Fixed HTML overlay in bottom-left corner:

```
[---] Explicit link      (slate solid)
[- -] Shared tag          (blue dashed)
[...] Document order      (green dotted)
```

Pure HTML/CSS, not part of the Cytoscape canvas. Uses `position: fixed; bottom: 16px; left: 16px;`.

### 3. Interaction Upgrade

#### 3.1 Hover Highlight

```javascript
cy.on('mouseover', 'node', function(evt) {
    var node = evt.target;
    var neighborhood = node.closedNeighborhood();
    cy.elements().difference(neighborhood).style('opacity', 0.2);
    node.style('border-color', '#f59e0b').style('border-width', '4px');
    neighborhood.edges().style('width', 3);
});

cy.on('mouseout', 'node', function(evt) {
    cy.elements().style('opacity', 1);
    // Reset to default styles via removeStyle
});
```

- Hovered node gets gold pulse ring (`#f59e0b`, 4px border).
- Direct neighbors and connecting edges stay at full opacity, edges thicken to 3px.
- All other elements drop to 0.2 opacity.
- On mouseout, all styles reset to defaults.

#### 3.2 Zoom and Pan

Configure Cytoscape init options:

```javascript
{
    wheelSensitivity: 0.3,
    minZoom: 0.3,
    maxZoom: 3.0,
    zoomingEnabled: true,
    panningEnabled: true,
}
```

- Double-click on empty canvas: `cy.animate({fit: {eles: cy.elements(), padding: 50}}, {duration: 300})`.

#### 3.3 Preserved Interactions

- Search filter (existing): keep as-is.
- Type filter dropdown (existing): keep as-is.
- Click node -> detail panel (existing): keep as-is.

### 4. Pipeline Flow

```
scan_bundle_to_graph()
    |
    v
infer_edges()          <-- NEW
    |
    v
compute_cited_by()
    |
    v
render_html()
```

`infer_edges` mutates `graph['edges']` in place, appending inferred edges after existing explicit edges.

### 5. Testing

#### 5.1 Unit Tests (`tests/unit/test_visualize.py`)

| Test | Description |
|------|-------------|
| `test_infer_edges_shared_tags` | Two nodes sharing a tag get an `inferred_tag` edge |
| `test_infer_edges_no_shared_tags` | Nodes with no common tags get no `inferred_tag` edge |
| `test_infer_edges_order_by_timestamp` | Nodes sorted by timestamp get `inferred_order` edges between adjacent pairs |
| `test_infer_edges_order_fallback_filename` | Nodes without timestamp fall back to filename sort |
| `test_infer_edges_no_duplicate_explicit` | Pair with existing explicit edge gets no inferred edge |
| `test_infer_edges_no_duplicate_same_kind` | Pair doesn't get duplicate `inferred_tag` edges |
| `test_infer_edges_tag_chain_not_clique` | 4 nodes sharing a tag produce 3 edges (chain), not 6 (clique) |

#### 5.2 Integration Test

Run `visualize.py` with the 5 OKR documents as fixture bundle. Assert `viz.html` output contains:
- `inferred_tag` in the JSON data
- `inferred_order` in the JSON data
- `mouseover` event binding
- Legend HTML element
- New layout parameters (`45000`, `280`)

#### 5.3 Manual Acceptance

- Open `viz.html` in browser
- Verify nodes are connected (not 0 edges)
- Hover a node: neighbors highlight, others dim
- Scroll to zoom, drag to pan
- Three edge styles are visually distinct
- Legend visible in bottom-left

## Scope

**In scope**:
- `scripts/visualize.py`: add `infer_edges()`, adjust layout params, edge/node styles, add hover/zoom interaction, add legend HTML
- `tests/unit/test_visualize.py`: new test file for edge inference tests

**Out of scope**:
- LLM-based relationship extraction (future enhancement)
- Drag-pin interaction (not requested)
- Bloom neighbor expansion (requires server-side data, not applicable to single-file HTML)
- Module splitting (deferred until edge inference logic grows complex)
