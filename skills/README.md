# Skills

Portable Claude skills that drive the `inst-ai-bot` HTTP API. Each folder follows Anthropic's [skill format](https://support.claude.com/en/articles/12512198-how-to-create-custom-skills): a `SKILL.md` with `name` + `description` frontmatter and a `scripts/` subdir.

All skills talk to an `inst-ai-bot` server. The URL is resolved in this order:

1. `--server URL` CLI flag
2. `$INST_AI_BOT_URL` environment variable
3. `INST_AI_BOT_URL=...` in a `.env` file at the skill folder root (next to `SKILL.md`)
4. `http://localhost:8000` (default)

The server has no auth — only point skills at a URL that's protected at the network layer (localhost, Tailscale, VPN). Don't expose it to the public internet without first adding an auth layer.

## Available skills

| Skill | What it does |
|---|---|
| [`adapt-reel/`](adapt-reel/) | Propose how to remix an Instagram reel into the user's niche. Outputs transferable structure + 2-3 concepts + a shot-by-shot script. |
| [`grill-reel/`](grill-reel/) | Opinionated creator feedback on an Instagram reel — hook, pacing, audience signal, improve list. |

## Install

### Claude Code (this repo)

Already done — `.claude/skills/<name>` symlinks point at `skills/<name>`, so any Claude Code session opened in this repo picks them up automatically.

### Claude Code (anywhere)

Symlink (or copy) the skill folder into your global skills directory:

```bash
ln -s "$(pwd)/skills/adapt-reel" ~/.claude/skills/adapt-reel
```

### Claude Desktop / Claude API

The skill sandbox likely won't inherit env vars from your machine, so configure the server URL via a `.env` file inside the skill folder before zipping:

```bash
cp skills/adapt-reel/.env.example skills/adapt-reel/.env
# edit skills/adapt-reel/.env → INST_AI_BOT_URL=http://your-tailnet.ts.net:8000
./skills/package.sh adapt-reel       # writes dist/adapt-reel.zip with .env bundled
./skills/package.sh --all            # packages every skill
```

The zip contains the skill folder at its root (matching Anthropic's spec) and your `.env` rides along. `skills/*/.env` is gitignored so your Tailscale URL stays out of the repo.

### Hermes (custom agent)

TBD — installer will land once the tool-registration interface is settled.

## Remote server via Tailscale

To run the skills from a host that isn't the one running `inst-ai-bot` (e.g. Claude Desktop on your laptop, server on your home box):

1. Install Tailscale on both machines and put them on the same tailnet.
2. Find the server's MagicDNS hostname (e.g. `pi.tail-abc123.ts.net`) — `tailscale status` lists it.
3. Set `INST_AI_BOT_URL` in the environment where the skill runs:

   ```bash
   export INST_AI_BOT_URL="http://pi.tail-abc123.ts.net:8000"
   ```

   For Claude Desktop / GUI hosts, set the env var system-wide (macOS: `launchctl setenv`; Linux: in your shell rc or systemd user unit).

4. Optional: front the server with `tailscale serve` to get an HTTPS URL on 443 — easier for hosts that prefer `https://...`:

   ```bash
   tailscale serve --bg http://localhost:8000
   # → https://pi.tail-abc123.ts.net
   ```

Skills fall back to `http://localhost:8000` when `INST_AI_BOT_URL` is unset, so the same skill works locally and remotely with no code changes.

## Adding a new skill

1. Create `skills/<your-skill>/SKILL.md` with `name` + `description` (≤200 chars) frontmatter.
2. Put any runnables in `skills/<your-skill>/scripts/`.
3. Reference scripts with paths relative to the skill folder (`scripts/foo.py`), not absolute paths.
4. Symlink into `.claude/skills/` if you want it active in this repo: `ln -s ../../skills/<your-skill> .claude/skills/<your-skill>`.
