"""Single-user OAuth 2.1 authorization server for the private MCP service.

The MCP Python SDK owns the protocol request parsing, PKCE verification,
dynamic client registration, token endpoint, revocation endpoint, and RFC 8414
metadata. This module supplies the deliberately small policy surface: one
password-protected owner, persistent grants in MongoDB, and RS256 JWTs bound to
the configured MCP resource.
"""

from __future__ import annotations

import base64
import hashlib
import html
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.server.auth.routes import create_auth_routes
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl
from pymongo import ASCENDING
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route

from .config import Config, get_config
from .store import DatabaseConnection


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    """Return a versioned, salted scrypt password hash."""
    if len(password) < 16:
        raise ValueError("OAuth owner password must contain at least 16 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${_b64url(salt)}${_b64url(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, raw_n, raw_r, raw_p, raw_salt, raw_digest = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(raw_salt + "=" * (-len(raw_salt) % 4))
        expected = base64.urlsafe_b64decode(raw_digest + "=" * (-len(raw_digest) % 4))
        actual = hashlib.scrypt(
            password.encode(),
            salt=salt,
            n=int(raw_n),
            r=int(raw_r),
            p=int(raw_p),
            dklen=len(expected),
        )
        return secrets.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True)
class OAuthServerConfig:
    issuer_url: str
    resource_url: str
    required_scope: str
    admin_password_hash: str
    signing_key_id: str = "inst-ai-bot-oauth-1"
    access_token_ttl: int = 900
    refresh_token_ttl: int = 30 * 24 * 60 * 60
    authorization_code_ttl: int = 300
    pending_request_ttl: int = 300


class OAuthRepository(Protocol):
    def put(self, kind: str, key: str, payload: dict[str, Any], expires_at: float | None = None) -> None: ...

    def get(self, kind: str, key: str) -> dict[str, Any] | None: ...

    def pop(self, kind: str, key: str) -> dict[str, Any] | None: ...

    def delete_family(self, family_id: str) -> None: ...


