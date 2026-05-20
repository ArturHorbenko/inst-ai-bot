# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A compute primitive for video analysis. Index videos once (download + Whisper transcription), then run opaque prompts against them via multimodal models. Agent workflows live in separate skill files — this repo is only the HTTP API.

See `CONTEXT.md` for domain glossary (Artifact, Run, Source, Content hash). See `docs/adr/` for architecture decisions. See `docs/infrastructure.md` for how the server actually runs on the host (systemd unit, Tailscale Funnel, auth, rate limit) — read this before assuming the server is launched manually.

## Key Commands

### Python Environment
- `source venv/bin/activate` - Activate virtual environment
- `pip install -r requirements.txt` - Install Python dependencies

### HTTP Server
- `python -m fastapi dev server.py` - Start development server on localhost:8000
- `python -m fastapi run server.py` - Start production server

### Full Stack
- `npm run dev` - Start backend (port 8000) + frontend (port 3000) concurrently
- `npm run dev:frontend` / `npm run dev:backend` - Start individually

## Project Structure

```
/
├── web/                      # Next.js frontend (read-only log view)
├── video_processor/
│   ├── config.py             # Config dataclass + env loading
│   ├── store.py              # MongoDB: ArtifactStore, RunsStore, UrlCacheStore
│   ├── indexer.py            # index_video() — download + hash + transcribe
│   ├── runner.py             # run_prompt() — route prompt to model provider
│   ├── gemini.py             # Gemini Files API caller (upload + reuse cached ref)
│   ├── transcription.py      # Groq Whisper wrapper
│   ├── downloader.py         # yt-dlp Instagram reel downloader
│   └── mcp_server.py         # FastMCP server exposing primitives to Claude hosts
├── scripts/
│   └── run_mcp.py            # MCP entrypoint (uvicorn on :8002, path /mcp)
├── server.py                 # FastAPI server — 7 endpoints
├── CONTEXT.md                # Domain glossary
└── docs/adr/                 # Architecture decision records
```

## API

Two parallel surfaces over the same MongoDB-backed core:

- **FastAPI HTTP** (`server.py`, port 8001) — the 7 endpoints below; gated by `X-API-Key`. Used by the Next.js log viewer and any direct callers.
- **MCP server** (`video_processor/mcp_server.py`, port 8002, path `/mcp`) — three tools: `index_video_from_url`, `run_prompt`, `get_artifact`. Streamable HTTP transport, gated by `Authorization: Bearer <key>` (same `INST_AI_BOT_API_KEY`). This is what the markdown-only skills (`skills/{adapt-reel,grill-reel}/`) call.

Both wrap the same `index_video` / `run_prompt` functions and write to the same `artifacts` and `runs` collections — runs created via MCP show up in `GET /runs`.

All endpoints are synchronous. FastAPI runs sync handlers in a thread pool automatically.

### Artifacts

**`POST /artifacts`** — Index a video from a URL. Idempotent by content hash.
```json
// Request
{ "url": "https://www.instagram.com/reel/..." }

// Response — Artifact
{
  "content_hash": "sha256:abc123...",
  "video_file_ref": "videos/sha256:abc123....mp4",
  "duration_sec": 42.1,
  "transcript": {
    "text": "full transcript text",
    "segments": [{"start": 0.0, "end": 2.5, "text": "..."}],
    "model": "whisper-large-v3-groq"
  },
  "sources": [
    {
      "type": "instagram_reel",
      "url": "https://...",
      "fetched_at": "...",
      "fetcher": "yt-dlp",
      "metadata": { "caption": "...", "comments": [...] }
    }
  ],
  "gemini_file_ref": null,
  "indexed_at": "...",
  "schema_version": 1
}
```

**`POST /artifacts/upload`** — Index an uploaded video file. Multipart form, field `video`.

**`GET /artifacts`** — List all artifacts (sorted by `indexed_at` desc).

**`GET /artifacts/{content_hash}`** — Get a single artifact by content hash.

### Runs

