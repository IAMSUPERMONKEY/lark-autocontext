"""Shared pytest fixtures."""
import pytest


@pytest.fixture
def tmp_bundle(tmp_path):
    """Create a temporary empty OKF bundle directory structure."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "projects").mkdir()
    (bundle / "people").mkdir()
    (bundle / "concepts").mkdir()
    (bundle / "index.md").write_text(
        '---\nokf_version: "0.1"\ntitle: Test Bundle\n---\n\n# Test Bundle\n',
        encoding='utf-8'
    )
    (bundle / "log.md").write_text(
        '# Change Log\n\n## 2026-06-21\n\n* **Initialization**: Bundle created\n',
        encoding='utf-8'
    )
    return bundle


@pytest.fixture
def sample_classified_json():
    """Standard classified_json output for testing okf_writer."""
    return {
        "project": "demo",
        "type": "Meeting Minutes",
        "category": "meetings",
        "title": "2026-06-01 测试会议",
        "description": "测试会议讨论 OKF 重构方案",
        "summary": "确定采用 Pipeline 架构。",
        "key_points": ["要点1", "要点2"],
        "decisions": [{"decision": "采用 OKF", "owner": "Alice", "deadline": "2026-07-01"}],
        "action_items": [{"task": "写实现", "owner": "张三", "due": "2026-06-30"}],
        "tags": ["测试", "OKF"],
        "people": ["Alice", "张三"],
        "concepts": ["OKF", "Pipeline 架构"],
        "filename": "2026-06-01-测试会议.md",
        "resource": "https://feishu.cn/docx/TESTTOKEN",
        "edited_time": "2026-06-01T14:30:00+08:00"
    }
