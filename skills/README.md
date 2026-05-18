# Skills

Portable Claude skills that drive the `inst-ai-bot` HTTP API. Each folder follows Anthropic's [skill format](https://support.claude.com/en/articles/12512198-how-to-create-custom-skills): a `SKILL.md` with `name` + `description` frontmatter and a `scripts/` subdir.

All skills assume the local `inst-ai-bot` server is running at `http://localhost:8000`. Pass `--server` to point at a different host.

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

Package the skill as a zip and upload via the skills UI / API:

```bash
./skills/package.sh adapt-reel       # writes dist/adapt-reel.zip
./skills/package.sh --all            # packages every skill
```

The zip contains the skill folder at its root, as Anthropic requires.

### Hermes (custom agent)

TBD — installer will land once the tool-registration interface is settled.

## Adding a new skill

1. Create `skills/<your-skill>/SKILL.md` with `name` + `description` (≤200 chars) frontmatter.
2. Put any runnables in `skills/<your-skill>/scripts/`.
3. Reference scripts with paths relative to the skill folder (`scripts/foo.py`), not absolute paths.
4. Symlink into `.claude/skills/` if you want it active in this repo: `ln -s ../../skills/<your-skill> .claude/skills/<your-skill>`.
