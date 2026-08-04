import hashlib
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch


if "google.genai" not in sys.modules:
    google = ModuleType("google")
    genai = ModuleType("google.genai")
    genai.Client = object
    google.genai = genai
    sys.modules["google"] = google
    sys.modules["google.genai"] = genai

from video_processor.runner import run_text_prompt


class FakeRunsStore:
    def __init__(self):
        self.inserted_run = None

    def insert(self, run):
        self.inserted_run = run
        return run


def test_text_run_forwards_structured_json_prompt_without_an_artifact():
    prompt = '{"task":"comment_sentiment","comment":"Great post"}'
    call_gemini_text = Mock(return_value='{"sentiment":"positive"}')
    runs_store = FakeRunsStore()

    with patch("video_processor.runner.gemini_module.call_gemini_text", call_gemini_text):
        run = run_text_prompt(
            prompt=prompt,
            model="google/gemini-3.5-flash",
            label="comment-sentiment/v1",
            metadata={"source": "dashboard"},
            config=SimpleNamespace(GEMINI_API_KEY="key"),
            runs_store=runs_store,
        )

    call_gemini_text.assert_called_once_with(api_key="key", prompt=prompt, model="gemini-3.5-flash")
    assert run["input_type"] == "text"
    assert run["artifact_hash"] is None
    assert run["provenance"] == {"input": "text_only", "artifact": None}
    assert run["metadata"] == {"source": "dashboard"}
    assert run["prompt_sha256"] == hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    assert run["prompt_length"] == len(prompt)
    assert "prompt" not in runs_store.inserted_run
    assert prompt not in str(runs_store.inserted_run)
