# Minimal Single-Creator, Multi-Client MCP Plan

**Status:** Implemented and deployed; per-client connection remains manual

The MCP contract, fail-closed bearer mode, OAuth resource-server/JWT support,
single-user OAuth authorization server, tool metadata, reusable skills, client
documentation, public discovery routes, and repo-local private plugin scaffold
are implemented. The live public flow has passed Dynamic Client Registration,
PKCE approval, token exchange, MCP initialization, and revocation. Each hosted
client still requires a manual private connection; no directory publication is
required.

**Goal:** Make the existing single-creator Instagram analytics and video-analysis tools connectable to ChatGPT, Claude, Codex, Hermes, and other MCP clients, with reusable instructions layered on top.

## Scope

This phase intentionally supports exactly one configured creator.

- The Instagram account may remain hardcoded/configured through the existing environment variables.
- All connected clients see the same creator, artifacts, analytics, and runs.
- No tenant, organization, workspace, or per-creator database partitioning is required.
- No dashboard rewrite is required.
- No public plugin-directory submission is required.
- Manual/private installation is sufficient.

The only identity question is whether a caller is allowed to use this private MCP server. OAuth does not imply multi-tenancy; for now, every authorized identity maps to the same creator.

## Architecture

Keep one external MCP server in `inst-ai-bot`. Keep the analytics dashboard behind it as an internal service.

```text
ChatGPT / Claude / Codex / Hermes
                 |
       MCP over Streamable HTTP
                 |
       inst-ai-bot MCP server
          /             \
 video-analysis core   dashboard JSON API
          |                  |
     creator-kb DB     single creator analytics DB
```

Clients should configure only one MCP server. The dashboard routes under `/api/internal/mcp/*` remain private JSON endpoints; they do not need to become a second MCP server.

## What already exists

- `video_processor/mcp_server.py` is a Streamable HTTP MCP server.
- It already exposes the required video, retrieval, creator-profile, and analytics tools.
- `video_processor/dashboard_analytics.py` already reads the dashboard's protected analytics routes.
- The server already has cross-tool instructions.
- `skills/adapt-reel` and `skills/grill-reel` already contain reusable workflow instructions.
- Bearer authentication already works for clients that support manually configured headers.
- Public Tailscale Funnel and an OpenAI-managed tunnel are already documented.

This is an integration and packaging task, not a new MCP implementation.

## Minimum definition of done

The work is complete when:

- The existing MCP URL can be added to Claude Code/Desktop, Codex, and Hermes.
- The MCP can be added privately to hosted ChatGPT and Claude web.
- Authentication works without putting a server secret inside a skill or instruction bundle.
- Existing MCP tools remain backward compatible.
- Every tool advertises a title and correct read/write safety annotations.
- The two reusable workflows can be installed manually on ChatGPT and Claude.
- A short smoke-test checklist passes on each target client.

---

## Step 1: Freeze the current MCP tool contract

**Objective:** Avoid breaking the tools that existing skills already call.

**Files:**

- Create: `tests/test_mcp_contract.py`
- Modify only if needed: `video_processor/mcp_server.py`

**Required checks:**

- These current tools remain available:
  - `index_video_from_url`
  - `run_prompt`
  - `get_artifact`
  - `search_videos`
  - `get_video_context`
  - `get_current_creator_profile`
  - `list_recent_reels`
  - `get_reel_analytics`
  - `content_audit`
- Tool input schemas still contain the parameters used by the existing skills.
- The server still uses Streamable HTTP at `/mcp`.
- Server instructions still tell creator-specific workflows to load the creator profile first.

**Do not do in this step:** Rename tools, redesign payloads, add jobs, or change the single-creator model.

**Verification:**

```bash
venv/bin/python -m pytest tests/test_mcp_contract.py -q
```

## Step 2: Fix the current authentication test hang

**Objective:** Make the auth surface reliably testable before extending it.

