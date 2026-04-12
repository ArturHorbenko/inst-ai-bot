import os
import logging
from typing import Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor

from .config import get_config
from .db import get_mongodb_connection
from .scene_detect import detect_and_save_scenes
from .ocr import get_captions
from .captioning import generate_scene_description
from .transcription import extract_transcription
from .matching import match_transcription_to_scenes
from .summarizer import summarize_scenes

logger = logging.getLogger(__name__)


def analyze_multimodal(video_path: str, video_id: str = None, job_manager=None) -> Dict[str, Any]:
    """
    Analyze video using TwelveLabs multimodal API.
    Handles both cases: existing video_id or automatic upload+indexing.

    Args:
        video_path: Path to video file
        video_id: TwelveLabs video ID for the uploaded video (optional)
        job_manager: JobManager instance for database updates (optional)

    Returns:
        Dict containing multimodal analysis results
    """
    config = get_config()

    try:
        if not config.ENABLE_MULTIMODAL_ANALYSIS:
            return {
                "analysis_type": "multimodal",
                "status": "error",
                "error": "Multimodal analysis is disabled by server configuration"
            }

        from .multimodal import generate_summary, extract_summary_text

        # Case 1: video_id provided - use existing video
        if video_id:
            summary_result = generate_summary(config.TWELVE_LABS_API_KEY, video_id)

            return {
                "analysis_type": "multimodal",
                "status": "completed",
                "results": {
                    "summary": extract_summary_text(summary_result),
                    "video_id": video_id
                }
            }

        # Case 2: No video_id provided - check for existing upload or upload new
        else:
            # Validate video format
            from .config import validate_video_format
            if not validate_video_format(video_path, config):
                return {
                    "analysis_type": "multimodal",
                    "status": "error",
                    "error": f"Unsupported video format. Supported formats: {config.SUPPORTED_VIDEO_FORMATS}"
                }

            # Check if this filename was already uploaded to TwelveLabs
            filename = os.path.basename(video_path)
            existing_job = None

            if job_manager:
                existing_job = job_manager.get_video_by_filename(filename)

            if existing_job and existing_job.get("twelve_labs_video_id"):
                logger.info(f"Found existing TwelveLabs video for filename: {filename}")
                existing_video_id = existing_job["twelve_labs_video_id"]

                # Generate summary using existing video_id
                from .multimodal import generate_summary, extract_summary_text
                summary_result = generate_summary(config.TWELVE_LABS_API_KEY, existing_video_id)

                return {
                    "analysis_type": "multimodal",
                    "status": "completed",
                    "results": {
                        "summary": extract_summary_text(summary_result),
                        "video_id": existing_video_id,
                        "index_id": existing_job.get("twelve_labs_index_id"),
                        "task_id": existing_job.get("twelve_labs_task_id"),
                        "index_was_created": False,
                        "reused_existing_upload": True
                    }
                }

            # No existing upload found - start upload process and return immediately
            logger.info(f"No existing upload found for {filename}, starting upload process")
            current_job_id = getattr(job_manager, "current_job_id", None) if job_manager else None

            # Start the upload process asynchronously
            try:
                from .config import validate_video_format
                if not validate_video_format(video_path, config):
                    return {
                        "analysis_type": "multimodal",
                        "status": "error",
                        "error": f"Unsupported video format. Supported formats: {config.SUPPORTED_VIDEO_FORMATS}"
                    }

                # Get or create index first (this is quick)
                from .multimodal import create_or_get_index
                final_index_id, was_created = create_or_get_index(
                    config.TWELVE_LABS_API_KEY,
                    config.TWELVE_LABS_INDEX_NAME,
                    config.TWELVE_LABS_INDEX_ID
                )

                # Update job with index info and set status to indicate upload started
                if job_manager and current_job_id:
                    job_manager.update_twelve_labs_metadata(
                        job_id=current_job_id,
                        index_id=final_index_id,
                        indexing_status="uploading"
                    )

                # Return immediately - the background process will continue
                return {
                    "analysis_type": "multimodal",
                    "status": "processing",
                    "results": {
                        "message": "Video upload started - check status for progress",
                        "index_id": final_index_id,
                        "index_was_created": was_created,
                        "reused_existing_upload": False
                    }
                }

            except Exception as e:
                logger.error(f"Error starting upload process: {e}")
                return {
                    "analysis_type": "multimodal",
                    "status": "error",
                    "error": str(e)
                }

    except Exception as e:
        return {
            "analysis_type": "multimodal",
            "status": "error",
            "error": str(e)
        }


