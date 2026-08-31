# Reusable Instagram creator skills

These markdown-only skills add repeatable workflows on top of the
`inst-ai-bot` MCP server. The MCP server exposes one creator configured by the
server. Every connected client sees that same creator and the same data.

| Skill | Workflow |
|---|---|
| [`adapt-reel/`](adapt-reel/) | Turn a source Reel's structure into creator-specific concepts and a shoot-ready script. |
| [`grill-reel/`](grill-reel/) | Critique a Reel's hook, pacing, audience response, and next edits. |

The connection and the instructions are separate:

- Configure the MCP URL and authentication in each client.
- Install or upload the skills only where you want the guided workflows.
- Never add a bearer token, OAuth token, or server credential to a skill.

Both skills first call `get_current_creator_profile`, then index the supplied
Reel, then call `run_prompt`. If the MCP tools are unavailable, they stop with a
clear setup error.

## Package for manual upload

```bash
./skills/package.sh --all
```

This creates `skills/dist/adapt-reel.zip` and
`skills/dist/grill-reel.zip`. Each archive contains only its skill markdown.
Upload the archives to Claude or another host that accepts skill bundles. The
repo-local Codex plugin already bundles both skills.

## Configure clients

Use [`docs/mcp-clients.md`](../docs/mcp-clients.md) for bearer and OAuth server
configuration, Claude, ChatGPT/Codex, and Hermes instructions, and the shared
smoke test.

## Add another workflow

1. Create `skills/<name>/SKILL.md` with `name` and `description` frontmatter.
2. Use only stable MCP tool names and load `get_current_creator_profile` first
   for creator-specific work.
3. State that the server exposes one configured creator.
4. Keep credentials, internal URLs, environment values, and lifecycle commands
   out of the skill.
5. Add the skill to `tests/test_skill_contract.py` and, if it belongs in the
   private Codex plugin, copy it into that plugin's `skills/` directory.
