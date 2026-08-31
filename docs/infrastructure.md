# Runtime infrastructure

How the server actually runs on the host machine right now. Read this before assuming the server is launched via `npm run dev:backend` or `python -m fastapi run` — it isn't, in normal operation.

## Topology

```
public internet
       │
       ▼
Tailscale Funnel edge  (https://pop-os.tailafd09f.ts.net)
       │  HTTPS, TLS terminated by Tailscale, forwards X-Forwarded-For
       ▼
127.0.0.1:8001   (this host, behind no extra proxy)
       │
       ▼
inst-ai-bot.service  (systemd user unit)
       │
       ▼
uvicorn server:app --workers 2  → FastAPI app (server.py)
       │
       ▼
MongoDB Atlas (creds in .env)
```

Alongside the FastAPI HTTP server, an MCP server runs on the same host. Its
current ingress paths converge on the same loopback service:

```
MCP clients
  ├─ public Tailscale Funnel /mcp
  ├─ tailnet-only Tailscale Serve :8443
  ├─ OpenAI-managed tunnel
  └─ localhost (direct HTTP)
       │
       ▼
127.0.0.1:8002
       │
       ▼
inst-ai-bot-mcp.service  (systemd user unit; see "MCP server lifecycle" below)
       │
       ▼
uvicorn (scripts/run_mcp.py → video_processor/mcp_server.py)
       │  in-process, same MongoDB connection
       ▼
MongoDB Atlas
```

The MCP server validates authentication for Streamable HTTP. It supports a
configured static bearer for developer clients and OAuth access tokens for
hosted clients. A separate single-user authorization server runs on loopback
port 8003, publishes discovery/JWKS, handles owner approval, and issues the
OAuth tokens. The FastAPI HTTP API stays in place for the Next.js log viewer
and any external callers using `X-API-Key`.

**Current MCP reachability**:

- `http://localhost:8002/mcp` — for Claude Code running on pop-os itself. Plain HTTP, never leaves the loopback.
- `https://pop-os.tailafd09f.ts.net:8443/mcp` — for Claude Code / Claude Desktop on any other tailnet machine. Claude Desktop's MCP client rejects plain `http://` for non-localhost URLs, so Serve provides HTTPS via the same Let's Encrypt cert Funnel uses (cached on the host, no fresh issuance per port).
- `https://pop-os.tailafd09f.ts.net/mcp` — exposed through the public Funnel and protected by OAuth or the migration bearer.
- The active `inst-ai-bot-tunnel.service` also provides an outbound OpenAI-managed tunnel to this MCP process.

The Next.js frontend is not part of the active runtime right now and should not be exposed. Its old user systemd unit (`inst-ai-bot-web.service`) has been stopped and disabled; do not re-enable it unless the frontend is intentionally brought back.

## Server lifecycle

The FastAPI server runs as the user-scope systemd unit `inst-ai-bot.service`:

- Unit file: `~/.config/systemd/user/inst-ai-bot.service`
- Working dir: `/home/artur/projects/inst-ai-bot`
- ExecStart: `venv/bin/uvicorn server:app --host 127.0.0.1 --port 8001 --workers 2`
- EnvironmentFile: `/home/artur/projects/inst-ai-bot/.env` (loaded fresh on start)
- `Restart=on-failure` with 5s backoff
- `WantedBy=default.target`, with `loginctl Linger=yes` → starts on boot without login

Operating it:

```bash
systemctl --user status inst-ai-bot         # current state
systemctl --user restart inst-ai-bot        # after code change OR .env edit
systemctl --user stop inst-ai-bot           # while debugging by hand
systemctl --user start inst-ai-bot
journalctl --user -u inst-ai-bot -f         # live logs
journalctl --user -u inst-ai-bot --since "10 min ago"
```

Do NOT start a manual `uvicorn ... --port 8001` alongside the unit — both will race for the port, and orphaned workers from a manual launch can outlive their master and squat the port, blocking systemd from binding.

## MCP server lifecycle

The MCP server runs as a second user-scope systemd unit, `inst-ai-bot-mcp.service`, on port 8002:

- Unit file: `~/.config/systemd/user/inst-ai-bot-mcp.service`
- Working dir: `/home/artur/projects/inst-ai-bot`
- ExecStart: `venv/bin/python scripts/run_mcp.py`
- EnvironmentFile: `/home/artur/projects/inst-ai-bot/.env`
- `Restart=on-failure` with 5s backoff
- `WantedBy=default.target`

Operating it mirrors the main unit:

```bash
systemctl --user status inst-ai-bot-mcp
systemctl --user restart inst-ai-bot-mcp     # after code change OR .env edit
journalctl --user -u inst-ai-bot-mcp -f
```

The unit file is not committed (lives in `~/.config/systemd/user/`). Bootstrap on a fresh host:

