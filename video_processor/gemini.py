import time
import logging
from typing import Optional
from google import genai

logger = logging.getLogger(__name__)


def call_gemini(
    api_key: str,
    video_path: str,
    prompt: str,
    model: str = "gemini-2.5-pro",
    gemini_file_ref: Optional[str] = None,
) -> tuple[str, str]:
    """
    Run a prompt against a video using the Gemini Files API.
    Returns (output_text, file_ref). Cache file_ref on the artifact for subsequent runs.
    """
    client = genai.Client(api_key=api_key)

    video_file = _get_or_upload(client, video_path, gemini_file_ref)

    logger.info(f"Calling {model}...")
    response = client.models.generate_content(
        model=model,
        contents=[video_file, prompt],
    )

    return response.text, video_file.name


def _get_or_upload(client: genai.Client, video_path: str, gemini_file_ref: Optional[str]):
    """Reuse an existing Gemini file if still ACTIVE, otherwise upload fresh."""
    if gemini_file_ref:
        try:
            existing = client.files.get(name=gemini_file_ref)
            if existing.state.name == "ACTIVE":
                logger.info(f"Reusing cached Gemini file: {gemini_file_ref}")
                return existing
            logger.info(f"Cached Gemini file {gemini_file_ref} is not ACTIVE ({existing.state.name}), re-uploading")
        except Exception as e:
            logger.warning(f"Could not retrieve cached Gemini file {gemini_file_ref}: {e}, re-uploading")

    import os
    file_size = os.path.getsize(video_path)
    logger.info(f"Uploading to Gemini Files API: {video_path} ({file_size / (1024 * 1024):.1f}MB)")
    video_file = client.files.upload(file=video_path)

    while video_file.state.name == "PROCESSING":
        logger.info("Waiting for Gemini file to become ACTIVE...")
        time.sleep(5)
        video_file = client.files.get(name=video_file.name)

    if video_file.state.name != "ACTIVE":
        raise RuntimeError(f"Gemini file entered unexpected state: {video_file.state.name}")

    logger.info(f"Gemini file ready: {video_file.name}")
    return video_file
