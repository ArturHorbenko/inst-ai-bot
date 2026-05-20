"""MCP server fronting the inst-ai-bot artifact/run primitives.

Exposes four tools over Streamable HTTP:
  - index_video_from_url(url)
  - run_prompt(artifact_hash, prompt, model?, label?)
  - get_artifact(content_hash)
  - get_reel_insights(url)

Auth: Bearer token in `Authorization: Bearer <key>`; must match
`INST_AI_BOT_API_KEY`. If the env var is unset, auth is disabled (matches
server.py's behaviour for parity).

Mirrors the FastAPI routes in server.py but is callable by MCP clients
(Claude Code, Claude Desktop) without bundling secrets into a skill.
"""
import logging
import os
import secrets
from typing import Optional

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .config import get_config
from .indexer import index_video
from .insights import InsightsError, ReelNotOwned, get_reel_insights as get_reel_insights_impl
from .runner import ArtifactNotFound, run_prompt as run_prompt_impl
from .store import (
    ArtifactStore,
    DatabaseConnection,
    InsightsStore,
    MetaCredentialsStore,
    RunsStore,
    UrlCacheStore,
)

logger = logging.getLogger(__name__)


mcp = FastMCP(
    "inst-ai-bot",
    instructions=(
        "Primitives for indexing and querying short-form videos (e.g. Instagram reels).\n\n"
        "Typical flow:\n"
        "  1) index_video_from_url(url) -> {content_hash, transcript_text, caption, ...}\n"
        "  2) run_prompt(artifact_hash=content_hash, prompt=...) -> {output}\n\n"
        "Indexing is idempotent (content-hash addressed); re-calling index_video_from_url "
        "with the same URL is a cheap cache hit.\n\n"
        "get_reel_insights(url) pulls real Instagram performance metrics for your own "
        "reels (separate from indexing — no content hash needed)."
    ),
    host="0.0.0.0",
    port=8002,
    stateless_http=True,
)


_config = get_config()
_db = DatabaseConnection(_config)
_artifact_store: Optional[ArtifactStore] = None
_runs_store: Optional[RunsStore] = None
_url_cache: Optional[UrlCacheStore] = None
_insights_store: Optional[InsightsStore] = None
_meta_creds: Optional[MetaCredentialsStore] = None

_HISTORY_LIMIT = 10


def _ensure_db() -> None:
    global _artifact_store, _runs_store, _url_cache, _insights_store, _meta_creds
    if _artifact_store is not None:
        return
    if not _db.connect():
        raise RuntimeError("MongoDB connection required for MCP server")
    _artifact_store = ArtifactStore(_db.db)
    _runs_store = RunsStore(_db.db)
    _url_cache = UrlCacheStore(_db.db)
    _insights_store = InsightsStore(_db.db)
    _meta_creds = MetaCredentialsStore(_db.db)


def _trim_artifact(artifact: dict) -> dict:
    """Drop bulky fields (transcript segments, gemini_file_ref, video_file_ref) and
    flatten Instagram metadata to the top level. The agent gets exactly what the
    prompt templates need, nothing more."""
    transcript = artifact.get("transcript") or {}
    sources = artifact.get("sources") or []
    insta = next((s for s in sources if s.get("type") == "instagram_reel"), None) or {}
    md = insta.get("metadata") or {}
    indexed_at = artifact.get("indexed_at")
    return {
        "content_hash": artifact["content_hash"],
        "duration_sec": artifact.get("duration_sec"),
        "transcript_text": transcript.get("text"),
        "caption": md.get("caption"),
        "hashtags": md.get("hashtags") or [],
        "uploader": md.get("uploader"),
        "comments": md.get("comments") or [],
        "indexed_at": indexed_at.isoformat() if hasattr(indexed_at, "isoformat") else indexed_at,
    }


@mcp.tool()
def index_video_from_url(url: str) -> dict:
    """Download, hash, and transcribe a short-form video. Idempotent by content hash.

    Pass an Instagram reel URL (e.g. `https://www.instagram.com/reel/...` or `/p/...`).
    Returns the fields needed for downstream prompting: `content_hash` (use as
    `artifact_hash` in `run_prompt`), `transcript_text`, and scraped post metadata
    (caption, hashtags, uploader, comments). Calling twice with the same URL is a
    cheap cache hit.
    """
    _ensure_db()
    artifact = index_video(url, _config, _artifact_store, _url_cache)
    return _trim_artifact(artifact)


