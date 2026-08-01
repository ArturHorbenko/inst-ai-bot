from types import SimpleNamespace
from unittest.mock import Mock, patch

from video_processor.runner import run_prompt


class FakeArtifactStore:
    def get_by_hash(self, content_hash):
        return {
            "content_hash": content_hash,
            "video_file_ref": "videos/example.mp4",
            "gemini_file_ref": None,
        }

    def update_gemini_ref(self, *_args):
        pass


class FakeRunsStore:
    def insert(self, run):
        return run


def test_run_persists_optional_metadata():
    config = SimpleNamespace(GEMINI_API_KEY="key")
    with patch("video_processor.runner.gemini_module.call_gemini", Mock(return_value=("{}", None))):
        run = run_prompt(
            "sha256:example",
            "extract traits",
            "google/gemini-2.5-pro",
            "reel-traits/v1",
            config,
            FakeArtifactStore(),
            FakeRunsStore(),
            metadata={"trait_schema": "reel-traits/v1"},
        )

    assert run["metadata"] == {"trait_schema": "reel-traits/v1"}
