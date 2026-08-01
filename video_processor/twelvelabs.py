"""TwelveLabs (Pegasus) prompt provider.

Mirrors `video_processor/gemini.py`: lazily uploads the artifact's video into a
TwelveLabs index on first run, caches the resulting `video_id` on the artifact,
then runs prompts via the Pegasus analyze endpoint.

Unlike Gemini's Files API, TwelveLabs requires a video to be indexed (a
background task) before it can be prompted. The first run pays that cost; later
runs reuse the cached `twelvelabs_video_id` and skip straight to analyze.
"""
import logging
import os
from typing import Any, Optional

try:
    from twelvelabs import TwelveLabs
except ImportError:  # pragma: no cover - exercised in environments without SDK installed
    TwelveLabs = None

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "pegasus1.5"
# Model + modalities enabled on the index so Pegasus analysis can run.
_INDEX_MODEL = "pegasus1.5"
_INDEX_MODEL_OPTIONS = ["visual", "audio"]


def call_twelvelabs(
    api_key: str,
    video_path: str,
    prompt: str,
    model: str = DEFAULT_MODEL,
    twelvelabs_video_id: Optional[str] = None,
    index_name: str = "default-index",
    index_id: Optional[str] = None,
    poll_interval: int = 10,
) -> tuple[str, str]:
    """
    Run a prompt against a video using the TwelveLabs Pegasus analyze endpoint.
    Returns (output_text, twelvelabs_video_id). Cache the video_id on the
    artifact so subsequent runs skip re-uploading and re-indexing.
    """
    if not api_key:
        raise RuntimeError("TWELVE_LABS_API_KEY is not configured")

    model = model or DEFAULT_MODEL
    if TwelveLabs is None:
        raise RuntimeError(
            "twelvelabs SDK is not installed. Install project requirements before using TwelveLabs provider."
        )
    client = TwelveLabs(api_key=api_key)

    video_id = _get_or_index(
        client, video_path, twelvelabs_video_id, index_name, index_id, poll_interval
    )

    logger.info(f"Calling TwelveLabs analyze ({model}) for video {video_id}...")
    analyze_kwargs: dict[str, Any] = {"prompt": prompt, "model_name": model}
    if model.startswith("pegasus1.5"):
        # Pegasus 1.5 uses the newer `video` context shape. The task API still
        # returns this value as `video_id`; for the analyze API it is an asset id.
        analyze_kwargs["video"] = {"type": "asset_id", "asset_id": video_id}
    else:
        # Pegasus 1.2 supports the legacy video_id parameter.
        analyze_kwargs["video_id"] = video_id
    response = client.analyze(**analyze_kwargs)

    return response.data or "", video_id


def _get_or_index(
    client: Any,
    video_path: str,
    twelvelabs_video_id: Optional[str],
    index_name: str,
    index_id: Optional[str],
    poll_interval: int,
) -> str:
    """Reuse an existing TwelveLabs video_id if cached, otherwise upload and index."""
    if twelvelabs_video_id:
        logger.info(f"Reusing cached TwelveLabs video: {twelvelabs_video_id}")
        return twelvelabs_video_id

    resolved_index = index_id or _resolve_index_id(client, index_name)

    file_size = os.path.getsize(video_path)
    logger.info(
        f"Uploading to TwelveLabs index {resolved_index}: "
        f"{video_path} ({file_size / (1024 * 1024):.1f}MB)"
    )
    task = client.tasks.create(index_id=resolved_index, video_file=video_path)
    task = client.tasks.wait_for_done(task_id=task.id, sleep_interval=poll_interval)

    if task.status != "ready":
        raise RuntimeError(f"TwelveLabs indexing task ended in state: {task.status}")
    if not task.video_id:
        raise RuntimeError("TwelveLabs indexing task finished without a video_id")

    logger.info(f"TwelveLabs video ready: {task.video_id}")
    return task.video_id


def _resolve_index_id(client: Any, index_name: str) -> str:
    """Find a TwelveLabs index by name, creating a Pegasus-enabled one if absent."""
    for index in client.indexes.list(index_name=index_name):
        if index.index_name == index_name:
            logger.info(f"Using existing TwelveLabs index '{index_name}': {index.id}")
            return index.id

    logger.info(f"Creating TwelveLabs index '{index_name}' (model {_INDEX_MODEL})")
    created = client.indexes.create(
        index_name=index_name,
        models=[{"model_name": _INDEX_MODEL, "model_options": _INDEX_MODEL_OPTIONS}],
    )
    return created.id
