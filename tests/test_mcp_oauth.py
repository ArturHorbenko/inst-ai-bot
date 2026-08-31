import asyncio
import importlib
import json
import sys
import time
from types import ModuleType

import jwt

from video_processor import config as config_module
from video_processor.config import Config
from video_processor.mcp_auth import OAuthTokenVerifier


TEST_SIGNING_SECRET = "test-signing-secret-with-at-least-32-bytes"


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


async def request(app, path):
    class ResponseComplete(Exception):
        pass

    messages = []
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": ("test", 123),
        "server": ("mcp.example.com", 443),
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

    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return start["status"], dict(start["headers"]), body


def test_oauth_verifier_accepts_a_valid_resource_bound_token():
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": "https://auth.example.com/",
            "aud": "https://mcp.example.com/mcp",
            "sub": "single-creator-owner",
            "client_id": "test-client",
            "scope": "instagram-creator:use",
            "iat": now,
            "exp": now + 300,
        },
        TEST_SIGNING_SECRET,
        algorithm="HS256",
    )
    verifier = OAuthTokenVerifier(
        issuer="https://auth.example.com/",
        audience="https://mcp.example.com/mcp",
        resource="https://mcp.example.com/mcp",
        jwks_url="https://auth.example.com/.well-known/jwks.json",
        algorithms=("HS256",),
        signing_key_resolver=lambda _: TEST_SIGNING_SECRET,
    )

    access_token = asyncio.run(verifier.verify_token(token))

    assert access_token is not None
    assert access_token.client_id == "test-client"
    assert access_token.scopes == ["instagram-creator:use"]
    assert access_token.resource == "https://mcp.example.com/mcp"


def test_oauth_verifier_rejects_a_token_for_another_resource():
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": "https://auth.example.com/",
            "aud": "https://another.example.com/mcp",
            "sub": "single-creator-owner",
            "scope": "instagram-creator:use",
            "exp": now + 300,
        },
        TEST_SIGNING_SECRET,
        algorithm="HS256",
    )
    verifier = OAuthTokenVerifier(
        issuer="https://auth.example.com/",
        audience="https://mcp.example.com/mcp",
        resource="https://mcp.example.com/mcp",
        jwks_url="https://auth.example.com/.well-known/jwks.json",
        algorithms=("HS256",),
        signing_key_resolver=lambda _: TEST_SIGNING_SECRET,
    )

    assert asyncio.run(verifier.verify_token(token)) is None


def test_oauth_verifier_rejects_invalid_signature_issuer_and_expiry():
    now = int(time.time())
    verifier = OAuthTokenVerifier(
        issuer="https://auth.example.com/",
        audience="https://mcp.example.com/mcp",
        resource="https://mcp.example.com/mcp",
        jwks_url="https://auth.example.com/.well-known/jwks.json",
        algorithms=("HS256",),
        signing_key_resolver=lambda _: TEST_SIGNING_SECRET,
    )
    base_claims = {
        "iss": "https://auth.example.com/",
        "aud": "https://mcp.example.com/mcp",
        "sub": "single-creator-owner",
        "scope": "instagram-creator:use",
        "exp": now + 300,
    }
    invalid_claims = (
        (base_claims, "another-signing-secret-with-32-bytes"),
        ({**base_claims, "iss": "https://wrong.example.com/"}, TEST_SIGNING_SECRET),
        ({**base_claims, "exp": now - 1}, TEST_SIGNING_SECRET),
    )

    for claims, signing_secret in invalid_claims:
        token = jwt.encode(claims, signing_secret, algorithm="HS256")
        assert asyncio.run(verifier.verify_token(token)) is None


def test_oauth_mode_publishes_resource_metadata_and_auth_challenge(monkeypatch):
    fake_config = Config(
        MONGODB_URI="mongodb://test",
        MONGODB_DB="test",
        MCP_AUTH_MODE="oauth",
        MCP_RESOURCE_URL="https://mcp.example.com/mcp",
        MCP_OAUTH_ISSUER_URL="https://auth.example.com/",
        MCP_OAUTH_JWKS_URL="https://auth.example.com/.well-known/jwks.json",
        MCP_OAUTH_AUDIENCE="https://mcp.example.com/mcp",
        MCP_OAUTH_SCOPE="instagram-creator:use",
    )
    with monkeypatch.context() as patcher:
        patcher.setattr(config_module, "get_config", lambda: fake_config)
        from video_processor import mcp_server

        module = importlib.reload(mcp_server)
        app = module.build_app()

        tools = asyncio.run(module.mcp.list_tools())
        for tool in tools:
            assert tool.meta["securitySchemes"] == [
                {"type": "oauth2", "scopes": ["instagram-creator:use"]}
            ]

        status, _, body = asyncio.run(request(app, "/.well-known/oauth-protected-resource/mcp"))
        metadata = json.loads(body)
        assert status == 200
        assert metadata["resource"] == "https://mcp.example.com/mcp"
        assert metadata["authorization_servers"] == ["https://auth.example.com/"]
        assert metadata["scopes_supported"] == ["instagram-creator:use"]

        status, _, body = asyncio.run(request(app, "/.well-known/oauth-protected-resource"))
        assert status == 200
        assert json.loads(body) == metadata

        status, headers, _ = asyncio.run(request(app, "/mcp"))
        assert status == 401
        assert b"resource_metadata=" in headers[b"www-authenticate"]

    importlib.reload(mcp_server)
