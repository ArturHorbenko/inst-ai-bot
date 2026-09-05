from pathlib import Path

import pytest

from video_processor import workflow_guides


@pytest.mark.parametrize("name", ["unknown", "../config", "/etc/passwd", "adapt-reel.md", ""])
def test_unknown_workflow_cannot_read_arbitrary_files(name, monkeypatch):
    def unexpected_read(*args, **kwargs):
        pytest.fail("Invalid workflow names must be rejected before reading a file")

    monkeypatch.setattr(Path, "read_text", unexpected_read)
    with pytest.raises(ValueError, match="Available workflows: adapt-reel, performance-audit"):
        workflow_guides.load_workflow(name)


@pytest.mark.parametrize("contents", [None, b"", b" \n\t", b"\xff"])
def test_missing_or_invalid_guide_reports_a_deployment_error(contents, tmp_path, monkeypatch):
    monkeypatch.setattr(workflow_guides, "WORKFLOW_DIRECTORY", tmp_path)
    if contents is not None:
        (tmp_path / "adapt-reel.md").write_bytes(contents)

    with pytest.raises(RuntimeError, match="Check the deployed workflow files") as error:
        workflow_guides.load_workflow("adapt-reel")

    assert str(tmp_path) not in str(error.value)


def test_guides_load_independently_of_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    for name in workflow_guides.WORKFLOW_FILES:
        guide = workflow_guides.load_workflow(name)
        assert guide.name == name
        assert guide.instructions.strip()
