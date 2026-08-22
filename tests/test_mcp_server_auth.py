import importlib
import sys
from types import ModuleType

from fastapi.testclient import TestClient


if "groq" not in sys.modules:
    groq = ModuleType("groq")
    groq.Groq = object
    sys.modules["groq"] = groq

if "google.genai" not in sys.modules:
    google = ModuleType("google")
    genai = ModuleType("google.genai")
    genai.Client = object
    genai.types = ModuleType("google.genai.types")
    google.genai = genai
    sys.modules["google"] = google
    sys.modules["google.genai"] = genai
    sys.modules["google.genai.types"] = genai.types

from video_processor import mcp_server


def authenticated_app_client(monkeypatch):
    module = importlib.reload(mcp_server)
    monkeypatch.setattr(module, "API_KEY", "test-bearer-token")
    return TestClient(module.build_app())


def test_oauth_protected_resource_metadata_paths_return_normal_404(monkeypatch):
    with authenticated_app_client(monkeypatch) as client:
        for path in (
            "/.well-known/oauth-protected-resource",
            "/.well-known/oauth-protected-resource/mcp",
        ):
            response = client.get(path)

            assert response.status_code == 404


def test_bearer_auth_still_protects_mcp_and_other_paths(monkeypatch):
    with authenticated_app_client(monkeypatch) as client:
        assert client.get("/mcp").status_code == 401
        assert client.get("/not-an-mcp-metadata-path").status_code == 401
