"""MCP server fronting the inst-ai-bot artifact/run primitives.

Exposes read/write Artifact primitives plus read-only dashboard analytics tools
over Streamable HTTP:
  - index_video_from_url(url)
  - run_prompt(artifact_hash, prompt, model?, label?, metadata?)
  - get_artifact(content_hash)
  - get_current_creator_profile(days?)
  - list_recent_content(limit?)
  - get_content_analytics(media_id, days?)
  - content_audit(days?)

Auth: configurable bearer or OAuth 2.1 resource-server validation. Every
authorized client operates on the same creator configured by the server.

Mirrors the FastAPI routes in server.py but is callable by MCP clients
(Claude Code, Claude Desktop) without bundling secrets into a skill.
"""
import logging
import os
import secrets
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import get_config
from .dashboard_analytics import DashboardAnalyticsClient
from .indexer import index_video
from .mcp_auth import build_oauth_runtime
from .retrieval import (
    RetrievalStore,
    resolve_retrieval_contract,
    search_retrieval_documents,
)
from .runner import ArtifactNotFound, run_prompt as run_prompt_impl
from .store import ArtifactStore, DatabaseConnection, RunsStore, UrlCacheStore

logger = logging.getLogger(__name__)


class ToolOutput(BaseModel):
    """Base for MCP outputs whose nested dashboard fields can evolve independently."""

    model_config = ConfigDict(extra="allow")


class ArtifactOutput(ToolOutput):
    content_hash: str
    duration_sec: Optional[float] = None
    transcript_text: Optional[str] = None
    caption: Optional[str] = None
    hashtags: list[str]
    uploader: Optional[str] = None
    comments: list[Any]
    indexed_at: Any = None


class RunPromptOutput(ToolOutput):
    run_id: str
    output: str


class RetrievalDocumentOutput(ToolOutput):
    media_id: str
    content_hash: str
    trait_schema: str
    prompt_version: str
    chunk_id: str
    kind: str
    start_sec: Optional[float] = None
    end_sec: Optional[float] = None
    text: str
    retrieval_tags: list[str]
    score: Optional[float] = None


class VideoContextOutput(ToolOutput):
    content_hash: str
    media_id: Optional[str] = None
    retrieval_documents: list[RetrievalDocumentOutput]
    transcript: list[dict[str, Any]]
    sources: list[dict[str, Any]]


class CreatorProfileOutput(ToolOutput):
    window: dict[str, Any]
    coverage: dict[str, Any]
    pillars: dict[str, Any]
    contentTypes: dict[str, Any]
    voice: dict[str, Any]
    audience: dict[str, Any]
    brands: dict[str, Any]


class ContentAnalyticsOutput(ToolOutput):
    media: dict[str, Any]
    latestObservation: Optional[dict[str, Any]] = None
    observations: list[dict[str, Any]]
    snapshotMetricChanges: list[dict[str, Any]]
    snapshotRateSeries: list[dict[str, Any]]
    taxonomy: Optional[dict[str, Any]] = None
    manualTaxonomy: Optional[dict[str, Any]] = None
    taxonomyTrait: Optional[dict[str, Any]] = None
    retrievalTrait: Optional[dict[str, Any]] = None
    commentResponse: Optional[dict[str, Any]] = None
    trait: Optional[dict[str, Any]] = None


class ContentAuditOutput(ToolOutput):
    window: dict[str, Any]
    coverage: dict[str, Any]
    medians: dict[str, Any]
    leaders: dict[str, Any]
    taxonomy: dict[str, Any]
    byFormat: list[dict[str, Any]]
    content: list[dict[str, Any]]
    reels: list[dict[str, Any]]

READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
INDEX_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
RUN_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)

