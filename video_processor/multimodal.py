from twelvelabs import TwelveLabs
import time
import os
import mimetypes
import logging
from typing import Dict, Any, Optional, Tuple, Iterable
from requests.exceptions import RequestException, ConnectionError, Timeout

logger = logging.getLogger(__name__)

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
    
    
