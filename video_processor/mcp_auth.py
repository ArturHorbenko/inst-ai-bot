"""Authentication helpers for the single-creator MCP resource server."""

import logging
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Optional

import jwt
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OAuthRuntime:
    settings: AuthSettings
    verifier: "OAuthTokenVerifier"


class OAuthTokenVerifier:
    """Verify JWT access tokens issued for this MCP resource."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        resource: str,
        jwks_url: str,
        algorithms: Sequence[str] = ("RS256",),
        signing_key_resolver: Optional[Callable[[str], Any]] = None,
        legacy_bearer_token: Optional[str] = None,
        required_scope: str = "instagram-creator:use",
    ):
        self._issuer = issuer
        self._audience = audience
        self._resource = resource
        self._jwks_client = jwt.PyJWKClient(jwks_url)
        self._algorithms = tuple(algorithms)
        self._signing_key_resolver = signing_key_resolver or self._resolve_signing_key
        self._legacy_bearer_token = legacy_bearer_token
        self._required_scope = required_scope

    def _resolve_signing_key(self, token: str) -> Any:
        return self._jwks_client.get_signing_key_from_jwt(token).key

    async def verify_token(self, token: str) -> AccessToken | None:
        if self._legacy_bearer_token and secrets.compare_digest(token, self._legacy_bearer_token):
            return AccessToken(
                token=token,
                client_id="legacy-bearer-client",
                scopes=[self._required_scope],
                resource=self._resource,
            )
        try:
            # PyJWKClient caches signing keys after the initial fetch. Keep this call
            # synchronous so verification remains compatible with the server's
            # synchronous tool stack and test environment.
            signing_key = self._signing_key_resolver(token)
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=list(self._algorithms),
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iss", "aud"]},
            )
        except (jwt.PyJWTError, ValueError, KeyError):
            logger.warning("Rejected invalid MCP OAuth access token")
            return None

        client_id = claims.get("client_id") or claims.get("azp") or claims.get("sub")
        if not isinstance(client_id, str) or not client_id:
            logger.warning("Rejected MCP OAuth access token without client identity")
            return None

        raw_scopes = claims.get("scope", claims.get("scp", []))
        if isinstance(raw_scopes, str):
            scopes = raw_scopes.split()
        elif isinstance(raw_scopes, list) and all(isinstance(scope, str) for scope in raw_scopes):
            scopes = raw_scopes
        else:
            scopes = []

        return AccessToken(
            token=token,
            client_id=client_id,
            scopes=scopes,
            expires_at=int(claims["exp"]),
            resource=self._resource,
        )


def build_oauth_runtime(
    *,
    issuer_url: Optional[str],
    resource_url: Optional[str],
    jwks_url: Optional[str],
    audience: Optional[str],
    required_scope: str,
    algorithms: Sequence[str] = ("RS256",),
    legacy_bearer_token: Optional[str] = None,
) -> OAuthRuntime:
    missing = [
        name
        for name, value in (
            ("MCP_OAUTH_ISSUER_URL", issuer_url),
            ("MCP_RESOURCE_URL", resource_url),
            ("MCP_OAUTH_JWKS_URL", jwks_url),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"Missing OAuth configuration: {', '.join(missing)}")

    resolved_audience = audience or resource_url
    verifier = OAuthTokenVerifier(
        issuer=issuer_url,
        audience=resolved_audience,
        resource=resource_url,
        jwks_url=jwks_url,
        algorithms=algorithms,
        legacy_bearer_token=legacy_bearer_token,
        required_scope=required_scope,
    )
    settings = AuthSettings(
        issuer_url=issuer_url,
        resource_server_url=resource_url,
        required_scopes=[required_scope],
    )
    return OAuthRuntime(settings=settings, verifier=verifier)