@mcp.tool()
def run_prompt(
    artifact_hash: str,
    prompt: str,
    model: str = "google/gemini-2.5-pro",
    label: Optional[str] = None,
) -> dict:
    """Run an opaque multimodal prompt against an indexed artifact.

    `artifact_hash` must be a `content_hash` returned by `index_video_from_url`.
    `model` uses `provider/model-id` format (e.g. `google/gemini-2.5-pro`); only
    the `google` provider is wired today. `label` is an optional tag for grouping
    runs in the log view. Returns `{run_id, output}`.
    """
    _ensure_db()
    try:
        run = run_prompt_impl(
            artifact_hash=artifact_hash,
            prompt=prompt,
            model=model,
            label=label,
            config=_config,
            artifact_store=_artifact_store,
            runs_store=_runs_store,
        )
    except ArtifactNotFound as e:
        raise ValueError(str(e))
    return {"run_id": run["run_id"], "output": run["output"]}


@mcp.tool()
def get_artifact(content_hash: str) -> dict:
    """Fetch a previously indexed artifact by `content_hash`. Returns the trimmed
    artifact (same shape as `index_video_from_url`). Useful when you already have a
    hash and want to re-read its transcript / metadata without re-indexing."""
    _ensure_db()
    artifact = _artifact_store.get_by_hash(content_hash)
    if not artifact:
        raise ValueError(f"Artifact not found: {content_hash}")
    return _trim_artifact(artifact)


def _iso(value):
    """ISO-format a datetime; pass through anything already a string/None."""
    return value.isoformat() if hasattr(value, "isoformat") else value


def _format_snapshot(snapshot: dict) -> dict:
    """An insights snapshot with datetime fields made JSON-safe."""
    return {
        "media_id": snapshot["media_id"],
        "shortcode": snapshot.get("shortcode"),
        "permalink": snapshot.get("permalink"),
        "media_product_type": snapshot.get("media_product_type"),
        "caption": snapshot.get("caption"),
        "posted_at": snapshot.get("posted_at"),
        "metrics": snapshot.get("metrics") or {},
        "fetched_at": _iso(snapshot.get("fetched_at")),
    }


@mcp.tool()
def get_reel_insights(url: str) -> dict:
    """Fetch Instagram performance metrics for one of your own reels.

    Pass a reel URL (e.g. `https://www.instagram.com/reel/...`). Fetches fresh
    numbers from the Instagram Graph API (views, reach, likes, comments, saved,
    shares, total interactions, average watch time), stores an append-only
    snapshot, and returns the latest snapshot plus recent `history` (prior
    snapshots, oldest-excluded-current) for decay/growth comparison.

    Only works for reels on the Instagram account this server is configured to
    manage — there are no insights for arbitrary public reels. This is separate
    from `index_video_from_url`; no content hash is involved.
    """
    _ensure_db()
    try:
        snapshot = get_reel_insights_impl(url, _config, _insights_store, _meta_creds)
    except (ReelNotOwned, InsightsError) as e:
        raise ValueError(str(e))
    result = _format_snapshot(snapshot)
    result["history"] = [
        {"fetched_at": _iso(h.get("fetched_at")), "metrics": h.get("metrics") or {}}
        for h in _insights_store.list(snapshot["media_id"])
        if h.get("fetched_at") != snapshot.get("fetched_at")
    ][:_HISTORY_LIMIT]
    return result


API_KEY = os.environ.get("INST_AI_BOT_API_KEY", "").strip()
if API_KEY:
    logger.info("MCP bearer auth: ENABLED")
else:
    logger.warning("MCP bearer auth: DISABLED (set INST_AI_BOT_API_KEY to require it)")


class BearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if not API_KEY:
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return JSONResponse({"error": "missing bearer token"}, status_code=401)
        token = auth.split(" ", 1)[1].strip()
        if not secrets.compare_digest(token, API_KEY):
            return JSONResponse({"error": "invalid bearer token"}, status_code=401)
        return await call_next(request)


def build_app() -> Starlette:
    """Return the Starlette ASGI app for the MCP server with bearer auth applied."""
    app = mcp.streamable_http_app()
    app.add_middleware(BearerAuthMiddleware)
    return app