class InMemoryOAuthRepository:
    """Thread-safe repository used by tests and explicit local development."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = threading.Lock()

    def put(self, kind: str, key: str, payload: dict[str, Any], expires_at: float | None = None) -> None:
        with self._lock:
            self._items[(kind, key)] = {**payload, "expires_at": expires_at}

    def _valid(self, item: dict[str, Any] | None) -> dict[str, Any] | None:
        if not item:
            return None
        expires_at = item.get("expires_at")
        if expires_at is not None and expires_at < time.time():
            return None
        return dict(item)

    def get(self, kind: str, key: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._valid(self._items.get((kind, key)))
            if item is None:
                self._items.pop((kind, key), None)
            return item

    def pop(self, kind: str, key: str) -> dict[str, Any] | None:
        with self._lock:
            return self._valid(self._items.pop((kind, key), None))

    def delete_family(self, family_id: str) -> None:
        with self._lock:
            self._items = {
                key: value
                for key, value in self._items.items()
                if value.get("family_id") != family_id
            }


class MongoOAuthRepository:
    """Persistent OAuth state; opaque credentials are indexed by SHA-256 hash."""

    def __init__(self, database) -> None:
        self._collection = database["oauth_state"]
        self._collection.create_index([("kind", ASCENDING), ("key", ASCENDING)], unique=True)
        self._collection.create_index("expires_at", expireAfterSeconds=0)
        self._collection.create_index("family_id")

    def put(self, kind: str, key: str, payload: dict[str, Any], expires_at: float | None = None) -> None:
        from datetime import datetime, timezone

        stored_expiry = datetime.fromtimestamp(expires_at, timezone.utc) if expires_at is not None else None
        document = {
            "kind": kind,
            "key": key,
            "payload": payload,
            "expires_at": stored_expiry,
            "family_id": payload.get("family_id"),
        }
        self._collection.replace_one({"kind": kind, "key": key}, document, upsert=True)

    @staticmethod
    def _payload(document: dict[str, Any] | None) -> dict[str, Any] | None:
        from datetime import timezone

        if not document:
            return None
        expires_at = document.get("expires_at")
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at is not None and expires_at.timestamp() < time.time():
            return None
        payload = dict(document["payload"])
        payload["expires_at"] = expires_at.timestamp() if expires_at is not None else None
        return payload

    def get(self, kind: str, key: str) -> dict[str, Any] | None:
        document = self._collection.find_one({"kind": kind, "key": key})
        payload = self._payload(document)
        if document and payload is None:
            self._collection.delete_one({"_id": document["_id"]})
        return payload

    def pop(self, kind: str, key: str) -> dict[str, Any] | None:
        document = self._collection.find_one_and_delete({"kind": kind, "key": key})
        return self._payload(document)

    def delete_family(self, family_id: str) -> None:
        self._collection.delete_many({"family_id": family_id})


class StoredRefreshToken(RefreshToken):
    family_id: str


class StoredAccessToken(AccessToken):
    family_id: str


class SingleUserOAuthProvider:
    def __init__(
        self,
        config: OAuthServerConfig,
        repository: OAuthRepository,
        private_key: rsa.RSAPrivateKey,
    ) -> None:
        self.config = config
        self.repository = repository
        self.private_key = private_key

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        payload = self.repository.get("client", client_id)
        return OAuthClientInformationFull.model_validate(payload) if payload else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if client_info.token_endpoint_auth_method not in {"none", "client_secret_basic", "client_secret_post"}:
            raise RegistrationError("invalid_client_metadata", "Unsupported token endpoint authentication method")
        for raw_uri in client_info.redirect_uris or []:
            parsed = urlparse(str(raw_uri))
            is_loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            if parsed.scheme != "https" and not (parsed.scheme == "http" and is_loopback):
                raise RegistrationError(
                    "invalid_redirect_uri",
                    "Redirect URIs must use HTTPS, except for loopback development clients",
                )
            if parsed.fragment:
                raise RegistrationError("invalid_redirect_uri", "Redirect URIs must not contain fragments")
        if not client_info.client_id:
            raise RegistrationError("invalid_client_metadata", "Client ID is required")
        self.repository.put("client", client_info.client_id, client_info.model_dump(mode="json"))

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        scopes = params.scopes or []
        if self.config.required_scope not in scopes:
            raise AuthorizeError("invalid_scope", f"Required scope: {self.config.required_scope}")
        if params.resource and params.resource != self.config.resource_url:
            raise AuthorizeError("invalid_request", "The requested resource is not this MCP server")
        request_id = secrets.token_urlsafe(32)
        expires_at = time.time() + self.config.pending_request_ttl
        self.repository.put(
            "pending",
            _token_hash(request_id),
            {
                "client_id": client.client_id,
                "params": params.model_dump(mode="json"),
            },
            expires_at,
        )
        return f"{self.config.issuer_url.rstrip('/')}/login?request_id={request_id}"

    def pending_request(self, request_id: str) -> tuple[OAuthClientInformationFull, AuthorizationParams] | None:
        payload = self.repository.get("pending", _token_hash(request_id))
        if not payload:
            return None
        client_payload = self.repository.get("client", payload["client_id"])
        if not client_payload:
            return None
        return (
            OAuthClientInformationFull.model_validate(client_payload),
            AuthorizationParams.model_validate(payload["params"]),
        )

    def complete_authorization(self, request_id: str, approved: bool) -> str | None:
        payload = self.repository.pop("pending", _token_hash(request_id))
        if not payload:
            return None
        params = AuthorizationParams.model_validate(payload["params"])
        if not approved:
            return construct_redirect_uri(
                str(params.redirect_uri),
                error="access_denied",
                error_description="The owner denied access",
                state=params.state,
            )
        code = secrets.token_urlsafe(32)
        authorization_code = AuthorizationCode(
            code=code,
            scopes=params.scopes or [self.config.required_scope],
            expires_at=time.time() + self.config.authorization_code_ttl,
            client_id=payload["client_id"],
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource or self.config.resource_url,
        )
        self.repository.put(
            "code",
            _token_hash(code),
            authorization_code.model_dump(mode="json", exclude={"code"}),
            authorization_code.expires_at,
        )
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        payload = self.repository.get("code", _token_hash(authorization_code))
        if not payload or payload.get("client_id") != client.client_id:
            return None
        return AuthorizationCode(code=authorization_code, **payload)

    def _issue_tokens(self, client_id: str, scopes: list[str], family_id: str | None = None) -> OAuthToken:
        now = int(time.time())
        family_id = family_id or secrets.token_urlsafe(24)
        jti = secrets.token_urlsafe(24)
        access_expires_at = now + self.config.access_token_ttl
        access_token = jwt.encode(
            {
                "iss": self.config.issuer_url,
                "aud": self.config.resource_url,
                "sub": "single-creator-owner",
                "client_id": client_id,
                "scope": " ".join(scopes),
                "iat": now,
                "exp": access_expires_at,
                "jti": jti,
            },
            self.private_key,
            algorithm="RS256",
            headers={"kid": self.config.signing_key_id, "typ": "at+jwt"},
        )
        refresh_token = secrets.token_urlsafe(48)
        refresh_expires_at = now + self.config.refresh_token_ttl
        self.repository.put(
            "access",
            _token_hash(access_token),
            {
                "client_id": client_id,
                "scopes": scopes,
                "resource": self.config.resource_url,
                "family_id": family_id,
            },
            access_expires_at,
        )
        self.repository.put(
            "refresh",
            _token_hash(refresh_token),
            {
                "client_id": client_id,
                "scopes": scopes,
                "family_id": family_id,
            },
            refresh_expires_at,
        )
        return OAuthToken(
            access_token=access_token,
            token_type="Bearer",
            expires_in=self.config.access_token_ttl,
            scope=" ".join(scopes),
            refresh_token=refresh_token,
        )

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        payload = self.repository.pop("code", _token_hash(authorization_code.code))
        if not payload or payload.get("client_id") != client.client_id:
            raise TokenError("invalid_grant", "Authorization code was already used or expired")
        return self._issue_tokens(client.client_id or "", authorization_code.scopes)

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> StoredRefreshToken | None:
        payload = self.repository.get("refresh", _token_hash(refresh_token))
        if not payload or payload.get("client_id") != client.client_id:
            return None
        return StoredRefreshToken(token=refresh_token, **payload)

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: StoredRefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        payload = self.repository.pop("refresh", _token_hash(refresh_token.token))
        if not payload or payload.get("client_id") != client.client_id:
            raise TokenError("invalid_grant", "Refresh token was already used, revoked, or expired")
        return self._issue_tokens(client.client_id or "", scopes, payload["family_id"])

    async def load_access_token(self, token: str) -> StoredAccessToken | None:
        payload = self.repository.get("access", _token_hash(token))
        return StoredAccessToken(token=token, **payload) if payload else None

    async def revoke_token(self, token: StoredAccessToken | StoredRefreshToken) -> None:
        self.repository.delete_family(token.family_id)

    def jwks(self) -> dict[str, list[dict[str, str]]]:
        numbers = self.private_key.public_key().public_numbers()
        return {
            "keys": [
                {
                    "kty": "RSA",
                    "use": "sig",
                    "alg": "RS256",
                    "kid": self.config.signing_key_id,
                    "n": _b64url(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
                    "e": _b64url(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
                }
            ]
        }


def _security_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
    }


def _login_page(client_name: str, scope: str, request_id: str, error: str | None = None) -> str:
    error_html = f'<p class="error">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Authorize Instagram Creator MCP</title><style>
body{{font:16px system-ui;background:#f4f4f5;color:#18181b;margin:0;padding:2rem}}
main{{max-width:28rem;margin:8vh auto;background:white;padding:2rem;border-radius:16px;box-shadow:0 8px 30px #0001}}
input,button{{box-sizing:border-box;width:100%;padding:.8rem;margin-top:.65rem;font:inherit}}
button{{background:#18181b;color:white;border:0;border-radius:8px;cursor:pointer}}
.deny{{background:#e4e4e7;color:#18181b}}.error{{color:#b91c1c}}code{{word-break:break-all}}
</style></head><body><main><h1>Authorize MCP access</h1>
<p><strong>{html.escape(client_name)}</strong> is requesting access to your single-creator Instagram tools.</p>
<p>Scope: <code>{html.escape(scope)}</code></p>{error_html}
<form method="post" action="/login">
<input type="hidden" name="request_id" value="{html.escape(request_id, quote=True)}">
<label>Owner password<input type="password" name="password" required autocomplete="current-password"></label>
<button name="action" value="approve" type="submit">Approve</button>
<button class="deny" name="action" value="deny" type="submit" formnovalidate>Deny</button>
</form></main></body></html>"""


