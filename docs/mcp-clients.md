# MCP client setup (single creator)

This MCP server supports many simultaneous clients, but exactly one creator.
The creator is selected by the server's existing configuration; there is no
creator ID, tenant ID, or account selector in MCP requests. Every authorized
client sees the same creator, analytics, indexed videos, and prompt runs.

## 1. Server authentication

Set these values in the server `.env`. Restart `inst-ai-bot-mcp.service`; when
using the bundled issuer, restart `inst-ai-bot-oauth.service` too.

| `MCP_AUTH_MODE` | Use |
|---|---|
| `bearer` | Default. Local and developer clients that can send a custom `Authorization` header. Requires `INST_AI_BOT_API_KEY`. |
| `oauth` | Public hosted clients. Requires an OAuth issuer, JWKS, resource URL, audience, and scope. |
| `oauth-and-bearer` | Migration mode. Accepts valid OAuth tokens and the existing bearer key. |
| `disabled-dev` | Explicit local development only. Never use on a public route. |

Bearer configuration:

```dotenv
MCP_AUTH_MODE=bearer
INST_AI_BOT_API_KEY=<random-secret>
```

OAuth resource-server configuration:

```dotenv
MCP_AUTH_MODE=oauth
MCP_RESOURCE_URL=https://mcp.example.com/mcp
MCP_OAUTH_ISSUER_URL=https://auth.example.com/
MCP_OAUTH_JWKS_URL=https://auth.example.com/jwks.json
MCP_OAUTH_AUDIENCE=https://mcp.example.com/mcp
MCP_OAUTH_SCOPE=instagram-creator:use
MCP_OAUTH_ALGORITHMS=RS256
```

This repository includes a deliberately small, single-user OAuth 2.1
authorization server in `video_processor/oauth_server.py`. It uses the MCP
SDK's authorization, token, registration, revocation, PKCE, and metadata
handlers; MongoDB persists clients and grants, and a local RSA key signs
resource-bound access tokens. OAuth only decides who may use this private
server; it does not choose a creator.

Generate the private key and owner password once:

```bash
venv/bin/python scripts/setup_oauth_secrets.py --generate-password --env-file .env
```

The password is stored at `secrets/oauth-owner-password.txt` with mode `0600`;
only its scrypt hash is stored in `.env`. Both `secrets/` and `.env` are
gitignored. Install and start the service using
`deploy/systemd/inst-ai-bot-oauth.service`.

The MCP resource server publishes both discovery forms:

```text
https://mcp.example.com/.well-known/oauth-protected-resource
https://mcp.example.com/.well-known/oauth-protected-resource/mcp
```

Route both discovery paths and `/mcp` to the MCP process. Do not route them to
the dashboard process.

Route these authorization-server paths to port 8003 while preserving each
backend path: `/.well-known/oauth-authorization-server`, `/authorize`, `/token`,
`/register`, `/revoke`, `/login`, `/jwks.json`, and `/oauth`.

## 2. Direct clients with a bearer

Keep secrets in each client's secret/config store. Replace the URL with the
reachable HTTPS endpoint, or use `http://localhost:8002/mcp` on the server host.

### Claude Code

```bash
claude mcp add inst-ai-bot \
  --transport http \
  https://mcp.example.com/mcp \
  --header "Authorization: Bearer <secret>"
```

Install the skills by copying or linking `skills/adapt-reel`,
`skills/performance-audit`, and `skills/grill-reel` into the Claude skills directory.

### Claude Desktop local MCP configuration

```json
{
  "mcpServers": {
    "inst-ai-bot": {
      "type": "http",
      "url": "https://mcp.example.com/mcp",
      "headers": {
        "Authorization": "Bearer <secret>"
      }
    }
  }
}
```

Fully restart Desktop after changing its local configuration. This local
mechanism is distinct from a Claude account-level remote connector.

### Hermes with bearer

Put the token in `~/.hermes/.env` as `INSTAGRAM_CREATOR_MCP_TOKEN`, then use
runtime substitution in `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  instagram_creator:
    url: "https://mcp.example.com/mcp"
    headers:
      Authorization: "Bearer ${INSTAGRAM_CREATOR_MCP_TOKEN}"
```

## 3. Hosted clients with OAuth

The URL must be publicly reachable over HTTPS. A tailnet-only address is not
enough for hosted ChatGPT or Claude.

### ChatGPT and Codex plugin

