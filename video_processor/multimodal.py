from twelvelabs import TwelveLabs
from google import genai
from google.genai import types
from pydantic import BaseModel
import time
import os
import mimetypes
import logging
from typing import Dict, Any, Optional, Tuple, Iterable, List
import requests
from requests.exceptions import RequestException, ConnectionError, Timeout

logger = logging.getLogger(__name__)

# Shared prompt for both Twelve Labs and Gemini
ANALYSIS_PROMPT = """
Analyze an Instagram reel from a tech blogger influencer and provide a Comprehensive Description. Include the following details: Describe the reel's purpose, main topics, and target audience, explaining what it communicates and its context in the tech niche. Detail the visual elements, including the setting, objects, gadgets, and notable effects or transitions. Summarize the spoken content or dialogues, highlighting key phrases, quotes, and points emphasized by the influencer. Explain the narrative flow, describing how the content progresses from start to finish, including the opening, main segments, and conclusion. Identify and describe any calls to action, such as encouraging viewers to like, comment, follow, or click links. Analyze the influencer's persona, including tone, style, personality traits, and engagement with their audience.
"""

# ─── Twelve Labs ─────────────────────────────────────────────────────────────

prompt="""
Analyze an Instagram reel from a tech blogger influencer and provide a Comprehensive Description. Include the following details: Describe the reel's purpose, main topics, and target audience, explaining what it communicates and its context in the tech niche. Detail the visual elements, including the setting, objects, gadgets, and notable effects or transitions. Summarize the spoken content or dialogues, highlighting key phrases, quotes, and points emphasized by the influencer. Explain the narrative flow, describing how the content progresses from start to finish, including the opening, main segments, and conclusion. Identify and describe any calls to action, such as encouraging viewers to like, comment, follow, or click links. Analyze the influencer's persona, including tone, style, personality traits, and engagement with their audience. Output the results in the following JSON format: {"content_overview": "Description of the reel's purpose, topics, and target audience.","key_visual_elements": "Details of setting, objects, effects, and transitions.","spoken_content_and_dialogues": "Summary of spoken content with key phrases or quotes.","narrative_flow": "How the reel's content progresses.","calls_to_action": "Details on any calls to action.","influencer_persona": "Analysis of the influencer's tone, style, and engagement."} Ensure the JSON is valid, contains no formatting or new line characters, and includes as much detail as possible for each field.
"""

def _extract_id(value: Any, *keys: str) -> Optional[str]:
    if not value:
        return None
    if isinstance(value, dict):
        for key in keys:
            result = value.get(key)
            if result:
                return str(result)
        return None
    for key in keys:
        if hasattr(value, key):
            result = getattr(value, key)
            if result:
                return str(result)
    return None


def _extract_page_data(page: Any) -> Iterable[Any]:
    if page is None:
        return []
    if isinstance(page, list):
        return page
    if isinstance(page, dict):
        for key in ("data", "items", "indexes", "results"):
            value = page.get(key)
            if isinstance(value, list):
                return value
        return []
    if hasattr(page, "data"):
        data = getattr(page, "data")
        if isinstance(data, list):
            return data
    for attr in ("items", "indexes", "results"):
        if hasattr(page, attr):
            value = getattr(page, attr)
            if isinstance(value, list):
                return value
    return []


def _extract_index_name(value: Any) -> Optional[str]:
    return _extract_id(value, "index_name", "name")


def _find_existing_index_id(client: TwelveLabs, index_name: str) -> Optional[str]:
    """
    Try multiple list query shapes and response shapes to find an index by name.
    """
    candidates = []
    for kwargs in ({"index_name": index_name}, {"name": index_name}, {}):
        try:
            candidates.append(client.indexes.list(**kwargs))
        except TypeError:
            continue
        except Exception as e:
            logger.warning(f"Index list query failed for {kwargs}: {e}")

    for page in candidates:
        for existing_index in _extract_page_data(page):
            existing_index_name = _extract_index_name(existing_index)
            existing_index_id = _extract_id(existing_index, "id", "index_id")
            if existing_index_name == index_name and existing_index_id:
                return str(existing_index_id)
    return None


def generate_summary(api_key, video_id):
    logger.info("Generating summary for video: " + video_id)
    client = TwelveLabs(api_key=api_key)
    res = None
    try:
        res = client.analyze(video_id=video_id, prompt=prompt)
        logger.info(f"Summary generation completed. Result type: {type(res)}")
        if res:
            # Log available attributes for debugging
            logger.info(f"Available attributes: {[attr for attr in dir(res) if not attr.startswith('_')]}")
    except Exception as e:
        logger.error(f"Error happened in generate_summary: {e}")

    return res


