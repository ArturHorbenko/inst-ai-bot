"""Index authorized image uploads as runner-compatible artifacts."""

import hashlib
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from PIL import Image, ImageOps

from .indexer import _append_source_if_new, _build_source
from .store import ArtifactStore, UrlCacheStore

logger = logging.getLogger(__name__)

MIN_IMAGES = 1
MAX_IMAGES = 10
SUPPORTED_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp"})
MAX_TILE_SIZE = 1024


def validate_image_paths(image_paths: Sequence[Path | str]) -> list[Path]:
    """Validate the supported, non-empty image upload set before indexing it."""
    paths = [Path(path) for path in image_paths]
    if not MIN_IMAGES <= len(paths) <= MAX_IMAGES:
        raise ValueError(f"Provide between {MIN_IMAGES} and {MAX_IMAGES} images")
    for path in paths:
        if path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
            raise ValueError(f"Unsupported image format: {path.suffix}")
        try:
            with Image.open(path) as image:
                image.verify()
        except (OSError, ValueError) as exc:
            raise ValueError(f"Invalid image file: {path.name}") from exc
    return paths


def hash_ordered_image_bytes(image_paths: Iterable[Path | str]) -> str:
    """Hash original upload bytes with positions and lengths, preserving slide order."""
    digest = hashlib.sha256()
    for index, image_path in enumerate(image_paths):
        data = Path(image_path).read_bytes()
        digest.update(index.to_bytes(2, "big"))
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return f"sha256:{digest.hexdigest()}"


def create_contact_sheet(image_paths: Sequence[Path | str], output_path: Path | str) -> Path:
    """Normalize one image or render an ordered carousel into a deterministic PNG."""
    paths = validate_image_paths(image_paths)
    normalized = [_normalized_image(path) for path in paths]
    try:
        tile_width = max(image.width for image in normalized)
        tile_height = max(image.height for image in normalized)
        columns = 1 if len(normalized) == 1 else math.ceil(math.sqrt(len(normalized)))
        rows = math.ceil(len(normalized) / columns)
        sheet = Image.new("RGB", (columns * tile_width, rows * tile_height), "white")
        for index, image in enumerate(normalized):
            x = (index % columns) * tile_width + (tile_width - image.width) // 2
            y = (index // columns) * tile_height + (tile_height - image.height) // 2
            sheet.paste(image, (x, y))
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(destination, format="PNG", optimize=False, compress_level=9)
        return destination
    finally:
        for image in normalized:
            image.close()


def index_images(
    image_paths: Sequence[Path | str],
    config,
    artifact_store: ArtifactStore,
    url_cache: Optional[UrlCacheStore] = None,
    source_url: Optional[str] = None,
    source_type: Optional[str] = None,
    source_metadata: Optional[dict[str, Any]] = None,
    source_fetcher: Optional[str] = None,
) -> dict:
    """Store an image or image carousel as an idempotent runner-compatible artifact."""
    paths = validate_image_paths(image_paths)
    content_hash = hash_ordered_image_bytes(paths)
    platform = source_type or "upload"
    image_metadata = {
        **(source_metadata or {}),
        "image_upload": {
            "count": len(paths),
            "filenames": [path.name for path in paths],
            "representation": "contact_sheet" if len(paths) > 1 else "normalized_image",
        },
    }

    existing = artifact_store.get_by_hash(content_hash)
    if existing:
        logger.info("Image artifact already exists for hash %s", content_hash)
        if source_url or source_metadata:
            _append_source_if_new(
                artifact_store, existing, source_url, platform, image_metadata,
                source_fetcher or "provided_upload",
            )
        return artifact_store.get_by_hash(content_hash) or existing

    destination = Path(config.VIDEO_DIR) / f"{content_hash}.png"
    create_contact_sheet(paths, destination)
    artifact = {
        "content_hash": content_hash,
        "video_file_ref": str(destination),
        "duration_sec": 0.0,
        "transcript": {"text": "", "segments": [], "model": None},
        "sources": [_build_source(
            source_url, platform, image_metadata, source_fetcher or "provided_upload"
        )],
        "gemini_file_ref": None,
        "indexed_at": datetime.now(timezone.utc),
        "schema_version": 1,
    }
    stored = artifact_store.upsert(artifact)
    logger.info("Indexed image artifact %s", content_hash)
    return stored


def _normalized_image(path: Path) -> Image.Image:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    image.thumbnail((MAX_TILE_SIZE, MAX_TILE_SIZE), Image.Resampling.LANCZOS)
    return image