_config = get_config()
API_KEY = os.environ.get("INST_AI_BOT_API_KEY", "").strip()
AUTH_MODE = _config.MCP_AUTH_MODE
TOOL_SECURITY_META = (
    {
        "securitySchemes": [
            {"type": "oauth2", "scopes": [_config.MCP_OAUTH_SCOPE]},
        ]
    }
    if AUTH_MODE in {"oauth", "oauth-and-bearer"}
    else None
)
_oauth_runtime = None
_oauth_config_error = None
if AUTH_MODE in {"oauth", "oauth-and-bearer"}:
    try:
        _oauth_runtime = build_oauth_runtime(
            issuer_url=_config.MCP_OAUTH_ISSUER_URL,
            resource_url=_config.MCP_RESOURCE_URL,
            jwks_url=_config.MCP_OAUTH_JWKS_URL,
            audience=_config.MCP_OAUTH_AUDIENCE,
            required_scope=_config.MCP_OAUTH_SCOPE,
            algorithms=tuple(
                algorithm.strip()
                for algorithm in _config.MCP_OAUTH_ALGORITHMS.split(",")
                if algorithm.strip()
            ),
            legacy_bearer_token=API_KEY if AUTH_MODE == "oauth-and-bearer" else None,
        )
    except ValueError as exc:
        _oauth_config_error = str(exc)


mcp = FastMCP(
    "inst-ai-bot",
    instructions=(
        "Every creator-specific workflow operates on the one creator configured by the server "
        "and must start by calling get_current_creator_profile(days) before retrieval, indexing, "
        "or run_prompt.\n\n"
        "Typical flow:\n"
        "  1) get_current_creator_profile(days) -> current creator context\n"
        "  2) index_video_from_url(url) -> {content_hash, transcript_text, caption, ...}\n"
        "  3) search_videos(query) -> matching videos and timestamped moments\n"
        "  4) get_video_context(content_hash, media_id?) -> evidence for an answer\n\n"
        "Indexing is idempotent (content-hash addressed); re-calling index_video_from_url "
        "with the same URL is a cheap cache hit."
    ),
    host="0.0.0.0",
    port=8002,
    stateless_http=True,
    auth=_oauth_runtime.settings if _oauth_runtime else None,
    token_verifier=_oauth_runtime.verifier if _oauth_runtime else None,
)


_db = DatabaseConnection(_config)
_artifact_store: Optional[ArtifactStore] = None
_runs_store: Optional[RunsStore] = None
_url_cache: Optional[UrlCacheStore] = None
_retrieval_store: Optional[RetrievalStore] = None
_dashboard_analytics: Optional[DashboardAnalyticsClient] = None


def _ensure_db() -> None:
    global _artifact_store, _runs_store, _url_cache, _retrieval_store
    if _artifact_store is not None:
        return
    if not _db.connect():
        raise RuntimeError("MongoDB connection required for MCP server")
    _artifact_store = ArtifactStore(_db.db)
    _runs_store = RunsStore(_db.db)
    _url_cache = UrlCacheStore(_db.db)
    _retrieval_store = RetrievalStore(_db.db)


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


@mcp.tool(
    title="Index Instagram video",
    annotations=INDEX_ANNOTATIONS,
    meta=TOOL_SECURITY_META,
    structured_output=True,
)
def index_video_from_url(url: str) -> ArtifactOutput:
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


