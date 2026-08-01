"""Unit tests for the prompt runner's provider routing.

Network-free: the provider modules (`gemini`, `twelvelabs`) are patched out so
no model API is contacted. Run with:

    venv/bin/python -m unittest discover -s tests -p 'test_*.py'
"""
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from video_processor import runner
from video_processor import twelvelabs as twelvelabs_module
from video_processor.runner import ArtifactNotFound, _parse_model, run_prompt


def _config(**overrides):
    base = dict(
        GEMINI_API_KEY="gem-key",
        TWELVE_LABS_API_KEY="tl-key",
        TWELVE_LABS_INDEX_NAME="default-index",
        TWELVE_LABS_INDEX_ID=None,
        INDEXING_POLL_INTERVAL=10,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _artifact(**overrides):
    base = dict(
        content_hash="sha256:abc",
        video_file_ref="videos/sha256:abc.mp4",
        gemini_file_ref=None,
        twelvelabs_video_id=None,
    )
    base.update(overrides)
    return base


class FakeArtifactStore:
    def __init__(self, artifact=None):
        self._artifact = artifact
        self.gemini_updates = []
        self.twelvelabs_updates = []

    def get_by_hash(self, content_hash):
        if self._artifact and self._artifact["content_hash"] == content_hash:
            return dict(self._artifact)
        return None

    def update_gemini_ref(self, content_hash, gemini_file_ref):
        self.gemini_updates.append((content_hash, gemini_file_ref))

    def update_twelvelabs_ref(self, content_hash, twelvelabs_video_id):
        self.twelvelabs_updates.append((content_hash, twelvelabs_video_id))


class FakeRunsStore:
    def __init__(self):
        self.inserted = []

    def insert(self, run):
        self.inserted.append(run)
        return run


class ParseModelTest(unittest.TestCase):
    def test_provider_split(self):
        self.assertEqual(
            _parse_model("google/gemini-2.5-pro"), ("google", "gemini-2.5-pro")
        )
        self.assertEqual(
            _parse_model("twelvelabs/pegasus1.5"), ("twelvelabs", "pegasus1.5")
        )

    def test_bare_model_defaults_to_google(self):
        self.assertEqual(_parse_model("gemini-2.5-pro"), ("google", "gemini-2.5-pro"))

    def test_provider_is_lowercased(self):
        self.assertEqual(
            _parse_model("TwelveLabs/pegasus1.5"), ("twelvelabs", "pegasus1.5")
        )


class RunPromptRoutingTest(unittest.TestCase):
    def test_missing_artifact_raises(self):
        with self.assertRaises(ArtifactNotFound):
            run_prompt(
                "sha256:missing", "p", "google/gemini-2.5-pro", None,
                _config(), FakeArtifactStore(), FakeRunsStore(),
            )

    def test_google_route_caches_file_ref(self):
        store = FakeArtifactStore(_artifact())
        runs = FakeRunsStore()
        fake_gemini = SimpleNamespace(
            call_gemini=Mock(return_value=("gemini output", "files/xyz"))
        )
        with patch.dict(sys.modules, {"video_processor.gemini": fake_gemini}):
            run = run_prompt(
                "sha256:abc", "what is the hook?", "google/gemini-2.5-pro",
                "hook", _config(), store, runs,
            )
        fake_gemini.call_gemini.assert_called_once()
        self.assertEqual(run["output"], "gemini output")
        self.assertEqual(run["model"], "google/gemini-2.5-pro")
        self.assertEqual(store.gemini_updates, [("sha256:abc", "files/xyz")])
        self.assertEqual(store.twelvelabs_updates, [])
        self.assertEqual(run["metadata"], {})

    def test_run_persists_client_metadata(self):
        store = FakeArtifactStore(_artifact())
        runs = FakeRunsStore()
        fake_gemini = SimpleNamespace(call_gemini=Mock(return_value=("{}", "files/xyz")))
        with patch.dict(sys.modules, {"video_processor.gemini": fake_gemini}):
            run = run_prompt(
                "sha256:abc", "extract", "google/gemini-2.5-pro", "reel-traits/v1",
                _config(), store, runs, metadata={"trait_schema": "reel-traits/v1"},
            )
        self.assertEqual(run["metadata"], {"trait_schema": "reel-traits/v1"})

    def test_twelvelabs_route_caches_video_id(self):
        store = FakeArtifactStore(_artifact())
        runs = FakeRunsStore()
        with patch.object(
            twelvelabs_module, "call_twelvelabs",
            return_value=("twelvelabs output", "vid-123"),
        ) as call:
            run = run_prompt(
                "sha256:abc", "what is the hook?", "twelvelabs/pegasus1.5",
                "hook", _config(), store, runs,
            )
        call.assert_called_once()
        kwargs = call.call_args.kwargs
        self.assertEqual(kwargs["model"], "pegasus1.5")
        self.assertEqual(kwargs["api_key"], "tl-key")
        self.assertEqual(kwargs["index_name"], "default-index")
        self.assertEqual(run["output"], "twelvelabs output")
        self.assertEqual(run["model"], "twelvelabs/pegasus1.5")
        self.assertEqual(store.twelvelabs_updates, [("sha256:abc", "vid-123")])
        self.assertEqual(store.gemini_updates, [])

    def test_twelvelabs_cached_video_id_not_rewritten(self):
        store = FakeArtifactStore(_artifact(twelvelabs_video_id="vid-123"))
        runs = FakeRunsStore()
        with patch.object(
            twelvelabs_module, "call_twelvelabs",
            return_value=("out", "vid-123"),
        ):
            run_prompt(
                "sha256:abc", "p", "twelvelabs/pegasus1.5", None,
                _config(), store, runs,
            )
        self.assertEqual(store.twelvelabs_updates, [])

    def test_unknown_provider_raises_not_implemented(self):
        store = FakeArtifactStore(_artifact())
        with self.assertRaises(NotImplementedError):
            run_prompt(
                "sha256:abc", "p", "openai/gpt-5", None,
                _config(), store, FakeRunsStore(),
            )


if __name__ == "__main__":
    unittest.main()
