#!/usr/bin/env python3
"""Create a local RSA signing key and print a scrypt hash for an owner password."""

import argparse
import getpass
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

from video_processor.oauth_server import hash_password  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--key-path", default="secrets/oauth-signing-key.pem")
    parser.add_argument(
        "--generate-password",
        action="store_true",
        help="Generate the owner password and store it in a mode-0600 local file",
    )
    parser.add_argument("--password-file", default="secrets/oauth-owner-password.txt")
    parser.add_argument("--env-file", help="Update OAUTH_ADMIN_PASSWORD_HASH in this env file")
    parser.add_argument("--issuer-url", help="Public OAuth issuer URL")
    parser.add_argument("--resource-url", help="Canonical public MCP URL")
    parser.add_argument(
        "--configure-only",
        action="store_true",
        help="Keep existing secrets and only update OAuth endpoint settings",
    )
    args = parser.parse_args()
    key_path = Path(args.key_path)
    if args.configure_only:
        if not args.env_file or not args.issuer_url or not args.resource_url:
            raise SystemExit("--configure-only requires --env-file, --issuer-url, and --resource-url")
        env_path = Path(args.env_file)
        lines = env_path.read_text(encoding="utf-8").splitlines()
        values = {
            "MCP_AUTH_MODE": "oauth-and-bearer",
            "MCP_RESOURCE_URL": args.resource_url,
            "MCP_OAUTH_ISSUER_URL": args.issuer_url.rstrip("/") + "/",
            "MCP_OAUTH_JWKS_URL": args.issuer_url.rstrip("/") + "/jwks.json",
            "MCP_OAUTH_AUDIENCE": args.resource_url,
            "MCP_OAUTH_SCOPE": "instagram-creator:use",
            "MCP_OAUTH_ALGORITHMS": "RS256",
            "OAUTH_SIGNING_KEY_PATH": str(key_path),
            "OAUTH_SIGNING_KEY_ID": "inst-ai-bot-oauth-1",
            "OAUTH_ACCESS_TOKEN_TTL": "900",
            "OAUTH_REFRESH_TOKEN_TTL": "2592000",
            "OAUTH_HOST": "127.0.0.1",
            "OAUTH_PORT": "8003",
        }
        lines = [line for line in lines if line.split("=", 1)[0] not in values]
        lines.extend(f"{name}={value}" for name, value in values.items())
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.chmod(env_path, 0o600)
        print(f"Configured OAuth endpoints in {env_path}")
        return
    if key_path.exists():
        raise SystemExit(f"Refusing to overwrite existing signing key: {key_path}")

    if args.generate_password:
        password = secrets.token_urlsafe(24)
        password_path = Path(args.password_file)
        if password_path.exists():
            raise SystemExit(f"Refusing to overwrite existing owner password: {password_path}")
        password_path.parent.mkdir(parents=True, exist_ok=True)
        password_path.write_text(password + "\n", encoding="utf-8")
        os.chmod(password_path, 0o600)
    else:
        password = getpass.getpass("OAuth owner password (minimum 16 characters): ")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            raise SystemExit("Passwords do not match")

    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    os.chmod(key_path, 0o600)
    password_hash = hash_password(password)
    if args.env_file:
        env_path = Path(args.env_file)
        lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
        lines = [line for line in lines if not line.startswith("OAUTH_ADMIN_PASSWORD_HASH=")]
        lines.append(f"OAUTH_ADMIN_PASSWORD_HASH={password_hash}")
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.chmod(env_path, 0o600)

    print(f"Created signing key: {key_path}")
    if args.generate_password:
        print(f"Created owner password: {args.password_file}")
    if args.env_file:
        print(f"Updated password hash: {args.env_file}")
    else:
        print("Add this value to .env:")
        print(f"OAUTH_ADMIN_PASSWORD_HASH={password_hash}")


if __name__ == "__main__":
    main()
