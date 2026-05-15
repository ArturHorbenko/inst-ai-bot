from typing import Dict, Any, List


def run_analysis(video_path: str, analysis_types: List[str], twelvelabs_video_id: str = None, job_manager=None, results_manager=None, job_id: str = None) -> Dict[str, Any]:
    """
    Route analysis requests to appropriate analysis functions.

    Args:
        video_path: Path to the uploaded video file
        analysis_types: List of requested analysis types
        twelvelabs_video_id: Video ID from TwelveLabs (optional - will auto-upload if not provided)
        job_manager: JobManager instance for database updates (optional)

    Returns:
        Dict containing results from all requested analyses
    """
    results = {}
    from .config import get_config
    config = get_config()

    for analysis_type in analysis_types:
        if analysis_type == "multimodal":
            if not config.ENABLE_MULTIMODAL_ANALYSIS:
                results[analysis_type] = {
                    "analysis_type": "multimodal",
                    "status": "error",
                    "error": "Multimodal analysis is disabled by server configuration"
                }
            else:
                from .analysis import analyze_multimodal
                results[analysis_type] = analyze_multimodal(video_path, twelvelabs_video_id, job_manager)

        elif analysis_type == "gemini":
            from .analysis import analyze_gemini
            results[analysis_type] = analyze_gemini(video_path, results_manager=results_manager, job_id=job_id)

        elif analysis_type == "structured":
            from .analysis import analyze_structured
            results[analysis_type] = analyze_structured(video_path, results_manager=results_manager, job_id=job_id)

        elif analysis_type == "format_extraction":
            from .analysis import analyze_format_extraction
            results[analysis_type] = analyze_format_extraction(video_path, results_manager=results_manager, job_id=job_id)

        else:
            results[analysis_type] = {
                "analysis_type": analysis_type,
                "status": "error",
                "error": f"Unknown analysis type: {analysis_type}"
            }

    return results


def get_supported_analysis_types() -> List[str]:
    """
    Get list of supported analysis types.

    Returns:
        List of supported analysis type names
    """
    from .config import get_config
    config = get_config()
    supported = ["gemini", "structured", "format_extraction"]
    if config.ENABLE_MULTIMODAL_ANALYSIS:
        supported.insert(0, "multimodal")
    return supported


def get_analysis_descriptions() -> Dict[str, str]:
    """
    Get descriptions of available analysis types.

    Returns:
        Dict mapping analysis type names to descriptions
    """
    from .config import get_config
    config = get_config()
    descriptions = {
        "gemini": "Comprehensive video analysis using Google Gemini 2.5 Pro - synchronous, structured JSON output with Groq Whisper transcript injection",
        "structured": "Complete internal pipeline: scene detection, OCR, transcription, matching, and summarization",
        "format_extraction": "Extract reusable video format template from tutorial videos using Whisper + Gemini — outputs structured JSON and markdown skill file"
    }
    if config.ENABLE_MULTIMODAL_ANALYSIS:
        descriptions["multimodal"] = "Comprehensive video analysis using TwelveLabs AI - automatic upload and indexing with fast external processing"
    return descriptions