The current `tests/test_mcp_server_auth.py` hangs on its first well-known-path request with the installed MCP SDK. Resolve this first so later OAuth tests are trustworthy.

**Files:**

- Modify: `tests/test_mcp_server_auth.py`
- Modify if required: `video_processor/mcp_server.py`

**Steps:**

1. Reproduce the hang with a five-second test timeout.
2. Check the interaction between MCP app lifespan handling and `BaseHTTPMiddleware`.
3. If middleware is the cause, replace it with a small pure ASGI auth middleware.
4. Confirm unauthenticated and invalid-token requests finish promptly.

**Verification:**

```bash
venv/bin/python -m pytest tests/test_mcp_server_auth.py -q
```

Expected: all tests pass in a few seconds.

## Step 3: Improve only the MCP metadata clients need

**Objective:** Make tool discovery clearer and safer across different hosts without changing tool behavior.

**Files:**

- Modify: `video_processor/mcp_server.py`
- Modify: `tests/test_mcp_contract.py`

**Add to every tool:**

- A short human-readable `title`
- Accurate MCP annotations:
  - `readOnlyHint`
  - `destructiveHint`
  - `idempotentHint`
  - `openWorldHint`

**Suggested annotations:**

| Tools | Metadata |
|---|---|
| Profile, analytics, audit, artifact, search, and context reads | read-only, non-destructive |
| `index_video_from_url` | creates state, non-destructive, idempotent, open-world |
| `run_prompt` | creates a paid run, non-destructive, non-idempotent, open-world |

Keep the important shared instruction within the first 512 characters:

> For creator-specific work, call `get_current_creator_profile` first. All tools operate on the one creator configured by the server.

Explicit output models are useful but are not a blocker for the first multi-client connection. Add them only where the current SDK/client rejects a schema or where doing so is a small change.

## Step 4: Preserve bearer auth for local/configurable clients

**Objective:** Keep the shortest existing setup working.

The current bearer token is sufficient for:

- Claude Code
- Claude Desktop local MCP configuration
- Codex CLI/Desktop
- Hermes
- Direct MCP/API clients that support custom headers

**Minimum changes:**

- Keep `Authorization: Bearer <token>` support.
- Never put the token in `SKILL.md`, plugin files, examples committed with real values, or chat messages.
- Change production behavior so a missing `INST_AI_BOT_API_KEY` does not silently make the server anonymous.
- Document one explicit development-only way to disable auth if it is still needed.

**Files:**

- Modify: `video_processor/mcp_server.py`
- Modify: `video_processor/config.py`
- Modify: `.env.example`
- Modify: `skills/README.md`
- Add/update tests in: `tests/test_mcp_server_auth.py`

**Acceptance checks:**

- Missing bearer returns 401.
- Wrong bearer returns 401.
- Correct bearer can initialize MCP and list tools.
- Starting the public server without an auth configuration fails clearly.

## Step 5: Add the minimum OAuth layer for hosted ChatGPT and Claude

**Objective:** Let hosted clients connect without supporting arbitrary custom bearer headers.

Hosted ChatGPT cannot present a custom API key to an authenticated MCP server, and hosted Claude custom connectors normally use OAuth. Therefore, the minimum common hosted-client path is OAuth 2.1.

This OAuth layer authorizes access to the one hardcoded creator. It does not select a creator or create tenant boundaries.

### Recommended approach

Use a maintained OAuth provider or a small standards-compliant OAuth gateway in front of the MCP server. Do not build a general identity platform inside either repository.

It must provide:

- Authorization Code flow with PKCE
- Authorization-server or OpenID discovery
- Dynamic Client Registration and/or the client-registration mechanism required by the chosen hosts
- A `resource`/audience bound to this MCP server
- Signed access tokens verifiable through JWKS
- Refresh and revocation behavior

The MCP resource server must provide:

- `GET /.well-known/oauth-protected-resource`
- A `WWW-Authenticate` challenge pointing at that metadata
- Validation of token signature, issuer, audience/resource, and expiry
- One simple scope such as `instagram-creator:use`

