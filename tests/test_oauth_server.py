import base64
import asyncio
import hashlib
from urllib.parse import parse_qs, urlparse

import jwt
import httpx
from cryptography.hazmat.primitives.asymmetric import rsa

from video_processor.oauth_server import (
    InMemoryOAuthRepository,
    OAuthServerConfig,
    build_app,
    hash_password,
    verify_password,
)


ISSUER = "https://auth.example.com/"
RESOURCE = "https://mcp.example.com/mcp"
REDIRECT_URI = "https://client.example.com/callback"
PASSWORD = "correct horse battery staple"


def _pkce_pair(verifier: str = "a" * 64) -> tuple[str, str]:
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def _test_app():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    config = OAuthServerConfig(
        issuer_url=ISSUER,
        resource_url=RESOURCE,
        required_scope="instagram-creator:use",
        admin_password_hash=hash_password(PASSWORD),
        signing_key_id="test-key",
        access_token_ttl=900,
        refresh_token_ttl=3600,
    )
    repository = InMemoryOAuthRepository()
    return build_app(config, repository=repository, private_key=private_key), private_key


async def _register(client: httpx.AsyncClient) -> dict:
    response = await client.post(
        "/register",
        json={
            "client_name": "ChatGPT test",
            "redirect_uris": [REDIRECT_URI],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "scope": "instagram-creator:use",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _authorize(client: httpx.AsyncClient, client_id: str, challenge: str) -> str:
    response = await client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "scope": "instagram-creator:use",
            "state": "state-123",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": RESOURCE,
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    login_url = response.headers["location"]
    assert login_url.startswith(f"{ISSUER}login?")
    return parse_qs(urlparse(login_url).query)["request_id"][0]


async def _approve(client: httpx.AsyncClient, request_id: str) -> str:
    response = await client.post(
        "/login",
        data={"request_id": request_id, "password": PASSWORD, "action": "approve"},
        follow_redirects=False,
    )
    assert response.status_code == 302, response.text
    callback = urlparse(response.headers["location"])
    assert f"{callback.scheme}://{callback.netloc}{callback.path}" == REDIRECT_URI
    query = parse_qs(callback.query)
    assert query["state"] == ["state-123"]
    return query["code"][0]


def test_password_hash_is_salted_and_verifiable():
    first = hash_password(PASSWORD)
    second = hash_password(PASSWORD)

    assert first != second
    assert verify_password(PASSWORD, first)
    assert not verify_password("wrong", first)


def test_oauth_discovery_registration_and_pkce_flow():
    asyncio.run(_oauth_discovery_registration_and_pkce_flow())


async def _oauth_discovery_registration_and_pkce_flow():
    app, private_key = _test_app()
    verifier, challenge = _pkce_pair()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ISSUER) as client:
        metadata = await client.get("/.well-known/oauth-authorization-server")
        assert metadata.status_code == 200
        assert metadata.json()["issuer"] == ISSUER
        assert metadata.json()["registration_endpoint"] == f"{ISSUER}register"
        assert metadata.json()["code_challenge_methods_supported"] == ["S256"]

        jwks = await client.get("/jwks.json")
        assert jwks.status_code == 200
        assert jwks.json()["keys"][0]["kid"] == "test-key"

        registration = await _register(client)
        request_id = await _authorize(client, registration["client_id"], challenge)

        login_page = await client.get("/login", params={"request_id": request_id})
        assert login_page.status_code == 200
        assert "ChatGPT test" in login_page.text
        assert (
            "form-action 'self' https://client.example.com"
            in login_page.headers["content-security-policy"]
        )

        wrong_password = await client.post(
            "/login",
            data={"request_id": request_id, "password": "wrong", "action": "approve"},
        )
        assert wrong_password.status_code == 401

        code = await _approve(client, request_id)
        token_response = await client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "client_id": registration["client_id"],
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": REDIRECT_URI,
                "resource": RESOURCE,
            },
        )
        assert token_response.status_code == 200, token_response.text
        tokens = token_response.json()
        assert tokens["token_type"] == "Bearer"
        assert tokens["scope"] == "instagram-creator:use"
        assert tokens["refresh_token"]

        public_key = private_key.public_key()
        claims = jwt.decode(
            tokens["access_token"],
            public_key,
            algorithms=["RS256"],
            issuer=ISSUER,
            audience=RESOURCE,
        )
        assert claims["client_id"] == registration["client_id"]
        assert claims["scope"] == "instagram-creator:use"

        replay = await client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "client_id": registration["client_id"],
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": REDIRECT_URI,
            },
        )
        assert replay.status_code == 400
        assert replay.json()["error"] == "invalid_grant"


def test_refresh_tokens_rotate_and_revocation_invalidates_them():
    asyncio.run(_refresh_tokens_rotate_and_revocation_invalidates_them())


async def _refresh_tokens_rotate_and_revocation_invalidates_them():
    app, _ = _test_app()
    verifier, challenge = _pkce_pair()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ISSUER) as client:
        registration = await _register(client)
        code = await _approve(client, await _authorize(client, registration["client_id"], challenge))
        tokens = (await client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "client_id": registration["client_id"],
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": REDIRECT_URI,
            },
        )).json()

        rotated = await client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "client_id": registration["client_id"],
                "refresh_token": tokens["refresh_token"],
            },
        )
        assert rotated.status_code == 200, rotated.text
        assert rotated.json()["refresh_token"] != tokens["refresh_token"]

        old_refresh = await client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "client_id": registration["client_id"],
                "refresh_token": tokens["refresh_token"],
            },
        )
        assert old_refresh.status_code == 400
        assert old_refresh.json()["error"] == "invalid_grant"

        revoked = await client.post(
            "/revoke",
            data={
                    "client_id": registration["client_id"],
                    "client_secret": "",
                    "token": rotated.json()["refresh_token"],
                "token_type_hint": "refresh_token",
            },
        )
        assert revoked.status_code == 200

        after_revoke = await client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "client_id": registration["client_id"],
                "refresh_token": rotated.json()["refresh_token"],
            },
        )
        assert after_revoke.status_code == 400


def test_registration_rejects_insecure_remote_redirect_uri():
    asyncio.run(_registration_rejects_insecure_remote_redirect_uri())


async def _registration_rejects_insecure_remote_redirect_uri():
    app, _ = _test_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ISSUER) as client:
        response = await client.post(
            "/register",
            json={
                "redirect_uris": ["http://attacker.example/callback"],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
            },
        )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_redirect_uri"