def extract_summary_text(summary_result) -> str:
    """
    Safely extract summary text from TwelveLabs result object.

    Args:
        summary_result: TwelveLabs generate result object

    Returns:
        Summary text as string
    """
    if not summary_result:
        return None

    # First check for common response payload locations.
    if hasattr(summary_result, "data"):
        data = getattr(summary_result, "data")
        if isinstance(data, str):
            return data
        if isinstance(data, dict):
            for key in ["text", "summary", "output", "content", "result"]:
                if data.get(key):
                    return str(data[key])
        if isinstance(data, list) and data:
            first = data[0]
            text_value = _extract_id(first, "text", "summary", "output", "content", "result")
            if text_value:
                return text_value

    # Try direct attributes.
    for attr in ['text', 'content', 'summary', 'output', 'result']:
        if hasattr(summary_result, attr):
            value = getattr(summary_result, attr)
            if value:
                return str(value)

    # Fallback to string representation
    return str(summary_result)


def create_or_get_index(api_key: str, index_name: str = "default-index", index_id: str = None) -> Tuple[str, bool]:
    """
    Create a new index or get existing index for TwelveLabs.

    Args:
        api_key: TwelveLabs API key
        index_name: Name for the index (used when creating new)
        index_id: Existing index ID to use (if provided, skips creation)

    Returns:
        Tuple of (index_id, was_created)
    """
    client = TwelveLabs(api_key=api_key)

    # If index_id is provided, use it directly.
    if index_id:
        try:
            client.indexes.retrieve(index_id)
            logger.info(f"Using existing index: {index_id}")
            return index_id, False
        except Exception as e:
            logger.error(f"Failed to retrieve existing index {index_id}: {e}")
            raise

    # Reuse an index with matching name if present.
    existing_index_id = _find_existing_index_id(client, index_name)
    if existing_index_id:
        logger.info(f"Using existing index by name: {existing_index_id}")
        return existing_index_id, False

    # Create new index with both models.
    try:
        index = client.indexes.create(
            index_name=index_name,
            models=[
                {
                    "model_name": "marengo2.7",
                    "model_options": ["visual", "audio"]
                },
                {
                    "model_name": "pegasus1.2",
                    "model_options": ["visual", "audio"]
                }
            ]
        )
        created_index_id = _extract_id(index, "id", "index_id")
        if not created_index_id:
            raise RuntimeError("Unable to determine created index ID")
        logger.info(f"Created new index: {created_index_id}")
        return created_index_id, True
    except Exception as e:
        # If a concurrent request created the index, recover by listing and reusing it.
        error_str = str(e).lower()
        if "index_name_already_exists" in error_str or "already exists" in error_str:
            existing_index_id = _find_existing_index_id(client, index_name)
            if existing_index_id:
                logger.info(f"Recovered existing index after conflict: {existing_index_id}")
                return existing_index_id, False
        logger.error(f"Failed to create index: {e}")
        raise