All valid tokens with that scope access the same configured creator.

**Suggested files:**

- Create: `video_processor/mcp_auth.py`
- Modify: `video_processor/mcp_server.py`
- Modify: `video_processor/config.py`
- Modify: `.env.example`
- Modify: `tests/test_mcp_server_auth.py`

**Minimum tests:**

- Protected-resource metadata is valid.
- Unauthenticated `/mcp` returns a discoverable challenge.
- Invalid signature, issuer, audience, or expiry is rejected.
- A valid token can initialize and call one read tool.
- OAuth and the existing development bearer mode can coexist during migration.

Do not add per-tool scopes, organizations, user provisioning, or tenant mapping in this phase.

## Step 6: Give the MCP endpoint a usable HTTPS route

**Objective:** Make `/mcp` and OAuth discovery reachable from hosted clients.

Prefer a dedicated stable origin:

```text
https://mcp.example.com/mcp
https://mcp.example.com/.well-known/oauth-protected-resource
```

If the existing Tailscale Funnel hostname is retained, update routing so both `/mcp` and the required `/.well-known/...` path reach the MCP/auth layer. The current documented `/mcp` path route alone is not enough for root-level OAuth discovery.

**Files:**

- Modify: `docs/infrastructure.md`
- Commit proxy/service configuration under `deploy/` when practical

**Acceptance checks:**

```bash
curl -i https://<mcp-host>/.well-known/oauth-protected-resource
curl -i https://<mcp-host>/mcp
```

Expected:

- Metadata request returns JSON describing the resource and authorization server.
- Unauthenticated MCP request returns 401 with a useful `WWW-Authenticate` challenge.
- No dashboard, MongoDB, model-provider, or Meta credentials are exposed.

## Step 7: Prepare reusable instructions

**Objective:** Keep MCP as the shared tool layer and install workflow instructions separately on top.

Start with the existing workflows:

- `skills/adapt-reel/SKILL.md`
- `skills/grill-reel/SKILL.md`

Review each skill so it:

- Refers only to stable MCP tool names.
- Calls `get_current_creator_profile` first for creator-specific work.
- States that the server has exactly one configured creator.
- Handles an indexing operation before calling `run_prompt` when necessary.
- Contains no secrets, internal hostnames, local filesystem paths, or environment values.
- Produces a useful fallback message when a tool is unavailable.

Do not duplicate large prompt templates inside the MCP server. Keep reusable workflows in skill files and keep tool descriptions focused on tool selection.

## Step 8: Package privately for ChatGPT/Codex

**Objective:** Install the MCP connection and workflows without publishing them.

**Suggested structure:**

```text
integrations/openai/instagram-creator/
  .codex-plugin/plugin.json
  .app.json
  skills/
    adapt-reel/SKILL.md
    grill-reel/SKILL.md
```

**Steps:**

1. Enable ChatGPT Developer mode.
2. Register the remote MCP URL and complete OAuth.
3. Copy the generated `plugin_asdk_app...` connection ID.
4. Create `.app.json` pointing to that connection.
5. Create `.codex-plugin/plugin.json`.
6. Copy/adapt the two reusable skills into the plugin.
7. Add a personal or repository-local marketplace entry.
8. Install from the private source and test in a new conversation.

No public submission is needed.

Codex CLI/Desktop can also connect directly to the same MCP URL, using OAuth or the configured development bearer token.

## Step 9: Package privately for Claude

**Objective:** Connect the same remote MCP server and install the same workflow intent in Claude.

### Claude web

1. Add a custom connector using the public MCP URL.
2. Complete OAuth.
3. Upload the skill ZIP files separately if the workflows are wanted.
4. Enable the connector in a new conversation.

### Claude Code/Desktop development setup

Retain direct HTTP configuration with a bearer header while OAuth is being introduced. Document OAuth as the preferred remote setup once it is stable.

Update:

