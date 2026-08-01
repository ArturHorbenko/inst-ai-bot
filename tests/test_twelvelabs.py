"""Unit tests for the TwelveLabs prompt provider.

Network-free: a fake TwelveLabs client stands in for the SDK, so no upload,
indexing task, or analyze call leaves the process. Run with:

    venv/bin/python -m unittest discover -s tests -p 'test_*.py'
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from video_processor import twelvelabs as tl


class FakeTasks:
    def __init__(self, video_id="vid-1", status="ready"):
        self._video_id = video_id
        self._status = status
        self.created = []

    def create(self, index_id, video_file):
        self.created.append((index_id, video_file))
        return SimpleNamespace(id="task-1", video_id=None)

    def wait_for_done(self, task_id, sleep_interval):
        return SimpleNamespace(
            id=task_id, status=self._status, video_id=self._video_id
        )


class FakeIndexes:
    def __init__(self, existing=None):
        self._existing = existing or []
        self.created = []

    def list(self, index_name=None):
        return list(self._existing)

    def create(self, index_name, models):
        self.created.append((index_name, models))
        return SimpleNamespace(id="new-index")


class FakeClient:
    def __init__(self, tasks=None, indexes=None, analyze_text="analysis"):
        self.tasks = tasks or FakeTasks()
        self.indexes = indexes or FakeIndexes()
        self._analyze_text = analyze_text
        self.analyze_calls = []

    def analyze(self, **kwargs):
        self.analyze_calls.append(kwargs)
        return SimpleNamespace(data=self._analyze_text)


class ResolveIndexIdTest(unittest.TestCase):
    def test_returns_existing_index_by_name(self):
        client = FakeClient(indexes=FakeIndexes([
            SimpleNamespace(id="idx-1", index_name="default-index"),
        ]))
        self.assertEqual(tl._resolve_index_id(client, "default-index"), "idx-1")
        self.assertEqual(client.indexes.created, [])

    def test_creates_pegasus_index_when_absent(self):
        client = FakeClient(indexes=FakeIndexes([]))
        self.assertEqual(tl._resolve_index_id(client, "default-index"), "new-index")
        self.assertEqual(len(client.indexes.created), 1)
        name, models = client.indexes.created[0]
        self.assertEqual(name, "default-index")
        self.assertEqual(models[0]["model_name"], "pegasus1.5")


class GetOrIndexTest(unittest.TestCase):
    def test_reuses_cached_video_id(self):
        client = FakeClient()
        vid = tl._get_or_index(client, "x.mp4", "cached-vid", "default-index", None, 1)
        self.assertEqual(vid, "cached-vid")
        self.assertEqual(client.tasks.created, [])

    def test_uploads_and_indexes_when_no_cache(self):
        client = FakeClient(
            indexes=FakeIndexes([]), tasks=FakeTasks(video_id="fresh-vid")
        )
        with patch.object(tl.os.path, "getsize", return_value=2048):
            vid = tl._get_or_index(client, "x.mp4", None, "default-index", None, 1)
        self.assertEqual(vid, "fresh-vid")
        self.assertEqual(client.tasks.created, [("new-index", "x.mp4")])

    def test_explicit_index_id_skips_resolution(self):
        client = FakeClient(
            indexes=FakeIndexes([]), tasks=FakeTasks(video_id="fresh-vid")
        )
        with patch.object(tl.os.path, "getsize", return_value=2048):
            tl._get_or_index(client, "x.mp4", None, "default-index", "explicit-idx", 1)
        self.assertEqual(client.tasks.created, [("explicit-idx", "x.mp4")])
        self.assertEqual(client.indexes.created, [])

    def test_failed_indexing_task_raises(self):
        client = FakeClient(tasks=FakeTasks(status="failed"))
        with patch.object(tl.os.path, "getsize", return_value=2048):
            with self.assertRaises(RuntimeError):
                tl._get_or_index(client, "x.mp4", None, "default-index", "idx", 1)


class CallTwelveLabsTest(unittest.TestCase):
    def test_missing_api_key_raises(self):
        with self.assertRaises(RuntimeError):
            tl.call_twelvelabs(api_key="", video_path="x.mp4", prompt="p")

    def test_cached_video_id_runs_analyze_directly(self):
        client = FakeClient(analyze_text="the hook is strong")
        with patch.object(tl, "TwelveLabs", return_value=client):
            output, video_id = tl.call_twelvelabs(
                api_key="key", video_path="x.mp4", prompt="grade the hook",
                model="pegasus1.5", twelvelabs_video_id="vid-9",
            )
        self.assertEqual(output, "the hook is strong")
        self.assertEqual(video_id, "vid-9")
        self.assertEqual(
            client.analyze_calls,
            [{
                "video": {"type": "asset_id", "asset_id": "vid-9"},
                "prompt": "grade the hook",
                "model_name": "pegasus1.5",
            }],
        )
        self.assertEqual(client.tasks.created, [])

    def test_empty_model_falls_back_to_default(self):
        client = FakeClient()
        with patch.object(tl, "TwelveLabs", return_value=client):
            tl.call_twelvelabs(
                api_key="key", video_path="x.mp4", prompt="p",
                model="", twelvelabs_video_id="vid-9",
            )
        self.assertEqual(client.analyze_calls[0]["model_name"], tl.DEFAULT_MODEL)

    def test_pegasus_1_2_uses_legacy_video_id(self):
        client = FakeClient()
        with patch.object(tl, "TwelveLabs", return_value=client):
            tl.call_twelvelabs(
                api_key="key", video_path="x.mp4", prompt="p",
                model="pegasus1.2", twelvelabs_video_id="vid-9",
            )
        self.assertEqual(
            client.analyze_calls[0],
            {"video_id": "vid-9", "prompt": "p", "model_name": "pegasus1.2"},
        )


if __name__ == "__main__":
    unittest.main()
