import sys
from types import ModuleType
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient


if "groq" not in sys.modules:
    groq = ModuleType("groq")
    groq.Groq = object
    sys.modules["groq"] = groq

if "google.genai" not in sys.modules:
    google = ModuleType("google")
    genai = ModuleType("google.genai")
    genai.Client = object
    google.genai = genai
    sys.modules["google"] = google
    sys.modules["google.genai"] = genai

import server


def test_post_runs_without_an_artifact_creates_a_text_only_run():
    stored_run = {
        "run_id": "text-run-1",
        "input_type": "text",
        "artifact_hash": None,
        "model": "google/gemini-3.5-flash",
        "label": "comment-sentiment/v1",
        "metadata": {"source": "dashboard"},
        "provenance": {"input": "text_only", "artifact": None},
        "output": '{"sentiment":"positive"}',
    }

    with patch("server.run_text_prompt", Mock(return_value=stored_run)) as run_text:
        response = TestClient(server.app).post(
            "/runs",
            json={
                "prompt": 'Return JSON for this comment: {"comment":"Great post"}',
                "model": "google/gemini-3.5-flash",
                "label": "comment-sentiment/v1",
                "metadata": {"source": "dashboard"},
            },
        )

    assert response.status_code == 200
    assert response.json() == stored_run
    run_text.assert_called_once_with(
        prompt='Return JSON for this comment: {"comment":"Great post"}',
        model="google/gemini-3.5-flash",
        label="comment-sentiment/v1",
        metadata={"source": "dashboard"},
        config=server.config,
        runs_store=server.runs_store,
    )


def test_post_runs_keeps_artifact_runs_on_the_existing_runner():
    stored_run = {"run_id": "artifact-run-1", "artifact_hash": "sha256:video"}

    with patch("server.run_prompt", Mock(return_value=stored_run)) as run_prompt:
        response = TestClient(server.app).post(
            "/runs",
            json={"artifact": "sha256:video", "prompt": "Analyze this video."},
        )

    assert response.status_code == 200
    assert response.json() == stored_run
    run_prompt.assert_called_once_with(
        artifact_hash="sha256:video",
        prompt="Analyze this video.",
        model="google/gemini-2.5-pro",
        label=None,
        metadata={},
        config=server.config,
        artifact_store=server.artifact_store,
        runs_store=server.runs_store,
    )


def test_text_runs_reject_unsupported_model_and_oversized_prompt():
    client = TestClient(server.app)

    unsupported_model = client.post(
        "/runs",
        json={"prompt": "hello", "model": "openai/gpt-5"},
    )
    oversized_prompt = client.post(
        "/runs",
        json={"prompt": "x" * 48_001, "model": "google/gemini-3.5-flash"},
    )

    assert unsupported_model.status_code == 422
    assert oversized_prompt.status_code == 422


def test_text_runs_require_the_same_api_key(monkeypatch):
    monkeypatch.setattr(server, "API_KEY", "test-key")
    client = TestClient(server.app)

    denied = client.post("/runs", json={"prompt": "hello", "model": "google/gemini-3.5-flash"})
    with patch("server.run_text_prompt", Mock(return_value={"run_id": "text-run-1"})):
        allowed = client.post(
            "/runs",
            headers={"X-API-Key": "test-key"},
            json={"prompt": "hello", "model": "google/gemini-3.5-flash"},
        )

    assert denied.status_code == 401
    assert allowed.status_code == 200


def test_text_run_failures_do_not_log_the_prompt(caplog):
    prompt = "private comment text must not appear in logs"
    with patch("server.run_text_prompt", Mock(side_effect=RuntimeError(prompt))):
        response = TestClient(server.app).post(
            "/runs", json={"prompt": prompt, "model": "google/gemini-3.5-flash"}
        )

    assert response.status_code == 500
    assert prompt not in caplog.text
