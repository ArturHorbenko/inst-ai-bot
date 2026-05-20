"""Instagram reel insights via the Graph API.

Productionises the scratch code that used to live in `auth/index.py`. Pulls
performance metrics (views, reach, engagement, watch time) for the operator's
own reels and stores each fetch as an append-only snapshot.

Insights are mutable, account-scoped data — the deliberate counterpoint to the
content-addressed Artifact. See docs/adr/0004.
"""
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from .downloader import extract_shortcode
from .store import InsightsStore, MetaCredentialsStore

logger = logging.getLogger(__name__)

# Pinned set of reel media-insight metrics. Meta deprecates these periodically
# (`plays`, `clips_replays_count`, `impressions` are already gone) — this
# constant is the one knob to adjust when the Graph API rejects a name.
REEL_INSIGHT_METRICS = [
    "reach",
    "saved",
    "shares",
    "total_interactions",
    "views",
    "ig_reels_avg_watch_time",
    "ig_reels_video_view_total_time",
]

# Base media-node fields fetched alongside the /insights edge.
_MEDIA_FIELDS_BASE = "permalink,caption,timestamp,media_product_type,like_count,comments_count"

_HTTP_TIMEOUT = 20
_TOKEN_REFRESH_MARGIN = timedelta(days=7)
_MEDIA_PAGE_SIZE = 100
_MEDIA_MAX_PAGES = 20
SCHEMA_VERSION = 1


class InsightsError(Exception):
    """A Graph API call failed or the integration is misconfigured."""


class ReelNotOwned(Exception):
    """The reel is not in the operator's own account — no insights available."""


# ── Graph API transport ───────────────────────────────────────────────────────

def _graph_base(config) -> str:
    return f"https://graph.facebook.com/{config.META_GRAPH_VERSION}"


def _graph_get(config, path: str, params: Optional[dict] = None) -> dict:
    """GET a Graph API path (or a full pagination URL); raise InsightsError with
    the API's own message on error."""
    url = path if path.startswith("http") else f"{_graph_base(config)}/{path.lstrip('/')}"
    try:
        resp = requests.get(url, params=params, timeout=_HTTP_TIMEOUT)
    except requests.RequestException as e:
        raise InsightsError(f"Graph API request failed: {e}") from e
    try:
        body = resp.json()
    except ValueError:
        raise InsightsError(f"Graph API returned non-JSON ({resp.status_code})")
    if resp.status_code >= 400 or "error" in body:
        msg = body.get("error", {}).get("message", f"HTTP {resp.status_code}")
        raise InsightsError(f"Graph API error: {msg}")
    return body


# ── Token lifecycle ───────────────────────────────────────────────────────────

def _fingerprint(token: str) -> str:
    """Short, non-reversible identity for a token — used to detect a .env swap."""
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def _exchange_token(config, token: str) -> tuple[str, datetime]:
    """Exchange a token for a fresh long-lived one. Works for short->long and
    long->refreshed-long exchanges. Returns (access_token, expires_at)."""
    body = _graph_get(config, "oauth/access_token", {
        "grant_type": "fb_exchange_token",
        "client_id": config.META_APP_ID,
        "client_secret": config.META_APP_SECRET,
        "fb_exchange_token": token,
    })
    access_token = body.get("access_token")
    if not access_token:
        raise InsightsError("Token exchange returned no access_token")
    # expires_in == 0 can mean a non-expiring token; treat as ~60d for scheduling.
    expires_in = int(body.get("expires_in") or 60 * 24 * 3600)
    return access_token, datetime.now(timezone.utc) + timedelta(seconds=expires_in)


