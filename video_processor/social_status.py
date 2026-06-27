import http.cookiejar
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from .downloader import _extract_hashtags, _is_instagram_reel_url

logger = logging.getLogger(__name__)

_DEFAULT_COMMENT_LIMIT = 50
_MAX_COMMENT_LIMIT = 500
_COMMENTS_PER_PAGE = 15  # Instagram's web page size; used only as a paging-safety bound
_MAX_COMMENT_PAGES = 40
_PREVIEW_REPLY_LIMIT = 3
# Seconds to wait between successive comment-page requests, to avoid the burst
# pattern Instagram rate-limits (HTTP 429). Override with INSTAGRAM_COMMENT_PAGE_DELAY.
_DEFAULT_PAGE_DELAY_SEC = 2.0

# Instagram web app id — the public value the website itself sends.
_IG_APP_ID = "936619743392459"
_IG_ASBD_ID = "129477"
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
)
# Standard base64 alphabet Instagram uses to encode media pk -> shortcode.
_SHORTCODE_ALPHABET = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)


def _resolve_cookie_path() -> Optional[Path]:
    """Return the configured Instagram cookie file, validating it exists."""
    cookies_file = os.environ.get("INSTAGRAM_COOKIES_FILE", "").strip()
    if not cookies_file:
        return None
    cookie_path = Path(cookies_file).expanduser()
    if not cookie_path.exists():
        raise ValueError(f"INSTAGRAM_COOKIES_FILE does not exist: {cookie_path}")
    return cookie_path


def _shortcode_from_url(url: str) -> Optional[str]:
    """Pull the shortcode out of a /reel/<sc>/, /reels/<sc>/ or /p/<sc>/ URL."""
    path = urllib.parse.urlparse(url).path.strip("/")
    parts = path.split("/")
    for marker in ("reel", "reels", "p"):
        if marker in parts:
            idx = parts.index(marker)
            if idx + 1 < len(parts):
                return parts[idx + 1]
    return None


def _shortcode_to_media_id(shortcode: str) -> int:
    """Decode an Instagram shortcode to its numeric media id."""
    media_id = 0
    for char in shortcode:
        media_id = media_id * 64 + _SHORTCODE_ALPHABET.index(char)
    return media_id


def _load_cookie_jar(cookie_path: Path) -> http.cookiejar.MozillaCookieJar:
    jar = http.cookiejar.MozillaCookieJar(str(cookie_path))
    jar.load(ignore_discard=True, ignore_expires=True)
    return jar


def _csrf_token(jar: http.cookiejar.MozillaCookieJar) -> str:
    for cookie in jar:
        if cookie.name == "csrftoken" and "instagram" in cookie.domain:
            return cookie.value or ""
    return ""


def _normalize_comment(raw: Dict[str, Any]) -> Dict[str, Any]:
    replies = []
    for child in (raw.get("preview_child_comments") or [])[:_PREVIEW_REPLY_LIMIT]:
        text = (child.get("text") or "").strip()
        if not text:
            continue
        replies.append({
            "author": (child.get("user") or {}).get("username"),
            "text": text,
            "timestamp": child.get("created_at"),
            "like_count": child.get("comment_like_count"),
        })
    return {
        "author": (raw.get("user") or {}).get("username"),
        "text": (raw.get("text") or "").strip(),
        "timestamp": raw.get("created_at"),
        "like_count": raw.get("comment_like_count"),
        "reply_count": raw.get("child_comment_count") or 0,
        "replies": replies,
    }


def _page_delay_seconds() -> float:
    raw = os.environ.get("INSTAGRAM_COMMENT_PAGE_DELAY", "").strip()
    if not raw:
        return _DEFAULT_PAGE_DELAY_SEC
    try:
        return max(0.0, float(raw))
    except ValueError:
        logger.warning("Invalid INSTAGRAM_COMMENT_PAGE_DELAY=%r; using default", raw)
        return _DEFAULT_PAGE_DELAY_SEC


