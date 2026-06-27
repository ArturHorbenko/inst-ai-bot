import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

logger = logging.getLogger(__name__)

_ALLOWED_HOSTS = {"instagram.com", "www.instagram.com"}
_HASHTAG_RE = re.compile(r"#([\w_]+)", re.UNICODE)
_COMMENT_LIMIT = 10


def _resolve_cookie_path() -> Optional[Path]:
    """Return the configured Instagram cookie file, validating it exists.

    Instagram serves logged-out clients an empty media response, so both the
    download path and the post-status path need a logged-in session cookie.
    """
    cookies_file = os.environ.get("INSTAGRAM_COOKIES_FILE", "").strip()
    if not cookies_file:
        return None
    cookie_path = Path(cookies_file).expanduser()
    if not cookie_path.exists():
        raise ValueError(f"INSTAGRAM_COOKIES_FILE does not exist: {cookie_path}")
    return cookie_path


def _is_instagram_reel_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.netloc.lower() not in _ALLOWED_HOSTS:
        return False
    path = parsed.path.lower()
    return "/reel/" in path or "/reels/" in path or "/p/" in path


def _extract_hashtags(text: str) -> List[str]:
    if not text:
        return []
    seen = set()
    tags = []
    for match in _HASHTAG_RE.findall(text):
        lowered = match.lower()
        if lowered not in seen:
            seen.add(lowered)
            tags.append(match)
    return tags


def _extract_top_comments(info: Dict[str, Any], limit: int = _COMMENT_LIMIT) -> List[Dict[str, Any]]:
    raw = info.get("comments") or []
    out = []
    for c in raw[:limit]:
        text = (c.get("text") or "").strip()
        if not text:
            continue
        out.append({
            "author": c.get("author"),
            "text": text,
            "timestamp": c.get("timestamp"),
            "like_count": c.get("like_count"),
        })
    return out


def _build_metadata(info: Dict[str, Any]) -> Dict[str, Any]:
    caption = info.get("description") or ""
    return {
        "caption": caption,
        "hashtags": _extract_hashtags(caption),
        "title": info.get("title"),
        "uploader": info.get("uploader"),
        "uploader_id": info.get("uploader_id"),
        "uploader_url": info.get("uploader_url"),
        "webpage_url": info.get("webpage_url"),
        "duration": info.get("duration"),
        "view_count": info.get("view_count"),
        "like_count": info.get("like_count"),
        "comment_count": info.get("comment_count"),
        "timestamp": info.get("timestamp"),
        "comments": _extract_top_comments(info),
    }


def download_instagram_reel(url: str, dest_dir: Path) -> Tuple[Path, Dict[str, Any]]:
    """Download an Instagram reel via yt-dlp; return (video_path, metadata)."""
    if not _is_instagram_reel_url(url):
        raise ValueError(
            f"Not an Instagram reel URL: {url!r}. "
            "Expected https://www.instagram.com/reel/<id>/ or /p/<id>/"
        )

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    ydl_opts = {
        "outtmpl": str(dest_dir / "%(id)s.%(ext)s"),
        "format": "mp4/bestvideo*+bestaudio/best",
        "merge_output_format": "mp4",
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "getcomments": True,
    }
    cookie_path = _resolve_cookie_path()
    if cookie_path:
        ydl_opts["cookiefile"] = str(cookie_path)

    logger.info(f"Downloading Instagram reel: {url}")
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except DownloadError as e:
        raise RuntimeError(f"yt-dlp failed to download {url}: {e}") from e

    mp4s = sorted(dest_dir.glob("*.mp4"))
    if not mp4s:
        raise RuntimeError(f"Download finished but no .mp4 produced in {dest_dir}")

    video_path = mp4s[0]
    size_mb = video_path.stat().st_size / (1024 * 1024)
    metadata = _build_metadata(info or {})
    logger.info(
        f"Downloaded reel to {video_path} ({size_mb:.1f}MB); "
        f"caption={'yes' if metadata['caption'] else 'no'}, "
        f"hashtags={len(metadata['hashtags'])}, "
        f"comments={len(metadata['comments'])}"
    )
    return video_path, metadata
