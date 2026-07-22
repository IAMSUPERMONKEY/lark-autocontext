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
    assert len(tag_edges) == 3


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
    assert len(tag_edges) == 1


from visualize import scan_bundle_to_graph, infer_edges, compute_cited_by, render_html
import tempfile
import os


def test_integration_viz_html_contains_inferred_edges():
    """Full pipeline produces HTML with inferred edge data and interaction code."""
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

        tag_edges = [e for e in graph["edges"] if e["kind"] == "inferred_tag"]
        order_edges = [e for e in graph["edges"] if e["kind"] == "inferred_order"]
        assert len(tag_edges) >= 1, f"Expected tag edges, got {tag_edges}"
        assert len(order_edges) >= 1, f"Expected order edges, got {order_edges}"

        html = render_html(graph)
        assert "inferred_tag" in html, "HTML missing inferred_tag"
        assert "inferred_order" in html, "HTML missing inferred_order"
        assert "mouseover" in html, "HTML missing hover handler"
        assert "legend" in html, "HTML missing legend"
        assert "45000" in html, "HTML missing new nodeRepulsion param"
        assert "280" in html, "HTML missing new idealEdgeLength param"
