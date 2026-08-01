import hashlib
import os
import shutil
import tempfile
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Union

from .downloader import download_instagram_reel
from .transcription import extract_transcription
from .store import ArtifactStore, UrlCacheStore

logger = logging.getLogger(__name__)

_URL_PREFIXES = ("http://", "https://")


def index_video(
    source: Union[str, Path],
    config,
    artifact_store: ArtifactStore,
    url_cache: UrlCacheStore,
    source_url: Optional[str] = None,
    source_type: Optional[str] = None,
    source_metadata: Optional[dict[str, Any]] = None,
    source_fetcher: Optional[str] = None,
) -> dict:
    """
    Index a video from a URL or local file path. Idempotent — same content returns existing artifact.
    """
    source = str(source)
    is_url = source.startswith(_URL_PREFIXES)

    if is_url:
        cached_hash = url_cache.get(source)
        if cached_hash:
            artifact = artifact_store.get_by_hash(cached_hash)
            if artifact:
                logger.info(f"Cache hit for URL {source}: {cached_hash}")
                return artifact

    temp_dir = Path(tempfile.mkdtemp(prefix="inst_ai_index_"))
    try:
        if is_url:
            video_path, source_metadata = download_instagram_reel(source, temp_dir)
            platform = _detect_platform(source)
            provenance_url = source
            fetcher = "yt-dlp"
        else:
            video_path = Path(source)
            source_metadata = source_metadata or {}
            platform = source_type or "upload"
            provenance_url = source_url
            fetcher = source_fetcher or ("provided_upload" if source_url else None)

        content_hash = _hash_file(video_path)

        existing = artifact_store.get_by_hash(content_hash)
        if existing:
            logger.info(f"Artifact already exists for hash {content_hash}")
            if provenance_url:
                _append_source_if_new(artifact_store, existing, provenance_url, platform, source_metadata, fetcher)
            if is_url:
                url_cache.put(source, content_hash)
            return artifact_store.get_by_hash(content_hash)

        dest_dir = Path(config.VIDEO_DIR)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / f"{content_hash}.mp4"
        shutil.copy2(video_path, dest_path)
        logger.info(f"Stored video at {dest_path}")

        logger.info("Transcribing...")
        segments = extract_transcription(str(video_path), api_key=config.GROQ_API_KEY)
        full_text = " ".join(s["text"].strip() for s in segments)

        duration_sec = _get_duration(video_path)

        artifact = {
            "content_hash": content_hash,
            "video_file_ref": str(dest_path),
            "duration_sec": duration_sec,
            "transcript": {
                "text": full_text,
                "segments": segments,
                "model": "whisper-large-v3-groq",
            },
            "sources": [_build_source(provenance_url, platform, source_metadata, fetcher)],
            "gemini_file_ref": None,
            "indexed_at": datetime.now(timezone.utc),
            "schema_version": 1,
        }

        stored = artifact_store.upsert(artifact)
        if is_url:
            url_cache.put(source, content_hash)

        logger.info(f"Indexed artifact {content_hash}")
        return stored

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def _get_duration(path: Path) -> float:
    try:
        import subprocess
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        import json
        data = json.loads(result.stdout)
        return float(data["format"].get("duration", 0))
    except Exception:
        return 0.0


def _detect_platform(url: str) -> str:
    if "instagram.com" in url:
        return "instagram_reel"
    if "tiktok.com" in url:
        return "tiktok"
    if "youtube.com" in url or "youtu.be" in url:
        return "youtube"
    return "url"


def _build_source(url, platform: str, metadata: dict, fetcher: Optional[str] = None) -> dict:
    return {
        "type": platform,
        "url": url,
        "fetched_at": datetime.now(timezone.utc),
        "fetcher": fetcher or ("yt-dlp" if url else None),
        "metadata": metadata or {},
    }


def _append_source_if_new(
    artifact_store: ArtifactStore,
    artifact: dict,
    url: str,
    platform: str,
    metadata: dict,
    fetcher: Optional[str] = None,
):
    existing_urls = {s.get("url") for s in artifact.get("sources", [])}
    if url not in existing_urls:
        artifact_store.append_source(content_hash=artifact["content_hash"], source=_build_source(url, platform, metadata, fetcher))
