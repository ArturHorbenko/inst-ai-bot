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
    def insert(self, run):
        return run


def test_text_run_forwards_structured_json_prompt_without_an_artifact():
    prompt = '{"task":"comment_sentiment","comment":"Great post"}'
    call_gemini_text = Mock(return_value='{"sentiment":"positive"}')

    with patch("video_processor.runner.gemini_module.call_gemini_text", call_gemini_text):
        run = run_text_prompt(
            prompt=prompt,
            model="google/gemini-3.5-flash",
            label="comment-sentiment/v1",
            metadata={"source": "dashboard"},
            config=SimpleNamespace(GEMINI_API_KEY="key"),
            runs_store=FakeRunsStore(),
        )

    call_gemini_text.assert_called_once_with(api_key="key", prompt=prompt, model="gemini-3.5-flash")
    assert run["input_type"] == "text"
    assert run["artifact_hash"] is None
    assert run["provenance"] == {"input": "text_only", "artifact": None}
    assert run["metadata"] == {"source": "dashboard"}