class LoginRateLimiter:
    def __init__(self, attempts: int = 8, window_seconds: int = 300) -> None:
        self.attempts = attempts
        self.window_seconds = window_seconds
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allowed(self, client_ip: str) -> bool:
        now = time.monotonic()
        with self._lock:
            recent = [value for value in self._failures.get(client_ip, []) if value > now - self.window_seconds]
            self._failures[client_ip] = recent
            return len(recent) < self.attempts

    def failed(self, client_ip: str) -> None:
        with self._lock:
            self._failures.setdefault(client_ip, []).append(time.monotonic())

    def succeeded(self, client_ip: str) -> None:
        with self._lock:
            self._failures.pop(client_ip, None)


def build_app(
    config: OAuthServerConfig,
    *,
    repository: OAuthRepository,
    private_key: rsa.RSAPrivateKey,
) -> Starlette:
    provider = SingleUserOAuthProvider(config, repository, private_key)
    limiter = LoginRateLimiter()

    async def login_get(request: Request):
        request_id = request.query_params.get("request_id", "")
        pending = provider.pending_request(request_id)
        if not pending:
            return HTMLResponse("Authorization request is invalid or expired.", status_code=400, headers=_security_headers())
        client, params = pending
        return HTMLResponse(
            _login_page(client.client_name or "An MCP client", " ".join(params.scopes or []), request_id),
            headers=_security_headers(),
        )

    async def login_post(request: Request):
        form = await request.form()
        request_id = str(form.get("request_id", ""))
        action = str(form.get("action", ""))
        forwarded_for = request.headers.get("x-forwarded-for", "")
        client_ip = (
            forwarded_for.split(",", 1)[0].strip()
            if forwarded_for
            else (request.client.host if request.client else "unknown")
        )
        pending = provider.pending_request(request_id)
        if not pending:
            return HTMLResponse("Authorization request is invalid or expired.", status_code=400, headers=_security_headers())
        client, params = pending
        if action == "deny":
            target = provider.complete_authorization(request_id, approved=False)
            return RedirectResponse(target or "/", status_code=302, headers={"Cache-Control": "no-store"})
        if not limiter.allowed(client_ip):
            return HTMLResponse("Too many failed login attempts. Try again later.", status_code=429, headers=_security_headers())
        password = str(form.get("password", ""))
        if not verify_password(password, config.admin_password_hash):
            limiter.failed(client_ip)
            return HTMLResponse(
                _login_page(
                    client.client_name or "An MCP client",
                    " ".join(params.scopes or []),
                    request_id,
                    "Incorrect password",
                ),
                status_code=401,
                headers=_security_headers(),
            )
        limiter.succeeded(client_ip)
        target = provider.complete_authorization(request_id, approved=True)
        if not target:
            return HTMLResponse("Authorization request is invalid or expired.", status_code=400, headers=_security_headers())
        return RedirectResponse(target, status_code=302, headers={"Cache-Control": "no-store"})

    async def jwks(_request: Request):
        return JSONResponse(provider.jwks(), headers={"Cache-Control": "public, max-age=300"})

    async def health(_request: Request):
        return JSONResponse({"status": "healthy", "service": "inst-ai-bot-oauth"})

    routes = create_auth_routes(
        provider,
        AnyHttpUrl(config.issuer_url),
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=[config.required_scope],
            default_scopes=[config.required_scope],
        ),
        revocation_options=RevocationOptions(enabled=True),
    )
    routes.extend(
        [
            Route("/login", login_get, methods=["GET"]),
            Route("/login", login_post, methods=["POST"]),
            Route("/jwks.json", jwks, methods=["GET"]),
            Route("/oauth/health", health, methods=["GET"]),
        ]
    )
    return Starlette(routes=routes)