def _fetch_comments_via_web_api(
    shortcode: str,
    cookie_path: Path,
    max_comments: int,
    referer: str,
) -> Tuple[List[Dict[str, Any]], Optional[int]]:
    """Fetch ranked top-level comments from Instagram's web comments endpoint.

    Returns (comments, total_comment_count). Paginates `next_min_id` until
    `max_comments` are collected or the post runs out of comments, sleeping
    `_page_delay_seconds()` between successive page requests to stay under
    Instagram's rate limit.
    """
    media_id = _shortcode_to_media_id(shortcode)
    jar = _load_cookie_jar(cookie_path)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    headers = {
        "User-Agent": _USER_AGENT,
        "X-IG-App-ID": _IG_APP_ID,
        "X-ASBD-ID": _IG_ASBD_ID,
        "X-IG-WWW-Claim": "0",
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRFToken": _csrf_token(jar),
        "Referer": referer,
        "Accept": "*/*",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }
    base = (
        f"https://www.instagram.com/api/v1/media/{media_id}/comments/"
        "?can_support_threading=true&permalink_enabled=false&sort_order=popular"
    )

    comments: List[Dict[str, Any]] = []
    total: Optional[int] = None
    min_id: Optional[str] = None
    page_delay = _page_delay_seconds()

    for page in range(_MAX_COMMENT_PAGES):
        url = base
        if min_id:
            url = f"{base}&min_id={urllib.parse.quote(min_id)}"
        if page > 0 and page_delay:
            time.sleep(page_delay)
        request = urllib.request.Request(url, headers=headers)
        with opener.open(request, timeout=25) as response:
            payload = json.loads(response.read())

        if total is None:
            total = payload.get("comment_count")
        for raw in payload.get("comments") or []:
            normalized = _normalize_comment(raw)
            if normalized["text"]:
                comments.append(normalized)
            if len(comments) >= max_comments:
                return comments[:max_comments], total

        # Ranked ("popular") order signals further pages via
        # `has_more_headload_comments`; chronological order uses
        # `has_more_comments`. Either, paired with a cursor, means keep going.
        has_more = payload.get("has_more_comments") or payload.get(
            "has_more_headload_comments"
        )
        min_id = payload.get("next_min_id")
        if not has_more or not min_id:
            break

    return comments[:max_comments], total


def _fetch_metadata(url: str, cookie_path: Optional[Path]) -> Dict[str, Any]:
    """Fetch post metadata via yt-dlp (no comments — those come from the web API)."""
    ydl_opts: Dict[str, Any] = {
        "skip_download": True,
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "getcomments": False,
    }
    if cookie_path:
        ydl_opts["cookiefile"] = str(cookie_path)
    try:
        with YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False) or {}
    except DownloadError as e:
        raise RuntimeError(
            f"yt-dlp failed to fetch Instagram status for {url}: {e}"
        ) from e


def to_status_snapshot(status: Dict[str, Any], fetched_at: Any) -> Dict[str, Any]:
    """Shape a fetch result into a persistable snapshot (adds shortcode + fetched_at).

    Pure: takes the `fetched_at` timestamp from the caller so storage stays in the
    store/MCP layer and this module never touches a clock or a database.
    """
    return {
        **status,
        "shortcode": _shortcode_from_url(status.get("url") or ""),
        "fetched_at": fetched_at,
    }


def fetch_instagram_post_status(
    url: str,
    max_comments: int = _DEFAULT_COMMENT_LIMIT,
    include_comments: bool = True,
) -> Dict[str, Any]:
    """Fetch current public Instagram reel/post metadata + ranked top comments.

    Metadata (caption, counts, uploader) comes from yt-dlp. Comments come from
    Instagram's web comments API in ranked ("Top") order, top-level only, each
    with a preview of its reply thread. Requires `INSTAGRAM_COOKIES_FILE`; if the
    comments call fails the post metadata is still returned with an empty
    `comments` list and a `comments_error` explaining why.
    """
    if not _is_instagram_reel_url(url):
        raise ValueError(
            f"Not an Instagram reel/post URL: {url!r}. "
            "Expected https://www.instagram.com/reel/<id>/ or /p/<id>/"
        )

    max_comments = max(0, min(int(max_comments), _MAX_COMMENT_LIMIT))
    cookie_path = _resolve_cookie_path()
    info = _fetch_metadata(url, cookie_path)

    caption = info.get("description") or ""
    comment_count = info.get("comment_count")
    comments: List[Dict[str, Any]] = []
    comments_error: Optional[str] = None

    if include_comments and max_comments > 0:
        shortcode = _shortcode_from_url(info.get("webpage_url") or url)
        if not cookie_path:
            comments_error = "INSTAGRAM_COOKIES_FILE not set; comments require a logged-in session"
        elif not shortcode:
            comments_error = "could not determine shortcode from URL"
        else:
            try:
                comments, api_total = _fetch_comments_via_web_api(
                    shortcode=shortcode,
                    cookie_path=cookie_path,
                    max_comments=max_comments,
                    referer=info.get("webpage_url") or url,
                )
                if api_total is not None:
                    comment_count = api_total
            except (urllib.error.HTTPError, urllib.error.URLError) as e:
                comments_error = f"comments fetch failed: {e}"
                logger.warning("Instagram comments fetch failed for %s: %s", url, e)

    result = {
        "id": info.get("id"),
        "url": info.get("webpage_url") or url,
        "caption": caption,
        "hashtags": _extract_hashtags(caption),
        "uploader": info.get("uploader"),
        "uploader_id": info.get("uploader_id"),
        "like_count": info.get("like_count"),
        "view_count": info.get("view_count"),
        "comment_count": comment_count,
        "timestamp": info.get("timestamp"),
        "comments": comments,
        "comments_returned": len(comments),
    }
    if comments_error:
        result["comments_error"] = comments_error
    return result
