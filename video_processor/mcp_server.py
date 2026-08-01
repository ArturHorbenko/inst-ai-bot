"""MCP server fronting the inst-ai-bot artifact/run primitives.

Exposes tools over Streamable HTTP:
  - index_video_from_url(url)
  - run_prompt(artifact_hash, prompt, model?, label?, metadata?)
  - get_artifact(content_hash)
  - get_instagram_post_status(url, max_comments?, include_comments?)

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

from datetime import datetime, timezone

from .config import get_config
from .dashboard_analytics import DashboardAnalyticsClient
from .indexer import index_video
from .runner import ArtifactNotFound, run_prompt as run_prompt_impl
from .social_status import fetch_instagram_post_status, to_status_snapshot
from .store import (
    ArtifactStore,
    DatabaseConnection,
    PostStatusStore,
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
        "with the same URL is a cheap cache hit."
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
_post_status_store: Optional[PostStatusStore] = None
_dashboard_analytics: Optional[DashboardAnalyticsClient] = None


def _ensure_db() -> None:
    global _artifact_store, _runs_store, _url_cache, _post_status_store
    if _artifact_store is not None:
        return
    if not _db.connect():
        raise RuntimeError("MongoDB connection required for MCP server")
    _artifact_store = ArtifactStore(_db.db)
    _runs_store = RunsStore(_db.db)
    _url_cache = UrlCacheStore(_db.db)
    _post_status_store = PostStatusStore(_db.db)


def _dashboard_client() -> DashboardAnalyticsClient:
    global _dashboard_analytics
    if _dashboard_analytics is None:
        _dashboard_analytics = DashboardAnalyticsClient(
            _config.ANALYTICS_DASHBOARD_URL,
            _config.ANALYTICS_DASHBOARD_API_KEY,
        )
    return _dashboard_analytics


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
    metadata: Optional[dict] = None,
) -> dict:
    """Run an opaque multimodal prompt against an indexed artifact.

    `artifact_hash` must be a `content_hash` returned by `index_video_from_url`.
    `model` uses `provider/model-id` format. Two providers are wired:
    `google/gemini-2.5-pro` (default) and `twelvelabs/pegasus1.5`. To compare
    providers, run the same prompt twice with different `model` values and one
    shared `label` — both runs show up side by side in the log view. `label` is
    an optional tag for grouping runs. `metadata` optionally records a stable
    caller namespace such as a trait schema and prompt version. Returns
    `{run_id, output}`.
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
            metadata=metadata,
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


@mcp.tool()
def list_recent_reels(limit: int = 10) -> list[dict]:
    """Read up to 25 recent Reels from the dashboard's stored analytics data.

    Results include the latest Meta observation, calculated day-over-day view
    growth when two snapshots exist, and the newest validated trait extraction.
    This tool is read-only: it never calls Meta or starts a model Run.
    """
    return _dashboard_client().list_recent_reels(limit)


@mcp.tool()
def get_reel_analytics(media_id: str, days: int = 30) -> dict:
    """Read one Reel's stored observation history and newest validated traits.

    `media_id` is Meta's media ID, returned by `list_recent_reels`. `days` is
    bounded to 1–90 by the dashboard. This is a database read, not a fresh Meta
    request or a video/model operation.
    """
    return _dashboard_client().get_reel_analytics(media_id, days)


@mcp.tool()
def get_instagram_post_status(
    url: str,
    max_comments: int = 50,
    include_comments: bool = True,
) -> dict:
    """Fetch current public Instagram reel/post status and persist a snapshot.

    Use for posted `/reel/...` or `/p/...` URLs when you need fresh engagement
    numbers and a bounded sample of comments. Returns caption, hashtags, uploader,
    `like_count`, `view_count`, `comment_count`, and up to `max_comments` ranked
    ("Top") comments (each with a preview of its reply thread). Metadata comes from
    yt-dlp; comments come from Instagram's web API and require a logged-in session
    (`INSTAGRAM_COOKIES_FILE`). Each call is stored as a timestamped snapshot in the
    `post_status` collection, so repeated calls build an engagement history.
    """
    _ensure_db()
    status = fetch_instagram_post_status(
        url=url,
        max_comments=max_comments,
        include_comments=include_comments,
    )
    fetched_at = datetime.now(timezone.utc)
    snapshot = to_status_snapshot(status, fetched_at)
    _post_status_store.insert(snapshot)
    status["shortcode"] = snapshot["shortcode"]
    status["fetched_at"] = fetched_at.isoformat()
    return status


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
