"""Tests for visualize.py graph scanning."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))


def test_extract_links():
    from visualize import extract_links
    md = "See [doc1](path/to/doc1.md) and [doc2](../other/doc2.md#section)."
    links = extract_links(md)
    assert "path/to/doc1.md" in links
    assert "../other/doc2.md" in links


def test_scan_bundle_to_graph(tmp_path):
    from visualize import scan_bundle_to_graph
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "projects").mkdir()
    (bundle / "people").mkdir()

    (bundle / "projects" / "meeting.md").write_text(
        '---\ntype: Meeting Minutes\ntitle: Test Meeting\n---\n\n'
        '# Summary\n\nRefers to [Alice](/people/Alice.md)\n',
        encoding="utf-8"
    )
    (bundle / "people" / "Alice.md").write_text(
        '---\ntype: Person\ntitle: Alice\nmentions:\n  - /projects/meeting.md\n---\n\n# Profile\n\nTest person.\n',
        encoding="utf-8"
    )

    graph = scan_bundle_to_graph(str(bundle))
    assert len(graph["nodes"]) == 2
    assert len(graph["edges"]) >= 1
    # Check edge from meeting -> Alice (body link) and Alice -> meeting (mentions)
    edge_pairs = {(e["source"], e["target"]) for e in graph["edges"]}
    assert ("projects/meeting.md", "people/Alice.md") in edge_pairs
    assert ("people/Alice.md", "projects/meeting.md") in edge_pairs


def test_compute_cited_by(tmp_path):
    from visualize import scan_bundle_to_graph, compute_cited_by
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "a.md").write_text(
        '---\ntitle: A\n---\n\nLink to [B](b.md)\n', encoding="utf-8"
    )
    (bundle / "b.md").write_text(
        '---\ntitle: B\n---\n\nContent.\n', encoding="utf-8"
    )
    graph = scan_bundle_to_graph(str(bundle))
    graph = compute_cited_by(graph)
    by_id = {n["id"]: n for n in graph["nodes"]}
    assert "a.md" in by_id["b.md"]["cited_by"]


def test_mixed_types_graph(tmp_path):
    from visualize import scan_bundle_to_graph
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "projects").mkdir()
    (bundle / "people").mkdir()
    (bundle / "concepts").mkdir()

    (bundle / "projects" / "dec.md").write_text(
        '---\ntype: Decision\ntitle: D1\n---\n\nRefers to [OKF](/concepts/OKF.md) and [Bob](/people/Bob.md)\n',
        encoding="utf-8"
    )
    (bundle / "people" / "Bob.md").write_text(
        '---\ntype: Person\ntitle: Bob\n---\n\n# Profile\n', encoding="utf-8"
    )
    (bundle / "concepts" / "OKF.md").write_text(
        '---\ntype: Concept\ntitle: OKF\n---\n\n# Definition\n', encoding="utf-8"
    )

    graph = scan_bundle_to_graph(str(bundle))
    assert len(graph["nodes"]) == 3
    types = {n["type"] for n in graph["nodes"]}
    assert "Decision" in types
    assert "Person" in types
    assert "Concept" in types
    assert len(graph["edges"]) == 2