```ini
# ~/.config/systemd/user/inst-ai-bot-mcp.service
[Unit]
Description=inst-ai-bot MCP server
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/artur/projects/inst-ai-bot
EnvironmentFile=/home/artur/projects/inst-ai-bot/.env
ExecStart=/home/artur/projects/inst-ai-bot/venv/bin/python scripts/run_mcp.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

Then `systemctl --user daemon-reload && systemctl --user enable --now inst-ai-bot-mcp`.

## OAuth authorization server lifecycle

The single-user OAuth server runs as `inst-ai-bot-oauth.service` on loopback
port 8003:

- Source unit: `deploy/systemd/inst-ai-bot-oauth.service`
- Installed unit: `~/.config/systemd/user/inst-ai-bot-oauth.service`
- ExecStart: `venv/bin/python scripts/run_oauth.py`
- Persistent state: MongoDB collection `oauth_state`
- Private signing key: `secrets/oauth-signing-key.pem` (mode `0600`)
- Owner credential: `secrets/oauth-owner-password.txt` (mode `0600`); `.env`
  contains only its scrypt hash

Operate it with:

```bash
systemctl --user status inst-ai-bot-oauth
systemctl --user restart inst-ai-bot-oauth
journalctl --user -u inst-ai-bot-oauth -f
```

After an OAuth code change, restart this service. After changing issuer,
audience, scope, or JWKS configuration, restart both OAuth and MCP services.

## Tailscale exposure

The host runs parallel mappings under the same `*.ts.net` hostname, backed by
the same cached Let's Encrypt certificate:

| Mapping | Scope | Listen | Proxies to | How it was enabled |
|---|---|---|---|---|
| Funnel | Public internet | `:443 /` | `127.0.0.1:8001` (FastAPI) | Persisted Tailscale configuration |
| Funnel | Public internet | `:443 /mcp` | `127.0.0.1:8002` (MCP) | Persisted Tailscale configuration |
| Funnel | Public internet | `:443 /.well-known/oauth-protected-resource*` | `127.0.0.1:8002` (MCP metadata) | Persisted Tailscale configuration |
| Funnel | Public internet | `:443` OAuth paths | `127.0.0.1:8003` (OAuth) | Persisted Tailscale configuration |
| Serve | Tailnet only | `:8443` | `127.0.0.1:8002` (MCP) | `sudo tailscale serve --bg --https=8443 http://127.0.0.1:8002` |

Both persist across reboots (the `--bg` flag stores the config). Inspect with `tailscale funnel status` (shows both, since Serve is the underlying mechanism) or `tailscale serve status`.

Disable individually:
- `sudo tailscale funnel --https=443 off` — turns off public access to both FastAPI and the public `/mcp` mapping.
- `sudo tailscale serve --https=8443 off` — turns off tailnet HTTPS to MCP (tailnet machines can still reach plain `:8002` directly).

DNS: the hostname resolves publicly via Tailscale's `*.ts.net` zone (`209.177.145.97 / 209.177.145.192` are the edge IPs). Fresh hostnames can be negatively cached for up to ~30 min on third-party resolvers — if Claude Desktop reports "could not resolve host" right after enabling, that's the cause and it self-heals.

## Auth

The three HTTP processes have separate auth behavior:

- **FastAPI HTTP** (port 8001): every endpoint except `/health` requires
  `X-API-Key: <secret>`, using `INST_AI_BOT_API_KEY`.
- **MCP server** (port 8002): `MCP_AUTH_MODE` selects `bearer`, `oauth`,
  `oauth-and-bearer`, or explicit local-only `disabled-dev` mode.
- **OAuth server** (port 8003): one password-protected owner; supports Dynamic
  Client Registration, Authorization Code with PKCE/S256, RS256 JWT access
  tokens, rotating refresh tokens, and token-family revocation.

`bearer` is the default and fails closed at startup when
`INST_AI_BOT_API_KEY` is missing. `oauth` verifies the JWT signature, issuer,
audience, expiry, and `instagram-creator:use` scope against the configured
issuer and JWKS. `oauth-and-bearer` keeps the static bearer working during a
client migration. OAuth identities all map to the same configured creator.

The public HTTPS origin routes `/mcp`,
`/.well-known/oauth-protected-resource`, and
`/.well-known/oauth-protected-resource/mcp` to port 8002. Authorization metadata,
`/authorize`, `/token`, `/register`, `/revoke`, `/login`, `/jwks.json`, and
`/oauth/*` route to port 8003. Tailscale `--set-path` strips the public prefix;
the configured proxy targets therefore include the same backend path.

Skills never carry credentials. See [`docs/mcp-clients.md`](mcp-clients.md) for
all environment variables, provider requirements, and client setup.

To rotate only the MCP bearer: edit `.env`, restart
`inst-ai-bot-mcp.service`, and update developer-client connector stores. Restart
the FastAPI service too only when its `X-API-Key` use of the shared value must
change.

## Rate limiting

Sliding-window, per-IP, in-memory:

- Default 120 req / 60s / IP / worker (configurable via `INST_AI_BOT_RATE_LIMIT_PER_MIN`; `0` disables).
- Real client IP resolved from `X-Forwarded-For` first (Funnel sets this), else `request.client.host`.
- 429 + `Retry-After: <seconds>` when exceeded.
- Per-worker memory: with `--workers 2`, effective ceiling is ~2× the configured value for the same IP.
- Bucket dict grows unboundedly with unique IPs. Not an issue at current scale; if it ever is, add periodic cleanup.

