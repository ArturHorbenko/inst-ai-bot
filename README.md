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

The MCP analytics surface provides `list_recent_reels`, `get_reel_analytics`,
and `content_audit(days=N)` for a 1–365 day stored-data review.

TwelveLabs SDK baseline:
- Python: `twelvelabs>=1.0.0,<2.0.0`
- Node: `twelvelabs-js@^1.1.0`

To disable multimodal analysis at server startup, set:
- `ENABLE_MULTIMODAL_ANALYSIS=false`