**`POST /runs`** — Run an opaque prompt against an indexed artifact.
```json
// Request
{
  "artifact": "sha256:abc123...",
  "prompt": "What is the hook of this video?",
  "model": "google/gemini-2.5-pro",   // provider/model-id format
  "label": "hook-extraction"           // optional tag for UI grouping
}

// Response — Run
{
  "run_id": "uuid",
  "artifact_hash": "sha256:abc123...",
  "prompt": "What is the hook of this video?",
  "model": "google/gemini-2.5-pro",
  "label": "hook-extraction",
  "output": "The hook is...",
  "created_at": "..."
}
```

**`GET /runs?artifact={hash}`** — List runs, optionally filtered by artifact hash.

**`GET /runs/{run_id}`** — Get a single run by ID.

### Health

**`GET /health`** — `{"status": "healthy", "version": "2.0.0"}`

## Configuration

Environment variables via `.env`:

| Variable | Default | Description |
|---|---|---|
| `MONGODB_URI` | `mongodb://localhost:27017/` | MongoDB connection string |
| `MONGODB_DB` | `creator-kb` | Database name |
| `VIDEO_DIR` | `videos` | Local directory for video files (keyed by content hash) |
| `GEMINI_API_KEY` | — | Google Gemini API key |
| `GROQ_API_KEY` | — | Groq API key (Whisper transcription) |
| `OPENAI_API_KEY` | — | OpenAI key (reserved for future provider) |
| `TWELVE_LABS_API_KEY` | — | TwelveLabs key (reserved for future provider) |
| `SUPPORTED_VIDEO_FORMATS` | `mp4,mov,avi,mkv,webm,m4v` | Accepted upload formats |
| `INST_AI_BOT_API_KEY` | — | If set, every endpoint except `/health` requires header `X-API-Key: <value>` (401 otherwise). Unset = auth disabled. |
| `INST_AI_BOT_RATE_LIMIT_PER_MIN` | `120` | Per-IP sliding-window rate limit (in-memory, per worker). Behind a proxy uses `X-Forwarded-For` for the real IP. Set to `0` to disable. |

## Architecture Notes

- **Indexing is light**: only Whisper transcription at index time. Visual analysis happens at run time via multimodal model prompt.
- **Gemini file caching**: `gemini_file_ref` stored on artifact; reused across runs if still ACTIVE (avoids re-upload on every prompt iteration).
- **Model routing**: `model` field uses `provider/model-id` format (`google/gemini-2.5-pro`). Runner splits on `/` to dispatch to the right SDK. Currently only `google` is implemented.
- **Idempotent indexing**: content-addressed by SHA-256. Re-indexing the same URL or file returns the existing artifact. New source URLs are appended to `sources[]`.
- **MongoDB collections**: `artifacts`, `runs`, `url_cache`. `url_cache` maps URL → content_hash to skip re-downloads.

## Code Style Guidelines

### Core Principles
- **KISS (Keep It Stupid Simple)** - Prioritize simplicity and clarity over clever solutions
- **DRY (Don't Repeat Yourself)** - Extract common functionality into reusable functions/modules
- **Single Responsibility Principle** - Each function/class should have one clear purpose
- **Explicit is better than implicit** - Code should be self-documenting and clear

### Python Style
- Follow PEP 8 conventions
- Use type hints for function parameters and return values
- Prefer descriptive variable names over comments
- Use dataclasses for configuration and data structures
- Handle errors explicitly with try/except blocks

### Architecture Guidelines
- Keep modules focused and cohesive
- Use dependency injection for external services (database, APIs)
- Separate business logic from HTTP/API concerns
- Prefer composition over inheritance
- Write testable code with clear interfaces

### Error Handling
- Use specific exception types rather than generic Exception
- Log errors with sufficient context for debugging
- Fail fast and provide clear error messages
- Implement graceful degradation where appropriate

## Agent skills

### Issue tracker

Issues live in GitHub Issues on `ArturHorbenko/inst-ai-bot`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default label vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context repo: one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.