1. Enable Developer mode in ChatGPT under Settings → Security and login.
2. Open ChatGPT Plugins, add a connection, and enter the full `/mcp` URL.
3. Do not enter a client ID or secret; the server supports Dynamic Client
   Registration.
4. Select Connect. At the `Authorize MCP access` page, enter the owner password
   from `secrets/oauth-owner-password.txt` and select Approve.
5. Confirm all ten tools are discovered, including `get_workflow`. The server
   instructions route adaptation and performance audit requests to that tool;
   connecting the MCP URL is sufficient to fetch those guides. Continue below
   only if you also want the packaged skill entry points.
6. Copy the connection's technical ID from the browser URL. It starts with
   `plugin_asdk_app`.
7. Add `.app.json` to the repo-local `instagram-creator` plugin with that real
   ID, then add `"apps": "./.app.json"` to its `plugin.json`.
8. Validate and install the plugin from the repo marketplace. Start a new
   conversation after installation.

The repository does not commit a fake `.app.json`: that file cannot be valid
until ChatGPT creates the private connection ID. No public submission is
needed. Codex may alternatively connect directly to the MCP URL using its MCP
configuration and use the repo skills without an app binding.

### Claude web / account-level connector

1. Open Customize → Connectors → Add custom connector. Team and Enterprise
   plans require an owner to add it in organization settings.
2. Enter the public `/mcp` URL. Provide a pre-registered OAuth client ID and
   secret in Advanced settings only if the OAuth provider requires them.
3. Connect and authorize, then enable the connector for the conversation.
4. Upload the skill archives produced by `./skills/package.sh --all` when the
   guided workflows are wanted.

Claude's remote connection originates from Anthropic infrastructure, including
when configured through the account in Claude Desktop. Only the separate local
Desktop MCP configuration uses the machine's own network.

### Hermes with OAuth

```bash
hermes mcp add --url https://mcp.example.com/mcp --auth oauth instagram-creator
hermes mcp test instagram-creator
```

Equivalent `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  instagram-creator:
    url: "https://mcp.example.com/mcp"
    auth: oauth
```

Hermes performs discovery and opens a browser for the PKCE flow. The OAuth
provider must support client metadata documents or Dynamic Client Registration.

## 4. Shared smoke test

Before testing a hosted client, exercise the deployed protocol without printing
credentials:

```bash
venv/bin/python scripts/smoke_test_oauth.py \
  --origin https://mcp.example.com
```

Expected: `registration=ok authorization=ok token=ok mcp_initialize=ok revocation=ok`.

Run these in every connected client:

1. “Summarize my creator profile from the last 60 days.”
2. “List my five most recent Reels and Feed posts.”
3. “Show analytics for this media ID.”
4. “Find indexed videos where I use a problem-solution hook.”
5. “Index this Instagram Reel and analyze its hook.”
6. Supply an invalid Instagram URL and confirm the error is understandable.
7. Ask an unrelated question and confirm the MCP is not called.

Also test “Adapt this Reel to my niche: <Instagram Reel URL>” and “Audit my
Instagram performance.” Confirm the first tool is `get_workflow` with
`adapt-reel` or `performance-audit`, respectively, followed by the guide's
profile and evidence calls. Repeat a new audit in the same chat and confirm it
fetches the guide again. Simple data lookups should call their tools directly.
Every client must return data for the same configured creator.

### Workflow deployment

For the first `get_workflow` rollout, deploy the MCP code and
`video_processor/workflows/` directory together, restart the MCP service, and
refresh the ChatGPT connection's tools and instructions. Test in a new chat.
Existing plugin installations need the updated entry points once; a bare
ChatGPT connector needs no plugin upload.

For later edits to an existing guide, deploy just the Markdown file to the
server's checkout (prefer an atomic file replacement). `get_workflow` reads it
on each call, with a content-derived `version`; no service restart or connection
refresh is needed. Already-running workflows retain the guide they fetched.
Adding workflow names or changing the tool schema/routing requires a code
deployment, restart, and connection refresh. See `skills/README.md` for packaging.

## References

- [OpenAI MCP authentication](https://developers.openai.com/plugins/build/auth)
- [OpenAI private plugin packaging](https://developers.openai.com/plugins/build/plugins)
- [OpenAI connect and test](https://developers.openai.com/plugins/deploy/connect-chatgpt)
- [Claude remote custom connectors](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)
- [Hermes MCP configuration](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/mcp.md)
