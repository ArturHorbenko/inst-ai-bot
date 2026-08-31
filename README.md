# inst-ai

## Environment Setup

1. Copy the template:
   - `cp .env.example .env`
2. Fill required keys for backend multimodal flow:
   - `MONGODB_URI`
   - `MONGODB_DB`
   - `TWELVE_LABS_API_KEY`

Optional keys (for specific scripts/features): `OPENAI_API_KEY`, `TWELVE_LABS_INDEX_ID`, `FB_TOKEN`.

To enable the MCP analytics-read tools, set `ANALYTICS_DASHBOARD_URL` to the
dashboard base URL and `ANALYTICS_DASHBOARD_API_KEY` to its `MCP_READ_SECRET`.
These tools only read stored dashboard data; they cannot request fresh Meta
analytics or start indexing/model work.

The MCP analytics surface provides `list_recent_content`,
`get_content_analytics`, and `content_audit(days=N)` for stored Reels and Feed
posts. Trial Reels are the only content filtered from these tools.

TwelveLabs SDK baseline:
- Python: `twelvelabs>=1.0.0,<2.0.0`
- Node: `twelvelabs-js@^1.1.0`

To disable multimodal analysis at server startup, set:
- `ENABLE_MULTIMODAL_ANALYSIS=false`

## Runs API

Artifact runs retain their existing contract: send `artifact`, `prompt`, and
optionally `model`, `label`, and `metadata` to `POST /runs`.

Text-only runs are available for requests such as dashboard comment sentiment
that do not have video bytes. Use `POST /runs/text`, or `POST /runs` without
an `artifact` field for compatibility with existing dashboard callers:

```json
{
  "prompt": "Return JSON only: {\"comment\": \"Great post\"}",
  "model": "google/gemini-3.5-flash",
  "label": "comment-sentiment/v1",
  "metadata": {"source": "dashboard"}
}
```

Text-only prompts may be up to 48,000 characters and must use a
`google/gemini-3.5-flash`-compatible model ID. The prompt is passed to Gemini
unchanged, so JSON instructions and JSON payloads are supported. Text-only
runs are stored with `input_type: "text"` and explicit text-only provenance;
they have no Artifact. Text-only Runs never persist or return the raw prompt:
they store `prompt_sha256` (a SHA-256 digest) and `prompt_length` instead.
Prompts are not written to application logs. Artifact Runs retain their
existing prompt persistence behavior. All run endpoints use the existing
`X-API-Key` authentication when it is configured.