def _ensure_fresh_token(config, creds_store: MetaCredentialsStore) -> str:
    """Return a valid Graph token, bootstrapping / refreshing / re-seeding as
    needed. The live token lives in Mongo so .env is never rewritten."""
    if not config.META_GRAPH_TOKEN:
        raise InsightsError(
            "META_GRAPH_TOKEN (or FB_TOKEN) is not set — cannot call the Instagram Graph API."
        )
    if not (config.META_APP_ID and config.META_APP_SECRET):
        raise InsightsError(
            "META_APP_ID and META_APP_SECRET must be set to refresh the Graph token."
        )

    env_fingerprint = _fingerprint(config.META_GRAPH_TOKEN)
    creds = creds_store.get()

    # Re-seed when .env's token changed (operator pasted a fresh one post-relogin).
    if creds and creds.get("bootstrap_fingerprint") != env_fingerprint:
        logger.info("META_GRAPH_TOKEN changed in .env — re-seeding stored credentials")
        creds = None

    if not creds:
        logger.info("Bootstrapping Instagram Graph token from .env")
        try:
            access_token, expires_at = _exchange_token(config, config.META_GRAPH_TOKEN)
        except InsightsError as e:
            raise InsightsError(
                f"Could not bootstrap the Graph token: {e}. Re-run auth/index.html to "
                "mint a fresh token and update META_GRAPH_TOKEN in .env."
            ) from e
        creds_store.put(access_token, expires_at, env_fingerprint)
        return access_token

    expires_at = creds["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at - datetime.now(timezone.utc) <= _TOKEN_REFRESH_MARGIN:
        logger.info("Graph token near expiry — refreshing")
        try:
            access_token, new_expiry = _exchange_token(config, creds["access_token"])
        except InsightsError as e:
            raise InsightsError(
                f"Graph token refresh failed: {e}. The token may have expired — "
                "re-run auth/index.html and update META_GRAPH_TOKEN in .env."
            ) from e
        creds_store.put(access_token, new_expiry, env_fingerprint)
        return access_token

    return creds["access_token"]


# ── Media resolution + insights fetch ─────────────────────────────────────────

def _permalink_has_shortcode(permalink: str, shortcode: str) -> bool:
    """True when a Graph permalink points at the given shortcode."""
    if not permalink:
        return False
    return permalink.rstrip("/").endswith(f"/{shortcode}")


def resolve_media_id(config, token: str, shortcode: str, insights_store: InsightsStore) -> str:
    """Resolve an Instagram shortcode to a Graph API media id.

    Reuses prior snapshots as a cache; only the first fetch per reel pages the
    account's media list. Raises ReelNotOwned if the reel is not in the account.
    """
    cached = insights_store.find_media_id(shortcode)
    if cached:
        return cached

    if not config.INSTAGRAM_USER_ID:
        raise InsightsError("INSTAGRAM_USER_ID is not set — cannot list account media.")

    path: str = f"{config.INSTAGRAM_USER_ID}/media"
    params: Optional[dict] = {
        "fields": "id,permalink,media_product_type",
        "access_token": token,
        "limit": _MEDIA_PAGE_SIZE,
    }
    for _ in range(_MEDIA_MAX_PAGES):
        body = _graph_get(config, path, params)
        for media in body.get("data", []):
            if _permalink_has_shortcode(media.get("permalink", ""), shortcode):
                return media["id"]
        next_url = body.get("paging", {}).get("next")
        if not next_url:
            break
        # The `next` URL carries its own cursor + token; hand it back wholesale.
        path, params = next_url, None

    raise ReelNotOwned(
        f"Reel '{shortcode}' was not found in your account's media. Instagram "
        "insights are only available for reels on the account you manage."
    )


def fetch_media_insights(config, token: str, media_id: str) -> dict:
    """Fetch base fields + the /insights edge for a media id; return merged
    metrics plus base metadata."""
    base = _graph_get(config, media_id, {
        "fields": _MEDIA_FIELDS_BASE,
        "access_token": token,
    })
    metrics: dict = {}
    if base.get("like_count") is not None:
        metrics["likes"] = base["like_count"]
    if base.get("comments_count") is not None:
        metrics["comments"] = base["comments_count"]

    insights = _graph_get(config, f"{media_id}/insights", {
        "metric": ",".join(REEL_INSIGHT_METRICS),
        "access_token": token,
    })
    for entry in insights.get("data", []):
        name = entry.get("name")
        values = entry.get("values") or []
        if name and values:
            metrics[name] = values[0].get("value")

    return {
        "permalink": base.get("permalink"),
        "caption": base.get("caption"),
        "posted_at": base.get("timestamp"),
        "media_product_type": base.get("media_product_type"),
        "metrics": metrics,
    }


# ── Orchestrator ──────────────────────────────────────────────────────────────

def get_reel_insights(
    url: str,
    config,
    insights_store: InsightsStore,
    creds_store: MetaCredentialsStore,
) -> dict:
    """Fetch fresh insights for one reel and store an append-only snapshot.

    Returns the stored snapshot. Raises ValueError (bad URL), ReelNotOwned (not
    your reel), or InsightsError (API / config failure).
    """
    shortcode = extract_shortcode(url)
    token = _ensure_fresh_token(config, creds_store)
    media_id = resolve_media_id(config, token, shortcode, insights_store)
    fetched = fetch_media_insights(config, token, media_id)

    snapshot = {
        "media_id": media_id,
        "shortcode": shortcode,
        "permalink": fetched["permalink"],
        "media_product_type": fetched["media_product_type"],
        "caption": fetched["caption"],
        "posted_at": fetched["posted_at"],
        "metrics": fetched["metrics"],
        "fetched_at": datetime.now(timezone.utc),
        "schema_version": SCHEMA_VERSION,
    }
    stored = insights_store.insert(snapshot)
    logger.info(f"Stored insights snapshot for reel {shortcode} ({media_id})")
    return stored