def analyze_gemini(video_path: str, video_id: str = None, job_manager=None) -> Dict[str, Any]:
    """
    Analyze video using Google Gemini multimodal API.
    Synchronous — returns results directly, no indexing step.

    Args:
        video_path: Path to video file
        video_id: Unused, kept for interface compatibility
        job_manager: Unused, kept for interface compatibility

    Returns:
        Dict containing Gemini analysis results
    """
    config = get_config()

    try:
        from .config import validate_video_format
        if not validate_video_format(video_path, config):
            return {
                "analysis_type": "gemini",
                "status": "error",
                "error": f"Unsupported video format. Supported formats: {config.SUPPORTED_VIDEO_FORMATS}"
            }

        # Run Groq Whisper transcription first to improve VO accuracy
        transcript = None
        try:
            logger.info("Running Groq Whisper transcription before Gemini analysis...")
            transcript = extract_transcription(video_path, api_key=config.GROQ_API_KEY)
            logger.info(f"Groq transcription complete: {len(transcript)} segments")
        except Exception as e:
            logger.warning(f"Groq transcription failed, proceeding without transcript: {e}")

        result = analyze_video_gemini(config.GEMINI_API_KEY, video_path, transcript)

        return {
            "analysis_type": "gemini",
            "status": "completed",
            "results": {
                "summary": result["summary"],
            }
        }

    except Exception as e:
        return {
            "analysis_type": "gemini",
            "status": "error",
            "error": str(e)
        }


def analyze_structured(video_path: str) -> Dict[str, Any]:
    """
    Analyze video using complete structured pipeline.
    Runs: scene detection → OCR → captioning → transcription → matching → summarization

    Args:
        video_path: Path to video file

    Returns:
        Dict containing structured analysis results
    """
    config = get_config()

    try:
        # Create output directory if it doesn't exist
        os.makedirs(config.IMAGE_DIR, exist_ok=True)

        # Run transcription and scene processing in parallel
        with ThreadPoolExecutor(max_workers=2) as executor:
            # Start transcription extraction
            transcription_future = executor.submit(extract_transcription, video_path)

            # Start scene detection and processing
            scene_future = executor.submit(_process_scenes_structured, video_path, config.IMAGE_DIR)

            # Get results
            transcription_result = transcription_future.result()
            scenes_result = scene_future.result()

        # Match transcription to scenes
        if transcription_result:
            scenes_result = match_transcription_to_scenes(scenes_result, transcription_result)

        # Generate structured summary
        summary = summarize_scenes(scenes_result)

        return {
            "analysis_type": "structured",
            "status": "completed",
            "results": {
                "scenes": scenes_result,
                "transcription": transcription_result,
                "structured_summary": summary
            }
        }

    except Exception as e:
        return {
            "analysis_type": "structured",
            "status": "error",
            "error": str(e)
        }


def _process_scenes_structured(video_path: str, images_dir: str) -> list:
    """
    Internal function to process scenes for structured analysis.
    """
    # Detect scenes and save frames
    scene_timestamps = detect_and_save_scenes(video_path)

    # Extract text from scenes using OCR
    scenes = get_captions(images_dir, scene_timestamps)

    # Generate AI descriptions for scenes
    scenes = generate_scene_description(images_dir, scenes)

    return scenes
