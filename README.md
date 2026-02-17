# inst-ai

## Environment Setup

1. Copy the template:
   - `cp .env.example .env`
2. Fill required keys for backend multimodal flow:
   - `MONGODB_URI`
   - `MONGODB_DB`
   - `TWELVE_LABS_API_KEY`

Optional keys (for specific scripts/features): `OPENAI_API_KEY`, `TWELVE_LABS_INDEX_ID`, `FB_TOKEN`.

TwelveLabs SDK baseline:
- Python: `twelvelabs>=1.0.0,<2.0.0`
- Node: `twelvelabs-js@^1.1.0`

To disable multimodal analysis at server startup, set:
- `ENABLE_MULTIMODAL_ANALYSIS=false`