@mcp.tool(
    title="Run video prompt",
    annotations=RUN_ANNOTATIONS,
    meta=TOOL_SECURITY_META,
    structured_output=True,
)
def run_prompt(
    artifact_hash: str,
    prompt: str,
    model: str = "google/gemini-2.5-pro",
    label: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> RunPromptOutput:
    """Run an opaque multimodal prompt against an indexed artifact.

    `artifact_hash` must be a `content_hash` returned by `index_video_from_url`.
    `model` uses `provider/model-id` format (e.g. `google/gemini-2.5-pro`); only
    the `google` provider is wired today. `label` is an optional tag for grouping
    runs in the log view. `metadata` optionally records a stable caller namespace
    such as a trait schema and prompt version. Returns `{run_id, output}`.
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


@mcp.tool(
    title="Get indexed artifact",
    annotations=READ_ONLY_ANNOTATIONS,
    meta=TOOL_SECURITY_META,
    structured_output=True,
)
def get_artifact(content_hash: str) -> ArtifactOutput:
    """Fetch a previously indexed artifact by `content_hash`. Returns the trimmed
    artifact (same shape as `index_video_from_url`). Useful when you already have a
    hash and want to re-read its transcript / metadata without re-indexing."""
    _ensure_db()
    artifact = _artifact_store.get_by_hash(content_hash)
    if not artifact:
        raise ValueError(f"Artifact not found: {content_hash}")
    return _trim_artifact(artifact)


@mcp.tool(
    title="Search indexed videos",
    annotations=READ_ONLY_ANNOTATIONS,
    meta=TOOL_SECURITY_META,
    structured_output=True,
)
def search_videos(
    query: str,
    limit: int = 8,
    trait_schema: Optional[str] = None,
    prompt_version: Optional[str] = None,
) -> list[RetrievalDocumentOutput]:
    """Semantic-search indexed videos and timestamped moments with Atlas Vector Search.

    Use this first for questions such as "find a Reel about fence installation" or
    "where does a creator use a running-away punchline?" Results include the
    matching artifact hash, dashboard media ID, timestamp range, retrieval text,
    and a similarity score. It only searches documents created by
    the active `reel-retrieval/v1` / `2026-08-07` contract by default; it never
    downloads video or invokes a video model. Supply both `trait_schema` and
    `prompt_version` only to query one other explicit contract.
    """
    _ensure_db()
    try:
        return search_retrieval_documents(
            store=_retrieval_store,
            config=_config,
            query=query,
            limit=limit,
            trait_schema=trait_schema,
            prompt_version=prompt_version,
        )
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    except Exception as exc:
        logger.exception("Atlas vector search failed")
        raise RuntimeError(
            "Video search is unavailable. Confirm Atlas Vector Search is enabled and "
            "the configured ATLAS_VECTOR_INDEX exists."
        ) from exc


@mcp.tool(
    title="Get video context",
    annotations=READ_ONLY_ANNOTATIONS,
    meta=TOOL_SECURITY_META,
    structured_output=True,
)
def get_video_context(
    content_hash: str,
    media_id: Optional[str] = None,
    trait_schema: Optional[str] = None,
    prompt_version: Optional[str] = None,
) -> VideoContextOutput:
    """Get the stored retrieval chunks and source transcript for one indexed video.

    Call this after `search_videos` with its `content_hash` (and `media_id` when
    provided) before answering a detailed question. This is a read-only MongoDB
    lookup; it does not rerun an embedding or Gemini video analysis. It returns
    only the active `reel-retrieval/v1` / `2026-08-07` contract by default.
    Supply both version fields only to select another explicit contract.
    """
    _ensure_db()
    artifact = _artifact_store.get_by_hash(content_hash)
    if not artifact:
        raise ValueError(f"Artifact not found: {content_hash}")
    contract = resolve_retrieval_contract(
        trait_schema=trait_schema,
        prompt_version=prompt_version,
    )
    return {
        "content_hash": content_hash,
        "media_id": media_id,
        "retrieval_documents": _retrieval_store.get_context(
            content_hash=content_hash,
            media_id=media_id,
            contract=contract,
        ),
        "transcript": (artifact.get("transcript") or {}).get("segments") or [],
        "sources": artifact.get("sources") or [],
    }


@mcp.tool(
    title="Get creator profile",
    annotations=READ_ONLY_ANNOTATIONS,
    meta=TOOL_SECURITY_META,
    structured_output=True,
)
def get_current_creator_profile(days: int = 60) -> CreatorProfileOutput:
    """Read the current evidence-first creator profile from the analytics dashboard.

    This read-only tool returns the dashboard's bounded profile of the connected
    creator. Call it first in every creator-specific workflow; the dashboard
    authoritatively enforces its 30–60 day window.
    """
    return _dashboard_client().get_current_creator_profile(days)


@mcp.tool(
    title="List recent content",
    annotations=READ_ONLY_ANNOTATIONS,
    meta=TOOL_SECURITY_META,
    structured_output=True,
)
def list_recent_content(limit: int = 10) -> list[ContentAnalyticsOutput]:
    """Read up to 25 recent Reels and Feed posts from stored analytics data.

    Results include the latest Meta observation, calculated day-over-day view
    growth when two snapshots exist, and the newest validated trait extraction.
    Trial Reels are excluded; other Reels and Feed posts are included. This tool
    is read-only: it never calls Meta or starts a model Run.
    """
    return _dashboard_client().list_recent_content(limit)


@mcp.tool(
    title="Get content analytics",
    annotations=READ_ONLY_ANNOTATIONS,
    meta=TOOL_SECURITY_META,
    structured_output=True,
)
def get_content_analytics(media_id: str, days: int = 30) -> ContentAnalyticsOutput:
    """Read one Reel or Feed post's history and newest validated traits.

    `media_id` is Meta's media ID, returned by `list_recent_content`. `days` is
    bounded to 1–90 by the dashboard. This is a database read, not a fresh Meta
    request or a video/model operation.
    """
    return _dashboard_client().get_content_analytics(media_id, days)


@mcp.tool(
    title="Audit creator content",
    annotations=READ_ONLY_ANNOTATIONS,
    meta=TOOL_SECURITY_META,
    structured_output=True,
)
def content_audit(days: int = 30) -> ContentAuditOutput:
    """Summarize the last N days of stored Reel and Feed post performance.

    Returns data coverage, personal medians, leaders by views/share/save rate,
    and available format comparisons for 1–365 days. Trial Reels are excluded;
    other Reels and Feed posts are included. It reads stored dashboard
    observations only; it never calls Meta or starts a model Run.
    """
    return _dashboard_client().get_content_audit(days)


if AUTH_MODE == "bearer" and API_KEY:
    logger.info("MCP bearer auth: ENABLED")
elif AUTH_MODE == "oauth-and-bearer" and API_KEY:
    logger.info("MCP OAuth and legacy bearer auth: ENABLED")
elif AUTH_MODE in {"bearer", "oauth-and-bearer"}:
    logger.warning("MCP bearer key is not configured")
elif AUTH_MODE == "oauth":
    logger.info("MCP OAuth auth: ENABLED")


OAUTH_PROTECTED_RESOURCE_METADATA_PATHS = frozenset({
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-protected-resource/mcp",
})


class BearerAuthMiddleware:
    """Streaming-safe ASGI bearer authentication for the MCP application."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope.get("path") in OAUTH_PROTECTED_RESOURCE_METADATA_PATHS:
            await self.app(scope, receive, send)
            return
        if not API_KEY:
            await self.app(scope, receive, send)
            return
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        auth = headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            await JSONResponse({"error": "missing bearer token"}, status_code=401)(scope, receive, send)
            return
        token = auth.split(" ", 1)[1].strip()
        if not secrets.compare_digest(token, API_KEY):
            await JSONResponse({"error": "invalid bearer token"}, status_code=401)(scope, receive, send)
            return
        await self.app(scope, receive, send)


def build_app() -> Starlette:
    """Return the MCP ASGI app with the configured authentication mode."""
    app = mcp.streamable_http_app()
    if AUTH_MODE == "disabled-dev":
        logger.warning("MCP authentication: DISABLED FOR DEVELOPMENT")
        return app
    if AUTH_MODE in {"oauth", "oauth-and-bearer"}:
        if _oauth_runtime is None:
            raise RuntimeError(_oauth_config_error or "MCP OAuth is not configured")
        from mcp.server.auth.handlers.metadata import ProtectedResourceMetadataHandler
        from mcp.server.auth.routes import cors_middleware
        from mcp.shared.auth import ProtectedResourceMetadata
        from starlette.routing import Route

        metadata = ProtectedResourceMetadata(
            resource=_config.MCP_RESOURCE_URL,
            authorization_servers=[_config.MCP_OAUTH_ISSUER_URL],
            scopes_supported=[_config.MCP_OAUTH_SCOPE],
        )
        app.routes.insert(
            0,
            Route(
                "/.well-known/oauth-protected-resource",
                endpoint=cors_middleware(
                    ProtectedResourceMetadataHandler(metadata).handle,
                    ["GET", "OPTIONS"],
                ),
                methods=["GET", "OPTIONS"],
            ),
        )
        return app
    if AUTH_MODE != "bearer":
        raise RuntimeError(f"Unsupported MCP_AUTH_MODE: {AUTH_MODE}")
    if not API_KEY:
        raise RuntimeError("INST_AI_BOT_API_KEY is required when MCP_AUTH_MODE=bearer")
    app.add_middleware(BearerAuthMiddleware)
    return app
