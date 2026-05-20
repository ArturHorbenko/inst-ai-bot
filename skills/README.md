# Skills

Portable Claude skills that drive the `inst-ai-bot` MCP server. Each folder follows Anthropic's [skill format](https://support.claude.com/en/articles/12512198-how-to-create-custom-skills): a `SKILL.md` with `name` + `description` frontmatter and nothing else — no bundled scripts, no `.env`, no secrets.

The skills are markdown-only. They instruct Claude to call MCP tools (`index_video_from_url`, `run_prompt`) exposed by the `inst-ai-bot` MCP server. Authentication lives in the host's MCP connector config, not in the skill bundle, so iterating on a skill never disturbs your credentials.

## Available skills

| Skill | What it does |
|---|---|
| [`adapt-reel/`](adapt-reel/) | Propose how to remix an Instagram reel into the user's niche. Outputs transferable structure + 2-3 concepts + a shot-by-shot script. |
| [`grill-reel/`](grill-reel/) | Opinionated creator feedback on an Instagram reel — hook, pacing, audience signal, improve list. |

## How auth works now

The MCP server reads `Authorization: Bearer <token>` and compares it (constant-time) to `INST_AI_BOT_API_KEY` from the server's `.env`. If `INST_AI_BOT_API_KEY` is unset, auth is disabled (mirrors the FastAPI server's behaviour).

You configure the bearer **once per host** in that host's MCP config. The skill itself has no auth code.

Generate a key (if you don't already have one in `.env`):

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

## Install

There are two reachable MCP URLs depending on where the client runs:

| Where the client runs | URL to use |
|---|---|
| Claude Code on `pop-os` (same host as the server) | `http://localhost:8002/mcp` |
| Claude Code or Claude Desktop on any other tailnet machine | `https://pop-os.tailafd09f.ts.net:8443/mcp` |

The second is HTTPS via Tailscale Serve — Claude Desktop's MCP client rejects plain `http://` for anything other than `localhost`, so Serve provides a real Let's Encrypt cert on the tailnet without exposing the server to the public internet.

### Claude Code (this repo, on pop-os)

Symlinks in `.claude/skills/<name>` point at `skills/<name>`, so any Claude Code session opened in this repo picks the skills up automatically.

Register the MCP server once:

```bash
claude mcp add inst-ai-bot \
  --transport http \
  http://localhost:8002/mcp \
  --header "Authorization: Bearer <key>"
```

### Claude Code (other tailnet machine)

Symlink (or copy) the skill folder into your global skills directory, then register against the HTTPS tailnet URL:

```bash
ln -s "$(pwd)/skills/adapt-reel" ~/.claude/skills/adapt-reel
ln -s "$(pwd)/skills/grill-reel"  ~/.claude/skills/grill-reel

claude mcp add inst-ai-bot \
  --transport http \
  https://pop-os.tailafd09f.ts.net:8443/mcp \
  --header "Authorization: Bearer <key>"
```

### Claude Desktop

1. Package each skill as a zip and import via Settings → Skills:

   ```bash
   ./skills/package.sh --all      # writes dist/<name>.zip
   ```

   The zip contains only `SKILL.md`. No secrets ride along.

2. Register the MCP server in `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or the equivalent Windows path:

   ```json
   {
     "mcpServers": {
       "inst-ai-bot": {
         "type": "http",
         "url": "https://pop-os.tailafd09f.ts.net:8443/mcp",
         "headers": { "Authorization": "Bearer <key>" }
       }
     }
   }
   ```

   Fully quit Claude Desktop (⌘Q on macOS, not just close the window) and reopen — Desktop only re-reads the config on a clean restart. The Mac must be on the same tailnet as `pop-os`.

### claude.ai (Pro/Max/Team/Enterprise)

Custom connectors in the claude.ai UI currently only support OAuth (client id / client secret) — there's no field for a `Authorization: Bearer` header or a custom `X-API-Key`. Until that gap closes, the options are:

- Run `mcp-remote` locally as a stdio→HTTP proxy that injects the bearer, and register *that* as the connector in claude.ai (only useful if you're on Desktop on the same machine — defeats the purpose for browser claude.ai).
- Add an OAuth layer to the MCP server (overkill for a personal tool).
- Use claude.ai only for non-MCP work; iterate on this skill in Claude Code or Desktop.

If you do want to upload the skill zips to claude.ai for the prompt content (without the MCP tools), the bundles are still safe to upload — they contain no secrets.

## Remote server via Tailscale

The MCP server is reachable on the tailnet over HTTPS at `https://pop-os.tailafd09f.ts.net:8443/mcp`. That mapping is provided by `tailscale serve` (tailnet-scoped, free, Let's Encrypt cert) — not Funnel. There's no public exposure for MCP and that's intentional; see `docs/infrastructure.md` for the topology and how to disable/rotate the mapping.

The plain `http://...:8002/mcp` port is also reachable on the tailnet for clients that don't enforce HTTPS (e.g. ad-hoc `curl` testing), but Claude Desktop requires the `:8443` HTTPS URL.

## Adding a new skill

1. Create `skills/<your-skill>/SKILL.md` with `name` + `description` (≤200 chars) frontmatter.
2. Write the SKILL.md body to instruct Claude on which MCP tools to call and in what order. Inline any prompt templates verbatim.
3. Symlink into `.claude/skills/` if you want it active in this repo: `ln -s ../../skills/<your-skill> .claude/skills/<your-skill>`.
4. Do **not** add `scripts/`, `.env`, or `.env.example` — the MCP server holds the secret, the skill stays markdown-only.
