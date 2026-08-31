#!/usr/bin/env python3
"""Exercise public DCR + PKCE + owner approval + MCP initialize without printing secrets."""

import argparse
import base64
import hashlib
import json
import secrets
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin", required=True)
    parser.add_argument("--password-file", default="secrets/oauth-owner-password.txt")
    args = parser.parse_args()
    origin = args.origin.rstrip("/")
    resource = f"{origin}/mcp"
    password = Path(args.password_file).read_text(encoding="utf-8").strip()
    redirect_uri = "http://127.0.0.1/oauth-smoke-callback"
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    session = requests.Session()

    registration_response = session.post(
        f"{origin}/register",
        json={
            "client_name": "inst-ai-bot deployment smoke test",
            "redirect_uris": [redirect_uri],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "scope": "instagram-creator:use",
        },
        timeout=20,
    )
    registration_response.raise_for_status()
    client_id = registration_response.json()["client_id"]

    authorize_response = session.get(
        f"{origin}/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": "instagram-creator:use",
            "state": "smoke-state",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": resource,
        },
        allow_redirects=False,
        timeout=20,
    )
    authorize_response.raise_for_status()
    login_url = authorize_response.headers["Location"]
    request_id = parse_qs(urlparse(login_url).query)["request_id"][0]

    approval_response = session.post(
        f"{origin}/login",
        data={"request_id": request_id, "password": password, "action": "approve"},
        allow_redirects=False,
        timeout=20,
    )
    approval_response.raise_for_status()
    callback = parse_qs(urlparse(approval_response.headers["Location"]).query)
    code = callback["code"][0]
    if callback.get("state") != ["smoke-state"]:
        raise RuntimeError("OAuth state was not preserved")

    token_response = session.post(
        f"{origin}/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
            "resource": resource,
        },
        timeout=20,
    )
    token_response.raise_for_status()
    tokens = token_response.json()

    mcp_response = session.post(
        resource,
        headers={
            "Authorization": f"Bearer {tokens['access_token']}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "oauth-smoke-test", "version": "1.0"},
                },
            }
        ),
        timeout=30,
    )
    mcp_response.raise_for_status()

    revoke_response = session.post(
        f"{origin}/revoke",
        data={
            "client_id": client_id,
            "client_secret": "",
            "token": tokens["refresh_token"],
            "token_type_hint": "refresh_token",
        },
        timeout=20,
    )
    revoke_response.raise_for_status()
    print("registration=ok authorization=ok token=ok mcp_initialize=ok revocation=ok")


if __name__ == "__main__":
    main()
