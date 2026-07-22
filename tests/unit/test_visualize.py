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
