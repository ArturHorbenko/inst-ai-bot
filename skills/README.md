# Reusable Instagram creator skills

These markdown-only skills add repeatable workflows on top of the
`inst-ai-bot` MCP server. The MCP server exposes one creator configured by the
server. Every connected client sees that same creator and the same data.

| Skill | Workflow |
|---|---|
| [`adapt-reel/`](adapt-reel/) | Fetch the current adaptation guide, then deliver creator-specific concepts and a shoot-ready script. |
| [`performance-audit/`](performance-audit/) | Fetch the current audit guide, then investigate stored performance and recommend experiments. |
| [`grill-reel/`](grill-reel/) | Critique a Reel's hook, pacing, audience response, and next edits. |

The connection and the instructions are separate:

- Configure the MCP URL and authentication in each client.
- Install or upload the skills only where you want the guided workflows.
- Never add a bearer token, OAuth token, or server credential to a skill.

Adaptation and audit skills are stable entry points: they call `get_workflow`
at the start of each new workflow execution and follow the returned instructions.
The full guides live in `video_processor/workflows/`, are read on every call,
and are not copied into skill packages. The existing `grill-reel` skill still
uses its bundled procedure. If the MCP tools are unavailable, the skills report
a setup error.

ChatGPT users who connect only the MCP URL can also use adaptation and audit:
the server instructions and `get_workflow` description route those requests to
the same guides. A skill package is optional for those clients. This routing
is model guidance, not enforced orchestration; test it in a new conversation.

## Package for manual upload

```bash
./skills/package.sh --all
```

This creates an archive in `skills/dist/` for each skill, including
`adapt-reel.zip`, `performance-audit.zip`, and `grill-reel.zip`.
Each archive contains only its skill markdown.
Upload the archives to Claude or another host that accepts skill bundles. The
repo-local Codex plugin already bundles all three skills.

## Update workflow instructions

Edit `video_processor/workflows/adapt-reel.md` or `performance-audit.md` and
deploy that file to the MCP server's checkout. Existing guides are loaded from
disk on every `get_workflow` call: no process restart, plugin upload, or connector
refresh is needed for guide-only changes. The returned `version` is a SHA-256
digest of the instructions, useful when checking which revision was fetched.
Publish complete files atomically to avoid readers seeing partial edits. A guide
already loaded in a running workflow is not retroactively replaced; the next
workflow execution must fetch it again.

The initial rollout requires deploying the new Python code, restarting the MCP
service, and refreshing ChatGPT's connection to discover `get_workflow` and the
new server instructions. Existing plugin users should install the updated skill
entry points once. Later changes to those entry points still require a package
update; changes to the MCP tool schema or routing still require code deployment,
restart, and connector refresh.

## Configure clients

Use [`docs/mcp-clients.md`](../docs/mcp-clients.md) for bearer and OAuth server
configuration, Claude, ChatGPT/Codex, and Hermes instructions, and the shared
smoke test.

## Add another workflow

1. Add a guide to `video_processor/workflows/` and register its name in
   `WorkflowName` and `WORKFLOW_FILES` in `video_processor/workflow_guides.py`.
2. Add a concise trigger to the MCP server instructions and tool description.
3. Add a stable `skills/<name>/SKILL.md` entry point that fetches the guide, and
   copy it into the private plugin's `skills/` directory if wanted there.
4. Keep credentials and lifecycle commands out of guides and skills. Load creator
   context when the workflow needs personalization; preserve the user's scope.
5. Run the workflow, MCP, and skill contract tests. Deploy the code and guide,
   restart the MCP service, and refresh clients to expose the added workflow name.
