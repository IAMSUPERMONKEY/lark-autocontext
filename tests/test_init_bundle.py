"""Tests for init_bundle okf_version frontmatter and index aggregation."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))


def test_init_bundle_root_index_has_okf_version(tmp_path):
    from init_bundle import init_bundle
    bundle = tmp_path / "newbundle"
    init_bundle(str(bundle))
    root_index = (bundle / "index.md").read_text(encoding="utf-8")
    assert 'okf_version: "0.1"' in root_index
    assert root_index.startswith("---")


def test_index_aggregation_includes_descriptions(tmp_path):
    from okf_writer import generate_directory_index
    proj_dir = tmp_path / "projects" / "demo" / "meetings"
    proj_dir.mkdir(parents=True)
    (proj_dir / "2026-06-01-test.md").write_text(
        '---\ntitle: 2026-06-01 Test\ndescription: 测试会议讨论OKF\n---\n\nbody',
        encoding="utf-8"
    )
    index_text = generate_directory_index(str(proj_dir), heading="meetings")
    assert "[2026-06-01 Test]" in index_text
    assert "测试会议讨论OKF" in index_text