def upload_video_for_indexing(api_key: str, video_path: str, index_id: str, max_retries: int = 3) -> Tuple[str, Optional[str]]:
    """
    Upload video to TwelveLabs for indexing with retry logic.

    Args:
        api_key: TwelveLabs API key
        video_path: Path to video file
        index_id: TwelveLabs index ID
        max_retries: Maximum number of retry attempts

    Returns:
        Tuple of (indexed_asset_id, task_id_if_available)
    """
    client = TwelveLabs(api_key=api_key)

    # Validate video file exists and size
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")

    file_size = os.path.getsize(video_path)
    if file_size == 0:
        raise ValueError(f"Video file is empty: {video_path}")

    # Size limit check (TwelveLabs has file size limits)
    max_size_mb = 5000  # 5GB limit
    if file_size > max_size_mb * 1024 * 1024:
        raise ValueError(f"Video file too large: {file_size / (1024*1024):.1f}MB > {max_size_mb}MB")

    # Upload once, then retry only the indexing step.
    filename = os.path.basename(video_path)
    content_type = mimetypes.guess_type(video_path)[0] or "application/octet-stream"
    logger.info(f"Uploading video: {video_path}")
    logger.info(f"File size: {file_size / (1024*1024):.1f}MB")
    logger.info(f"Using index ID: {index_id}")
    with open(video_path, "rb") as video_file:
        asset = client.assets.create(
            method="direct",
            file=(filename, video_file, content_type),
            filename=filename
        )
    asset_id = _extract_id(asset, "id", "asset_id")
    if not asset_id:
        raise RuntimeError("Unable to determine uploaded asset ID")
    logger.info(f"Uploaded asset created: asset_id={asset_id}, filename={filename}")

    for attempt in range(max_retries):
        try:
            logger.info(f"Starting indexing (attempt {attempt + 1}/{max_retries}) for asset_id={asset_id}")
            index_response = client.indexes.indexed_assets.create(
                index_id=index_id,
                asset_id=asset_id
            )

            indexed_asset_id = _extract_id(index_response, "indexed_asset_id", "id")
            task_id = _extract_id(index_response, "task_id")
            if not indexed_asset_id:
                raise RuntimeError("Unable to determine indexed asset ID")

            logger.info(
                f"Started video indexing: indexed_asset_id={indexed_asset_id}, "
                f"asset_id={asset_id}, task_id={task_id}"
            )
            return indexed_asset_id, task_id


        except (ConnectionError, Timeout, RequestException) as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt  # Exponential backoff
                logger.warning(f"Network error uploading video (attempt {attempt + 1}): {e}. Retrying in {wait_time}s...")
                time.sleep(wait_time)
                continue
            else:
                logger.error(f"Failed to start indexing after {max_retries} attempts: {e}")
                raise
        except ValueError as e:
            # Don't retry for API key validation errors
            logger.error(f"Configuration error: {e}")
            raise
        except Exception as e:
            # Handle API-specific errors (rate limits, quota exceeded, etc.)
            error_str = str(e).lower()
            if "rate limit" in error_str:
                if attempt < max_retries - 1:
                    wait_time = 60  # Wait 1 minute for rate limit
                    logger.warning(f"Rate limit hit (attempt {attempt + 1}): {e}. Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                else:
                    logger.error(f"Rate limit exceeded after {max_retries} attempts: {e}")
                    raise
            elif "unauthorized" in error_str or "authentication" in error_str:
                logger.error(f"Authentication error: {e}")
                raise ValueError(f"TwelveLabs API authentication failed: {e}")
            elif "not found" in error_str and "index" in error_str:
                logger.error(f"Index not found: {e}")
                raise ValueError(f"TwelveLabs index not found: {index_id}. Error: {e}")
            else:
                logger.error(f"Failed to start indexing for asset {asset_id}: {e}")
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    logger.warning(f"Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                else:
                    raise


def wait_for_indexing_completion(
    api_key: str,
    index_id: str,
    indexed_asset_id: str,
    job_manager=None,
    job_id: str = None,
    timeout: int = 1800,
    poll_interval: int = 10
) -> Tuple[bool, str]:
    """
    Wait for indexing task to complete with exponential backoff.

    Args:
        api_key: TwelveLabs API key
        index_id: Index ID
        indexed_asset_id: Indexed asset ID returned after index request
        timeout: Maximum time to wait in seconds (default: 30 minutes)
        poll_interval: Initial polling interval in seconds

    Returns:
        Tuple of (success, video_id_or_error)
    """
    client = TwelveLabs(api_key=api_key)

    start_time = time.time()
    current_interval = poll_interval

    while (time.time() - start_time) < timeout:
        try:
            indexed_asset = client.indexes.indexed_assets.retrieve(
                index_id=index_id,
                indexed_asset_id=indexed_asset_id
            )

            status = str(getattr(indexed_asset, "status", "")).lower()

            if status in {"ready", "completed"}:
                logger.info(
                    f"Indexing completed successfully: indexed_asset_id={indexed_asset_id}, "
                    f"asset_id={_extract_id(indexed_asset, 'asset_id')}"
                )
                # Update job status
                if job_manager and job_id:
                    job_manager.update_twelve_labs_metadata(
                        job_id=job_id,
                        video_id=indexed_asset_id,
                        indexing_status="ready"
                    )
                return True, indexed_asset_id
            elif status in {"failed", "error"}:
                error_msg = f"Indexing failed: {getattr(indexed_asset, 'error', 'Unknown error')}"
                logger.error(error_msg)
                # Update job status
                if job_manager and job_id:
                    job_manager.update_twelve_labs_metadata(
                        job_id=job_id,
                        indexing_status="failed"
                    )
                return False, error_msg
            elif status in ["pending", "queued", "validating", "running", "indexing"]:
                logger.info(f"Indexing in progress... Status: {status}")
                # Update job status with progress
                if job_manager and job_id:
                    job_manager.update_twelve_labs_metadata(
                        job_id=job_id,
                        indexing_status=status
                    )
                time.sleep(current_interval)
                # Exponential backoff with max 60 seconds
                current_interval = min(current_interval * 1.5, 60)
            else:
                logger.warning(f"Unknown indexing status: {status}")
                time.sleep(current_interval)

        except (ConnectionError, Timeout, RequestException) as e:
            logger.warning(f"Network error checking task status: {e}")
            time.sleep(current_interval)
        except Exception as e:
            if "rate limit" in str(e).lower():
                logger.warning(f"Rate limit checking task status: {e}")
                time.sleep(60)  # Wait 1 minute for rate limit
            else:
                logger.error(f"Error checking task status: {e}")
                time.sleep(current_interval)

    error_msg = f"Indexing timeout after {timeout} seconds"
    logger.error(error_msg)
    return False, error_msg


def get_video_analysis(api_key: str, video_path: str, index_id: str = None, index_name: str = "default-index", job_manager=None, job_id: str = None) -> Dict[str, Any]:
    """
    Comprehensive function that handles the complete workflow:
    upload → index → generate summary

    Args:
        api_key: TwelveLabs API key
        video_path: Path to video file
        index_id: Existing index ID (if None, creates new index)
        index_name: Name for new index (used if index_id is None)

    Returns:
        Dict containing analysis results and metadata
    """
    try:
        # Step 1: Get or create index
        logger.info("Getting or creating index...")
        final_index_id, was_created = create_or_get_index(api_key, index_name, index_id)

        # Update job with index info
        if job_manager and job_id:
            job_manager.update_twelve_labs_metadata(
                job_id=job_id,
                index_id=final_index_id,
                indexing_status="uploading"
            )

        # Step 2: Upload video for indexing
        logger.info("Uploading video for indexing...")
        indexed_asset_id, task_id = upload_video_for_indexing(api_key, video_path, final_index_id)


        # Update job with task info
        if job_manager and job_id:
            job_manager.update_twelve_labs_metadata(
                job_id=job_id,
                video_id=indexed_asset_id,
                task_id=task_id,
                indexing_status="pending"
            )

        # Step 3: Wait for indexing completion
        logger.info("Waiting for indexing completion...")
        success, video_id_or_error = wait_for_indexing_completion(
            api_key,
            final_index_id,
            indexed_asset_id,
            job_manager,
            job_id
        )


        if not success:
            return {
                "status": "failed",
                "error": video_id_or_error,
                "index_id": final_index_id,
                "task_id": task_id
            }

        video_id = video_id_or_error

        # Step 4: Generate summary
        logger.info("Generating summary...")
        summary_result = generate_summary(api_key, video_id)

        return {
            "status": "completed",
            "video_id": video_id,
            "index_id": final_index_id,
            "task_id": task_id,
            "index_was_created": was_created,
            "summary": extract_summary_text(summary_result)
        }

    except Exception as e:
        logger.error(f"Error in get_video_analysis: {e}")
        return {
            "status": "error",
            "error": str(e)
        }


# ─── Gemini ──────────────────────────────────────────────────────────────────

class ReelSection(BaseModel):
    label: str                             # Hook | Setup | Problem | Concept | Build | Demo | Feature | Punchline | Reveal | Story | Lesson | CTA | Outro
    duration_seconds: int
    vo: Optional[str] = None              # exact spoken words, null if no voiceover in this section
    visual: str                            # specific description — enough to reshoot
    on_screen_text: Optional[str] = None  # designed overlay text only, null if none


class MemeDetails(BaseModel):
    joke_mechanic: str              # the comedic device — irony, subversion, exaggeration, relatable truth, etc.
    what_its_satirizing: str        # what behavior, trend, or assumption is being mocked or parodied
    implied_vs_literal: str         # what is literally shown vs what is actually meant
    cultural_reference: Optional[str] = None  # trending audio, format, or cultural moment it riffs on, null if none


class ReelScript(BaseModel):
    content_type: str        # sponsored | meme | lifestyle | talking_head_advice | motivational | personal_story | other
    topic: str               # 2-4 word snake_case slug
    duration_seconds: int
    sections: List[ReelSection]
    has_voiceover: bool
    meme_details: Optional[MemeDetails] = None  # only populated when content_type is "meme"


_GEMINI_BASE_PROMPT = """You are analyzing an Instagram reel from a tech/lifestyle content creator.
Your job is to produce a structured analysis that another person could use to recreate the reel from scratch.

Return valid JSON matching this schema:
{
  "content_type": one of ["sponsored", "meme", "lifestyle", "talking_head_advice", "motivational", "personal_story", "other"],
  "topic": "2-4 word slug, lowercase with underscores",
  "duration_seconds": integer,
  "sections": [
    {
      "label": "one of: Hook | Setup | Problem | Concept | Build | Demo | Feature | Punchline | Reveal | Story | Lesson | CTA | Outro",
      "duration_seconds": integer,
      "vo": "exact spoken words verbatim, or null if silent",
      "visual": "specific description — camera angle, action, transitions",
      "on_screen_text": "exact designed text overlay, or null if none"
    }
  ],
  "has_voiceover": boolean,
  "meme_details": {
    "joke_mechanic": "string",
    "what_its_satirizing": "string",
    "implied_vs_literal": "string",
    "cultural_reference": "string or null"
  }
}

Note: "meme_details" must be null for all content_types except "meme".

═══════════════════════════════════════════════════════════
CONTENT_TYPE — pick exactly one
═══════════════════════════════════════════════════════════
- sponsored: brand partnership, product demo with clear promotional intent, has #ad or explicit mention of partnership
- meme: humor-first content, relatable joke, often with trending audio or text overlay template, no real "teaching"
- lifestyle: day in life, vlog, aesthetic content, personal life, location-based content with no specific lesson
- talking_head_advice: creator directly teaching or advising — interview tips, career advice, coding tips, how-tos
- motivational: inspirational, encouragement, "you got this" energy, mindset content
- personal_story: narrative about the creator's own experience — career change, struggles, milestones
- other: doesn't fit any of the above

When in doubt between two: pick the one that matches the creator's PRIMARY INTENT, not the surface format.
A funny sponsored post is still "sponsored". A motivational story about a career change is "personal_story".

═══════════════════════════════════════════════════════════
TOPIC — 2-4 words, snake_case
═══════════════════════════════════════════════════════════
Specific enough to distinguish from other reels, generic enough to cluster similar ones.
Good: "no_code_app_build", "debugging_humor", "interview_prep_tips", "morning_routine", "career_change_story"
Bad: "coding" (too vague), "how_galinie_built_a_posture_app_using_anything" (too specific)

═══════════════════════════════════════════════════════════
SECTIONS — the most important field
═══════════════════════════════════════════════════════════
An array of shot-list sections. Someone reading this should be able to reshoot the reel without watching the original.

Each section object:
- label: Hook | Setup | Problem | Concept | Build | Demo | Feature | Punchline | Reveal | Story | Lesson | CTA | Outro
- duration_seconds: integer seconds for that section
- vo: verbatim spoken words in this section, or null if silent
- visual: specific enough to reshoot — camera angle, what is shown, action, transitions
- on_screen_text: ONLY intentional designed overlays (title cards, callouts, brand logos, lower-thirds). null if none.
  STRICTLY FORBIDDEN: do NOT include auto-generated captions or subtitles echoing the spoken words.

RULES:
- Section durations must sum to total reel duration (±2s tolerance)
- vo must be VERBATIM — exact words, do not paraphrase. null if no speech in that section.
- visual must be specific enough to reshoot:
  ✓ "Talking head, creator at desk, holds phone toward camera"
  ✗ "Creator talking about the app"
- Include transitions, cuts, and effects in the visual field where notable
- For memes with trending audio, note the audio name in the visual field

═══════════════════════════════════════════════════════════
MEME ANALYSIS — only when content_type is "meme"
═══════════════════════════════════════════════════════════
When the reel is a meme, populate the "meme_details" object. Leave it null for all other content types.

- joke_mechanic: name the comedic device. Examples: "ironic contrast between caption and action", "relatable exaggeration", "subverted expectation", "deadpan literalism"
- what_its_satirizing: the specific behavior, trend, or cultural assumption being mocked. Be precise — not "work culture" but "the idea that approving AI outputs counts as working hard"
- implied_vs_literal: two-part answer. Literal = what the video actually shows. Implied = what the creator actually means. The gap between these IS the joke.
- cultural_reference: the trend, audio, format, or moment it riffs on. null if it's an original format.

For memes, the visual description in sections should note behavioral cues and implied actions — not just what is in frame, but what the body language and context communicate.

═══════════════════════════════════════════════════════════
NOW ANALYZE THE PROVIDED REEL.
═══════════════════════════════════════════════════════════
- Watch the entire reel before producing output
- Be precise with VO transcription — exact words, not paraphrases
- Be specific with visual descriptions — enough detail to reshoot
- Make sure section durations add up to total duration
- Return ONLY the JSON object, no preamble or explanation"""


def _build_gemini_prompt(transcript: list = None) -> str:
    """Build the Gemini prompt, optionally injecting a Whisper transcript."""
    if not transcript:
        return _GEMINI_BASE_PROMPT

    lines = "\n".join(
        f"[{seg['start']}s\u2013{seg['end']}s]: \"{seg['text'].strip()}\""
        for seg in transcript
    )
    transcript_block = (
        "\n\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\n"
        "TRANSCRIPT (Whisper-generated \u2014 authoritative source for all spoken words)\n"
        "\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\n"
        f"{lines}\n"
        "\nIMPORTANT rules when a transcript is provided:\n"
        "- All VO lines MUST use the exact words from this transcript. Do not listen to the audio independently.\n"
        "- CRITICAL: The reel's auto-generated captions / subtitles are NOT on-screen text. They are a visual echo of this transcript — the exact same words shown as overlaid text. DO NOT include them in any On-screen text line. Treat them as invisible.\n"
        "- Only include On-screen text for DESIGNED GRAPHIC ELEMENTS: title cards, callout boxes, branded lower-thirds, emphasis graphics, product names, URLs — things a video editor manually added that are NOT captions of the spoken audio.\n"
        "- When in doubt: if the text on screen matches or closely paraphrases what is being said at that moment, it is a caption. Omit it.\n"
    )
    return _GEMINI_BASE_PROMPT.replace(
        "\n═══════════════════════════════════════════════════════════\nNOW ANALYZE THE PROVIDED REEL.",
        transcript_block + "\n═══════════════════════════════════════════════════════════\nNOW ANALYZE THE PROVIDED REEL."
    )


def analyze_video_gemini(api_key: str, video_path: str, transcript: list = None) -> Dict[str, Any]:
    """
    Analyze a video using Google Gemini.
    Uploads the video, sends it with the prompt, and returns structured JSON.

    Args:
        api_key: Google Gemini API key
        video_path: Path to video file
        transcript: Optional Whisper transcript [{start, end, text}, ...] to inject into prompt

    Returns:
        Dict containing analysis results with status and summary
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")

    file_size = os.path.getsize(video_path)
    if file_size == 0:
        raise ValueError(f"Video file is empty: {video_path}")

    client = genai.Client(api_key=api_key)

    logger.info(f"Uploading video to Gemini: {video_path} ({file_size / (1024*1024):.1f}MB)")
    video_file = client.files.upload(file=video_path)
    logger.info(f"Video uploaded: {video_file.name}, state: {video_file.state}")

    # Wait for file to become ACTIVE before generating
    while video_file.state.name == "PROCESSING":
        logger.info("Waiting for Gemini file to become ACTIVE...")
        time.sleep(5)
        video_file = client.files.get(name=video_file.name)

    if video_file.state.name != "ACTIVE":
        raise RuntimeError(f"Gemini file entered unexpected state: {video_file.state.name}")

    gemini_prompt = _build_gemini_prompt(transcript)

    logger.info("Generating Gemini analysis...")
    response = client.models.generate_content(
        model="gemini-2.5-pro",
        contents=[video_file, gemini_prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ReelScript,
            temperature=0.7,
        ),
    )

    result = response.parsed
    logger.info("Gemini analysis completed successfully")

    return {
        "status": "completed",
        "summary": result.model_dump() if result else response.text,
    }


# ─── Format Extraction ──────────────────────────────────────────────────────

class SectionTemplate(BaseModel):
    position: str              # "opening", "middle", "closing"
    role: str                  # "hook", "setup", "tension", "payoff", "cta", etc.
    duration_guidance: str     # "1-3 seconds", "20-30% of total"
    description: str           # what this section should accomplish
    visual_style_notes: Optional[str] = None
    audio_notes: Optional[str] = None
    on_screen_text_pattern: Optional[str] = None


class FormatTemplate(BaseModel):
    format_name: str                # "The Ironic Demo", "Before/After Reveal"
    description: str                # 2-3 sentence summary of the format
    target_audience: str            # who this format works for
    content_categories: List[str]   # what niches/topics suit this format
    total_duration_range: str       # "15-30 seconds"
    structure: List[SectionTemplate]
    pacing_guidelines: str
    hook_mechanics: str             # what makes the opening work
    visual_style_notes: str
    audio_strategy: str             # voiceover vs music vs trending audio
    when_to_use: str
    what_makes_it_work: str         # underlying engagement principle
    common_mistakes: Optional[str] = None
    example_topics: List[str]       # 3-5 example topics that would work


_FORMAT_EXTRACTION_PROMPT = """You are watching a TUTORIAL VIDEO where a content creator EXPLAINS a short-form video format.
The creator is teaching you the structure, pacing, and mechanics of a specific reel/short format.

YOUR TASK: Extract the FORMAT being taught — NOT a description of the tutorial itself.
Think of yourself as a student taking notes on the lesson. The output should be a reusable template
that a tech/software engineering content creator could follow to make their own video in this format.

═══════════════════════════════════════════════════════════
CREATOR CONTEXT
═══════════════════════════════════════════════════════════
The person who will USE this template is a tech content creator covering:
- Software engineering, coding, developer tools, AI/ML
- Corporate/office life, startup culture, tech industry
- Career advice, interview prep, job hunting in tech
- Humorous takes on dev life, debugging, meetings, code reviews
- Honest/vulnerable content about burnout, imposter syndrome, career pivots

When writing target_audience, content_categories, and example_topics — frame everything
through this lens. The format itself is universal, but the APPLICATION should feel native
to a tech creator's world.

Return valid JSON matching this schema:
{
  "format_name": "catchy name for the format (e.g. 'The Ironic Demo', 'Before/After Reveal')",
  "description": "2-3 sentence summary of what this format IS and how it works",
  "target_audience": "who should use this format — frame for tech/SWE/corporate creators",
  "content_categories": ["list of specific tech/corporate/dev life topics this works for"],
  "total_duration_range": "e.g. '15-30 seconds' or '30-60 seconds'",
  "structure": [
    {
      "position": "opening | middle | closing",
      "role": "hook | setup | tension | build | demo | payoff | reveal | lesson | cta | outro",
      "duration_guidance": "e.g. '1-3 seconds' or '20-30% of total'",
      "description": "what this section should accomplish — written as instruction to the creator",
      "visual_style_notes": "camera angle, framing, transitions, effects to use — or null",
      "audio_notes": "voiceover, music, sound effects guidance — or null",
      "on_screen_text_pattern": "type of text overlay to use — or null"
    }
  ],
  "pacing_guidelines": "how the rhythm/tempo should flow across the video",
  "hook_mechanics": "what makes the first 1-3 seconds grab attention — the specific technique",
  "visual_style_notes": "overall visual approach, editing style, aesthetic",
  "audio_strategy": "voiceover vs music vs trending audio vs silence — and why",
  "when_to_use": "situations, goals, or content types where this format excels",
  "what_makes_it_work": "the underlying psychological or engagement principle — WHY this format is effective",
  "common_mistakes": "pitfalls to avoid when using this format — or null if not discussed",
  "example_topics": ["5 example topics — mix of: coding/tech humor, corporate life, career advice, honest/vulnerable, and lifestyle"]
}

═══════════════════════════════════════════════════════════
KEY RULES
═══════════════════════════════════════════════════════════

1. EXTRACT THE TAUGHT FORMAT, NOT THE TUTORIAL
   The creator is explaining a format. Your job is to capture WHAT they're teaching, not HOW they're teaching it.
   - Good: "Open with a bold claim that challenges conventional wisdom"
   - Bad: "The creator explains that you should open with a bold claim"

2. GENERALIZE THE STRUCTURE, SPECIALIZE THE EXAMPLES
   The format structure should be universal (it works for any topic).
   But example_topics and content_categories should be grounded in the tech/SWE/corporate niche.
   Think: debugging, code reviews, standups, deploy Fridays, imposter syndrome, career pivots,
   side projects, tech Twitter drama, AI hype, startup culture, work-life balance.

3. USE THE TRANSCRIPT AS PRIMARY SOURCE
   The creator's spoken words contain the core knowledge — the structure, tips, and reasoning.
   The visual content shows demonstrations and examples. Both matter, but the transcript is primary.

4. STRUCTURE SECTIONS AS INSTRUCTIONS
   Each section's description should read like a directive to a creator:
   - Good: "Open with a surprising statistic or counterintuitive claim that stops the scroll"
   - Bad: "The opening section contains a hook"

5. CAPTURE THE WHY
   When the creator explains WHY something works (psychology, algorithm, audience behavior),
   capture that in what_makes_it_work and hook_mechanics.

6. BE SPECIFIC ABOUT TIMING
   If the creator mentions specific durations or ratios, preserve them.
   "Hook should be under 2 seconds" is more useful than "keep the hook short".

7. EXAMPLE TOPICS SHOULD BE VARIED
   Aim for a mix across these flavors:
   - Tech humor (debugging, legacy code, meetings that could've been emails)
   - Corporate reality (standups, sprint planning, performance reviews)
   - Career/growth (interview prep, salary negotiation, switching stacks)
   - Honest/vulnerable (burnout, imposter syndrome, rejection, layoffs)
   - Lifestyle crossover (WFH life, side projects, conference travel)

═══════════════════════════════════════════════════════════
NOW EXTRACT THE FORMAT FROM THIS TUTORIAL.
═══════════════════════════════════════════════════════════
- Watch/listen to the entire tutorial before producing output
- Capture ALL format details the creator teaches, not just the obvious ones
- If the creator teaches multiple variations, focus on the PRIMARY format
- Return ONLY the JSON object, no preamble or explanation"""


def _build_format_extraction_prompt(transcript: list = None) -> str:
    """Build the format extraction prompt, optionally injecting a Whisper transcript."""
    if not transcript:
        return _FORMAT_EXTRACTION_PROMPT

    lines = "\n".join(
        f"[{seg['start']}s–{seg['end']}s]: \"{seg['text'].strip()}\""
        for seg in transcript
    )
    transcript_block = (
        "\n═══════════════════════════════════════════════════════════\n"
        "TRANSCRIPT (Whisper-generated — the creator's verbal explanation)\n"
        "═══════════════════════════════════════════════════════════\n"
        "This is the PRIMARY source of knowledge. The creator is explaining the format in their own words.\n"
        "Extract the format details, structure, and tips they describe.\n\n"
        f"{lines}\n"
    )
    return _FORMAT_EXTRACTION_PROMPT.replace(
        "\n═══════════════════════════════════════════════════════════\nNOW EXTRACT THE FORMAT FROM THIS TUTORIAL.",
        transcript_block + "\n═══════════════════════════════════════════════════════════\nNOW EXTRACT THE FORMAT FROM THIS TUTORIAL."
    )


def analyze_video_format_extraction(api_key: str, video_path: str, transcript: list = None) -> Dict[str, Any]:
    """
    Analyze a tutorial video to extract the video format being taught.
    Uploads the video to Gemini and returns a structured FormatTemplate.

    Args:
        api_key: Google Gemini API key
        video_path: Path to video file
        transcript: Optional Whisper transcript [{start, end, text}, ...]

    Returns:
        Dict containing format template results with status, summary (JSON), and markdown
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")

    file_size = os.path.getsize(video_path)
    if file_size == 0:
        raise ValueError(f"Video file is empty: {video_path}")

    client = genai.Client(api_key=api_key)

    logger.info(f"Uploading video to Gemini for format extraction: {video_path} ({file_size / (1024*1024):.1f}MB)")
    video_file = client.files.upload(file=video_path)
    logger.info(f"Video uploaded: {video_file.name}, state: {video_file.state}")

    while video_file.state.name == "PROCESSING":
        logger.info("Waiting for Gemini file to become ACTIVE...")
        time.sleep(5)
        video_file = client.files.get(name=video_file.name)

    if video_file.state.name != "ACTIVE":
        raise RuntimeError(f"Gemini file entered unexpected state: {video_file.state.name}")

    prompt = _build_format_extraction_prompt(transcript)

    logger.info("Generating format extraction analysis...")
    response = client.models.generate_content(
        model="gemini-2.5-pro",
        contents=[video_file, prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=FormatTemplate,
            temperature=0.7,
        ),
    )

    result = response.parsed
    logger.info("Format extraction completed successfully")

    summary = result.model_dump() if result else response.text
    markdown = render_format_markdown(result) if result else None

    return {
        "status": "completed",
        "summary": summary,
        "markdown": markdown,
    }


def render_format_markdown(template: FormatTemplate) -> str:
    """Convert a FormatTemplate into a clean markdown skill file."""
    sections_table = "| # | Role | Duration | What to Do |\n|---|------|----------|------------|\n"
    for i, section in enumerate(template.structure, 1):
        sections_table += f"| {i} | {section.role.title()} | {section.duration_guidance} | {section.description} |\n"

    section_details = ""
    for i, section in enumerate(template.structure, 1):
        section_details += f"\n**{i}. {section.role.title()}** ({section.position}, {section.duration_guidance})\n"
        section_details += f"{section.description}\n"
        if section.visual_style_notes:
            section_details += f"- *Visual:* {section.visual_style_notes}\n"
        if section.audio_notes:
            section_details += f"- *Audio:* {section.audio_notes}\n"
        if section.on_screen_text_pattern:
            section_details += f"- *Text overlay:* {section.on_screen_text_pattern}\n"

    categories = ", ".join(template.content_categories)
    examples = "\n".join(f"- {topic}" for topic in template.example_topics)

    common_mistakes_section = ""
    if template.common_mistakes:
        common_mistakes_section = f"\n## Common Mistakes\n{template.common_mistakes}\n"

    return f"""# Format: {template.format_name}

> {template.description}

**Duration:** {template.total_duration_range}

## When to Use
{template.when_to_use}

## Target Audience
{template.target_audience}

**Works well for:** {categories}

## What Makes It Work
{template.what_makes_it_work}

## Structure

{sections_table}

### Section Details
{section_details}

## Pacing
{template.pacing_guidelines}

## Hook Mechanics
{template.hook_mechanics}

## Visual Style
{template.visual_style_notes}

## Audio Strategy
{template.audio_strategy}
{common_mistakes_section}
## Example Topics
{examples}
"""
