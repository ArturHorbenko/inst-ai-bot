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

Alongside the FastAPI HTTP server, an MCP server runs on the same host. Two reachable URLs:

```
                                              tailnet (no public exposure)
                                                       │
Tailscale Serve  (https://pop-os.tailafd09f.ts.net:8443)
       │  HTTPS, TLS terminated by Tailscale, Let's Encrypt cert
       ▼
127.0.0.1:8002  ◄────────────────────  Claude Code on pop-os (direct, plain HTTP)
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

The MCP server is the auth surface that skills call (Streamable HTTP, `Authorization: Bearer <INST_AI_BOT_API_KEY>`). The FastAPI HTTP API stays in place for the Next.js log viewer and any external callers using `X-API-Key`.

**Two-port reachability** (intentional split):

- `http://localhost:8002/mcp` — for Claude Code running on pop-os itself. Plain HTTP, never leaves the loopback.
- `https://pop-os.tailafd09f.ts.net:8443/mcp` — for Claude Code / Claude Desktop on any other tailnet machine. Claude Desktop's MCP client rejects plain `http://` for non-localhost URLs, so Serve provides HTTPS via the same Let's Encrypt cert Funnel uses (cached on the host, no fresh issuance per port).
- No public funnel for MCP — that's the rule, MCP traffic stays on the tailnet.

The Next.js frontend is not part of the active runtime right now and should not be exposed. Its old user systemd unit (`inst-ai-bot-web.service`) has been stopped and disabled; do not re-enable it unless the frontend is intentionally brought back.

## Server lifecycle

The FastAPI server runs as the user-scope systemd unit `inst-ai-bot.service`:

- Unit file: `~/.config/systemd/user/inst-ai-bot.service`
- Working dir: `/home/artur/projects/inst-ai-bot`
- ExecStart: `venv/bin/uvicorn server:app --host 0.0.0.0 --port 8001 --workers 2`
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

## Tailscale exposure

The host runs two parallel mappings under the same `*.ts.net` hostname, both backed by the same cached Let's Encrypt cert:

| Mapping | Scope | Listen | Proxies to | How it was enabled |
|---|---|---|---|---|
| Funnel | Public internet | `:443` | `127.0.0.1:8001` (FastAPI) | `sudo tailscale funnel --bg 8001` |
| Serve | Tailnet only | `:8443` | `127.0.0.1:8002` (MCP) | `sudo tailscale serve --bg --https=8443 http://127.0.0.1:8002` |

Both persist across reboots (the `--bg` flag stores the config). Inspect with `tailscale funnel status` (shows both, since Serve is the underlying mechanism) or `tailscale serve status`.

Disable individually:
- `sudo tailscale funnel --https=443 off` — turns off public access to FastAPI.
- `sudo tailscale serve --https=8443 off` — turns off tailnet HTTPS to MCP (tailnet machines can still reach plain `:8002` directly).

DNS: the hostname resolves publicly via Tailscale's `*.ts.net` zone (`209.177.145.97 / 209.177.145.192` are the edge IPs). Fresh hostnames can be negatively cached for up to ~30 min on third-party resolvers — if Claude Desktop reports "could not resolve host" right after enabling, that's the cause and it self-heals.

## Auth

Two surfaces, same shared secret (`INST_AI_BOT_API_KEY` in `.env`):

- **FastAPI HTTP** (port 8001): every endpoint except `/health` requires header `X-API-Key: <secret>`. Constant-time compare in `require_api_key()` (`server.py`).
- **MCP server** (port 8002): every request requires `Authorization: Bearer <secret>`. Constant-time compare in `BearerAuthMiddleware` (`video_processor/mcp_server.py`).

If `INST_AI_BOT_API_KEY` is unset, auth is disabled on both surfaces and a warning is logged at startup.

Skills no longer carry the key — they call MCP tools through the host's MCP connector, which holds the bearer in its own config (not in the skill bundle).

To rotate: edit `.env` server-side, restart both units (`systemctl --user restart inst-ai-bot inst-ai-bot-mcp`), update the bearer in each Claude host's MCP connector config (Claude Code: `claude mcp remove inst-ai-bot && claude mcp add ...`; Claude Desktop: edit `claude_desktop_config.json`).

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

Secrets are not committed; `.env` is gitignored. Skills no longer carry their own `.env` — auth lives in the per-host MCP connector config.

## Skills distribution

Two skills live in `skills/{adapt-reel,grill-reel}/`, markdown-only (no `scripts/`, no `.env`):

- For Claude Code in this repo: `.claude/skills/<name>` are symlinks to `skills/<name>` — discovered automatically.
- For external hosts (Claude Desktop, etc.): `./skills/package.sh --all` writes `skills/dist/<name>.zip` with just `SKILL.md` inside. Upload zip; the skill instructs Claude to call MCP tools `index_video_from_url` and `run_prompt`, which the host's MCP connector routes to `inst-ai-bot-mcp.service` with the bearer token attached by the host (not by the skill).

See `skills/README.md` for the per-host MCP connector setup (Claude Code / Desktop / claude.ai).

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