## Environment & secrets

Server reads from `/home/artur/projects/inst-ai-bot/.env`. The full set of recognised variables is documented in `CLAUDE.md` under "Configuration". Notable ones beyond the standard ones in CLAUDE.md:

- `INST_AI_BOT_API_KEY` — shared secret for `X-API-Key` (see above).
- `INST_AI_BOT_RATE_LIMIT_PER_MIN` — rate-limit ceiling per worker.
- `OAUTH_ADMIN_PASSWORD_HASH` — scrypt hash of the single owner's password.
- `OAUTH_SIGNING_KEY_PATH` / `OAUTH_SIGNING_KEY_ID` — private RSA key path and
  public key identifier.
- `OAUTH_ACCESS_TOKEN_TTL` / `OAUTH_REFRESH_TOKEN_TTL` — token lifetimes in
  seconds.

Secrets are not committed; `.env` is gitignored. Skills no longer carry their own `.env` — auth lives in the per-host MCP connector config.

## Skills distribution

Two skills live in `skills/{adapt-reel,grill-reel}/`, markdown-only (no `scripts/`, no `.env`):

- For Claude Code in this repo: `.claude/skills/<name>` are symlinks to `skills/<name>` — discovered automatically.
- For external hosts (Claude Desktop, etc.): `./skills/package.sh --all` writes `skills/dist/<name>.zip` with just `SKILL.md` inside. Upload zip; the skill instructs Claude to call MCP tools `index_video_from_url` and `run_prompt`, which the host's MCP connector routes to `inst-ai-bot-mcp.service` with the bearer token attached by the host (not by the skill).

See `docs/mcp-clients.md` for Claude, ChatGPT/Codex, Hermes, bearer, and OAuth
setup.

## Other on-host services to be aware of

- `inst-ai-bot-web.service` — old user-scope systemd unit for the Next.js frontend on port 3001. Status: stopped and disabled; frontend is not currently used and should not be exposed.
- `/etc/systemd/system/myfastapi.service` — stale system-scope unit pointing at the unrelated checkout at `/home/artur/web/inst-ai-bot/` on port 8000. Disabled; safe to delete. Doesn't conflict with `:8001`.

## Public-traffic monitoring / emergency containment

Hermes cron job `inst-ai-bot Funnel traffic watchdog` (`a275216c92dc`) runs every minute via script `~/.hermes/profiles/artur/scripts/inst_ai_funnel_traffic_watchdog.py`.

Behavior:

- Reads `journalctl --user -u inst-ai-bot` uvicorn access logs over a 5-minute sliding window.
- Ignores `/health`.
- Treats common public-URL background noise as non-actionable: 404 `GET`/`HEAD` probes for `/`, `/favicon.ico`, `/robots.txt`, WordPress/XML-RPC paths, etc.
- Alerts on suspicious non-noise traffic immediately, with a 30-minute cooldown.
- Alerts on background-noise volume only when it reaches `>=25` probes / 5 min.
- Treats sustained suspicious traffic or an extreme flood as an incident and immediately runs `systemctl --user stop inst-ai-bot`.
- Also attempts `tailscale funnel --https=443 off`; if that requires privileges and fails, the backend is still stopped so the Funnel has no live app to reach.

Current mitigation thresholds:

- `>=100` total requests / 5 min, or
- `>=15` suspicious non-noise requests / 5 min, or
- `>=8` suspicious non-noise 404 requests / 5 min, or
- `>=8` distinct suspicious non-noise paths / 5 min, or
- `>=200` background-noise probes / 5 min.

Inspect/manage:

```bash
hermes cron list
journalctl --user -u inst-ai-bot --since "10 min ago"
systemctl --user status inst-ai-bot
tailscale funnel status
```

## What to do on common situations

| Situation | Action |
|---|---|
| Pulled a code change | `systemctl --user restart inst-ai-bot` |
| Edited `.env` | `systemctl --user restart inst-ai-bot` (env is read at start) |
| Server unreachable from outside | `systemctl --user status inst-ai-bot` first, then `tailscale funnel status`, then `journalctl --user -u inst-ai-bot -n 100` |
| MCP unreachable from a tailnet machine | `systemctl --user status inst-ai-bot-mcp`, then `tailscale serve status` (look for the `:8443` line), then `journalctl --user -u inst-ai-bot-mcp -n 100`. Confirm the calling machine is on the tailnet (`tailscale status` on that machine). |
| Watchdog stopped the backend | Inspect `journalctl --user -u inst-ai-bot --since "30 min ago"`, then either keep it down or restart with `systemctl --user start inst-ai-bot` |
| Want to test a change without affecting prod | Stop the unit (`systemctl --user stop inst-ai-bot`), run uvicorn manually on a different port, then start the unit again when done |
| Rotated the API key | Update `.env` (server only), restart both units (`systemctl --user restart inst-ai-bot inst-ai-bot-mcp`), update the bearer in each Claude host's MCP connector config |
| Rebooted the host | Server auto-starts; Funnel reattaches automatically; no action needed |