- `skills/README.md`
- `skills/package.sh` only if packaging needs change
- Each skill only when required for cross-client wording

## Step 10: Add Hermes setup instructions

Hermes can use the same remote endpoint directly.

OAuth configuration:

```yaml
mcp_servers:
  instagram_creator:
    url: "https://mcp.example.com/mcp"
    auth: oauth
```

During migration, the existing bearer can be configured through a secret environment value and an `Authorization` header instead.

Verification:

```bash
hermes mcp test instagram_creator
```

There is no need to submit the integration to the Hermes MCP catalog.

## Step 11: Run a small shared smoke test

Run these prompts in ChatGPT, Claude, Codex, and Hermes:

1. "Summarize my creator profile from the last 60 days."
2. "List my five most recent Reels."
3. "Show analytics for this media ID."
4. "Find indexed videos where I use a problem-solution hook."
5. "Index this Instagram Reel and analyze its hook."
6. Supply an invalid Instagram URL and confirm the error is understandable.
7. Ask an unrelated question and confirm the MCP is not called unnecessarily.

For each client, record:

- Whether connection/authentication succeeded
- Tools discovered
- Tool selected for each prompt
- Whether the creator profile was loaded first
- Result and error quality
- Any client-specific setup difference

## Verification commands

### `instagram-analytics-dashboard`

No dashboard feature change is expected. Run its focused contract tests if routes or auth are touched:

```bash
pnpm vitest run tests/mcp-reels-route.test.ts tests/mcp-profile-route.test.ts
pnpm lint
pnpm typecheck
```

### `inst-ai-bot`

```bash
venv/bin/python -m pytest tests/test_mcp_contract.py -q
venv/bin/python -m pytest tests/test_mcp_server_auth.py -q
venv/bin/python -m pytest tests/test_dashboard_analytics.py tests/test_mcp_creator_profile.py -q
```

Before rollout:

```bash
venv/bin/python -m pytest tests -q
```

Also inspect the deployed endpoint with MCP Inspector:

```bash
npx @modelcontextprotocol/inspector@latest
```

## Recommended implementation order

1. Freeze current tool contract.
2. Fix the hanging auth test.
3. Add titles and safety annotations.
4. Make missing production auth fail closed.
5. Select/configure the smallest suitable OAuth provider or gateway.
6. Add protected-resource metadata and token validation.
7. Route `/mcp` and `/.well-known/...` through the public HTTPS origin.
8. Verify hosted ChatGPT and Claude connections.
9. Update and package the two reusable skills.
10. Add Codex and Hermes instructions.
11. Run the shared smoke tests.
12. Retire the single legacy shared bearer after all desired clients use OAuth, or retain it only for clearly scoped local development.

## Explicitly deferred work

Do not include these in the minimum implementation:

- Multiple creators or tenants
- Creator selection in MCP tool arguments
- Tenant IDs on database records
- Per-user analytics partitions
- Job queues or worker redesign
- Renaming existing MCP tools
- Redesigning all output payloads
- Per-tool OAuth scopes
- MCP UI components
- Public plugin or connector publication
- Moving dashboard logic into `inst-ai-bot`
- Supporting non-Instagram downloader hosts

Revisit long-running job handling, quotas, detailed audit logging, and stronger typed output schemas only if real client testing shows they are necessary.

## Primary references

- [OpenAI: Build an MCP server](https://developers.openai.com/plugins/build/mcp-server)
- [OpenAI: MCP authentication](https://developers.openai.com/plugins/build/auth)
- [OpenAI: Package a private plugin](https://developers.openai.com/plugins/build/plugins)
- [OpenAI: Connect and test a plugin](https://developers.openai.com/plugins/deploy/connect-chatgpt)
- [OpenAI: Codex and ChatGPT Desktop MCP](https://learn.chatgpt.com/docs/extend/mcp)
- [Claude: Custom connectors using remote MCP](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)
- [Hermes: MCP configuration and remote servers](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/mcp.md)