def _load_private_key(path: str) -> rsa.RSAPrivateKey:
    key = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValueError("OAUTH_SIGNING_KEY_PATH must contain an RSA private key")
    return key


def server_config_from_app_config(config: Config) -> OAuthServerConfig:
    missing = [
        name
        for name, value in (
            ("MCP_OAUTH_ISSUER_URL", config.MCP_OAUTH_ISSUER_URL),
            ("MCP_RESOURCE_URL", config.MCP_RESOURCE_URL),
            ("OAUTH_ADMIN_PASSWORD_HASH", config.OAUTH_ADMIN_PASSWORD_HASH),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"Missing OAuth server configuration: {', '.join(missing)}")
    return OAuthServerConfig(
        issuer_url=config.MCP_OAUTH_ISSUER_URL.rstrip("/") + "/",
        resource_url=config.MCP_RESOURCE_URL,
        required_scope=config.MCP_OAUTH_SCOPE,
        admin_password_hash=config.OAUTH_ADMIN_PASSWORD_HASH,
        signing_key_id=config.OAUTH_SIGNING_KEY_ID,
        access_token_ttl=config.OAUTH_ACCESS_TOKEN_TTL,
        refresh_token_ttl=config.OAUTH_REFRESH_TOKEN_TTL,
    )


def build_production_app() -> Starlette:
    app_config = get_config()
    oauth_config = server_config_from_app_config(app_config)
    if not app_config.OAUTH_SIGNING_KEY_PATH:
        raise ValueError("Missing OAuth server configuration: OAUTH_SIGNING_KEY_PATH")
    database = DatabaseConnection(app_config)
    if not database.connect():
        raise RuntimeError("MongoDB connection required for OAuth server")
    app = build_app(
        oauth_config,
        repository=MongoOAuthRepository(database.db),
        private_key=_load_private_key(app_config.OAUTH_SIGNING_KEY_PATH),
    )

    async def close_database() -> None:
        database.close()

    app.add_event_handler("shutdown", close_database)
    return app
