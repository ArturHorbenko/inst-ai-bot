import asyncio
import importlib
import sys
from types import ModuleType

import pytest

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
from video_processor import config as config_module
from video_processor.config import Config


def authenticated_app_client(monkeypatch):
    fake_config = Config(
        MONGODB_URI="mongodb://test",
        MONGODB_DB="test",
        MCP_AUTH_MODE="bearer",
    )
    monkeypatch.setattr(config_module, "get_config", lambda: fake_config)
    module = importlib.reload(mcp_server)
    monkeypatch.setattr(module, "API_KEY", "test-bearer-token")
    return module.build_app()


async def request(app, path, authorization=None):
    class ResponseComplete(Exception):
        pass

    messages = []
    headers = []
    if authorization:
        headers.append((b"authorization", authorization.encode("latin-1")))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("test", 123),
        "server": ("test", 80),
    }

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body", False):
            raise ResponseComplete

    try:
        await asyncio.wait_for(app(scope, receive, send), timeout=2)
    except ResponseComplete:
        pass
    return next(message["status"] for message in messages if message["type"] == "http.response.start")


def test_oauth_protected_resource_metadata_paths_return_normal_404(monkeypatch):
    app = authenticated_app_client(monkeypatch)

    for path in (
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/mcp",
    ):
        assert asyncio.run(request(app, path)) == 404


def test_bearer_auth_rejects_missing_and_wrong_tokens(monkeypatch):
    app = authenticated_app_client(monkeypatch)

    assert asyncio.run(request(app, "/mcp")) == 401
    assert asyncio.run(request(app, "/health", "Bearer wrong-token")) == 401


def test_bearer_auth_allows_the_configured_token_through_middleware(monkeypatch):
    app = authenticated_app_client(monkeypatch)

    # The MCP app has no /health route, so 404 proves the auth middleware
    # accepted the token and passed the request to the underlying application.
    assert asyncio.run(request(app, "/health", "Bearer test-bearer-token")) == 404


def test_bearer_mode_fails_closed_when_api_key_is_missing(monkeypatch):
    module = importlib.reload(mcp_server)
    monkeypatch.setattr(module, "AUTH_MODE", "bearer")
    monkeypatch.setattr(module, "API_KEY", "")

    with pytest.raises(RuntimeError, match="INST_AI_BOT_API_KEY"):
        module.build_app()


def test_auth_can_only_be_disabled_explicitly_for_development(monkeypatch):
    module = importlib.reload(mcp_server)
    monkeypatch.setattr(module, "AUTH_MODE", "disabled-dev")
    monkeypatch.setattr(module, "API_KEY", "")

    assert module.build_app() is not None
