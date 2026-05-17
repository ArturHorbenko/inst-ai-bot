import tempfile
import shutil
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from enum import Enum
import uuid
import asyncio
import logging
from pathlib import Path

# Import our modular pipeline
from video_processor.pipeline import run_analysis, get_supported_analysis_types, get_analysis_descriptions
from video_processor.config import get_config, validate_video_format
from video_processor.db import DatabaseConnection, JobManager, ResultsManager
from video_processor.downloader import download_instagram_reel

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Video Analysis API", version="1.0.0")

# Add CORS middleware for frontend communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # Next.js dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize database connection
config = get_config()
db_connection = DatabaseConnection(config)
job_manager = None
results_manager = None


def finalize_job_if_complete(job_id: str):
    """
    Mark job as completed only when all requested analyses have stored results.
    """
    job = job_manager.get_job(job_id)
    if not job:
        return

    # Never override terminal failure.
    if job.get("status") == "failed":
        return

    requested = set(job.get("analyses", []))
    if not requested:
        job_manager.update_job_status(job_id, "completed")
        return

    stored_results = results_manager.get_results(job_id)
    finished = {result.get("analysis_type") for result in stored_results if result.get("analysis_type")}

    if requested.issubset(finished):
        job_manager.update_job_status(job_id, "completed")

@app.on_event("startup")
async def startup_event():
    """Initialize database connection on startup"""
    global job_manager, results_manager

    if not db_connection.connect():
        logger.error("Failed to connect to MongoDB - server cannot start")
        raise RuntimeError("MongoDB connection required for server operation")

    job_manager = JobManager(db_connection)
    results_manager = ResultsManager(db_connection)
    logger.info("MongoDB connection established - server ready")

@app.on_event("shutdown")
async def shutdown_event():
    """Clean up database connection on shutdown"""
    if db_connection:
        db_connection.close()

class AnalysisType(str, Enum):
    MULTIMODAL = "multimodal"
    GEMINI = "gemini"
    STRUCTURED = "structured"
    FORMAT_EXTRACTION = "format_extraction"

class AnalysisResponse(BaseModel):
    job_id: str
    status: str
    results: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    processing_time: Optional[float] = None
    # TwelveLabs indexing progress fields
    twelve_labs_video_id: Optional[str] = None
    twelve_labs_index_id: Optional[str] = None
    indexing_status: Optional[str] = None
    indexing_progress: Optional[float] = None
    # Populated for jobs created via /analyze/url
    source_url: Optional[str] = None
    source_metadata: Optional[Dict[str, Any]] = None


class AnalyzeUrlRequest(BaseModel):
    url: str
    analyses: Optional[List[str]] = None

@app.post("/analyze", response_model=AnalysisResponse)
async def analyze_video(
    video: UploadFile = File(..., description="Video file to analyze"),
    analyses: str = Form(..., description="Comma-separated list of analysis types")
):
    """
    Analyze uploaded video file with specified analysis types
    """
    # Validate file type using config-based validation
    if not validate_video_format(video.filename, config):
        file_extension = Path(video.filename).suffix.lower() if video.filename else ""
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported video format '{file_extension}'. Supported formats: {config.SUPPORTED_VIDEO_FORMATS}"
        )

    # Parse analysis types
    try:
        analysis_list = [AnalysisType(a.strip()) for a in analyses.split(",")]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid analysis type: {e}")

    analysis_values = [a.value for a in analysis_list]
    if "multimodal" in analysis_values and not config.ENABLE_MULTIMODAL_ANALYSIS:
        raise HTTPException(status_code=400, detail="Multimodal analysis is disabled by server configuration")

    # Generate job ID
    job_id = str(uuid.uuid4())

    # Create temporary directory for this job
    temp_dir = Path(tempfile.mkdtemp(prefix=f"video_analysis_{job_id}_"))
    video_path = temp_dir / video.filename

    try:
        # Save uploaded video
        with open(video_path, "wb") as buffer:
            shutil.copyfileobj(video.file, buffer)

        # Initialize job tracking
        job_data = {
            "job_id": job_id,
            "video_filename": video.filename,
            "video_size": video.size if hasattr(video, 'size') else 0,
            "video_content_type": video.content_type or "",
            "temp_dir": str(temp_dir),
            "video_path": str(video_path),
            "analyses": [a.value for a in analysis_list]
        }

        if not job_manager.create_job(job_data):
            # Cleanup and raise error if job creation fails
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            raise HTTPException(status_code=500, detail="Failed to create job - database error")

        # Start internal analyses (non-multimodal) in background.
        if any(analysis != "multimodal" for analysis in analysis_values):
            asyncio.create_task(process_video_analysis(job_id))

        # If multimodal analysis is requested, start TwelveLabs upload in background.
        if "multimodal" in analysis_values:
            asyncio.create_task(process_twelvelabs_upload(job_id))

        return AnalysisResponse(
            job_id=job_id,
            status="processing",
            results=None,
            twelve_labs_video_id=None,
            twelve_labs_index_id=None,
            indexing_status=None,
            indexing_progress=None
        )

    except Exception as e:
        # Cleanup on error
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        raise HTTPException(status_code=500, detail=f"Error processing video: {str(e)}")


@app.post("/analyze/url", response_model=AnalysisResponse)
async def analyze_video_from_url(request: AnalyzeUrlRequest):
    """
    Download an Instagram reel from a URL and kick off analysis.
    """
    analysis_values = request.analyses if request.analyses else ["gemini"]

    try:
        analysis_list = [AnalysisType(a.strip()) for a in analysis_values]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid analysis type: {e}")

    analysis_values = [a.value for a in analysis_list]
    if "multimodal" in analysis_values and not config.ENABLE_MULTIMODAL_ANALYSIS:
        raise HTTPException(status_code=400, detail="Multimodal analysis is disabled by server configuration")

    job_id = str(uuid.uuid4())
    temp_dir = Path(tempfile.mkdtemp(prefix=f"video_analysis_{job_id}_"))

    try:
        try:
            video_path, source_metadata = download_instagram_reel(request.url, temp_dir)
        except ValueError as e:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail=str(e))
        except RuntimeError as e:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail=str(e))

        if not validate_video_format(video_path.name, config):
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise HTTPException(
                status_code=400,
                detail=f"Downloaded file has unsupported format: {video_path.name}"
            )

        job_data = {
            "job_id": job_id,
            "video_filename": video_path.name,
            "video_size": video_path.stat().st_size,
            "video_content_type": "video/mp4",
            "temp_dir": str(temp_dir),
            "video_path": str(video_path),
            "analyses": analysis_values,
            "source_url": request.url,
            "source_metadata": source_metadata,
        }

        if not job_manager.create_job(job_data):
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise HTTPException(status_code=500, detail="Failed to create job - database error")

        if any(analysis != "multimodal" for analysis in analysis_values):
            asyncio.create_task(process_video_analysis(job_id))

        if "multimodal" in analysis_values:
            asyncio.create_task(process_twelvelabs_upload(job_id))

        return AnalysisResponse(
            job_id=job_id,
            status="processing",
            results=None,
            twelve_labs_video_id=None,
            twelve_labs_index_id=None,
            indexing_status=None,
            indexing_progress=None,
            source_url=request.url,
            source_metadata=source_metadata,
        )

    except HTTPException:
        raise
    except Exception as e:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Error processing reel URL: {str(e)}")


@app.get("/analyze/{job_id}", response_model=AnalysisResponse)
async def get_analysis_status(job_id: str):
    """
    Get status and results of analysis job
    """
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    results = None
    if job["status"] == "completed":
        results_list = results_manager.get_results(job_id)
        if results_list:
            results = {}
            for result in results_list:
                if result["analysis_type"] != "transcription":
                    results[result["analysis_type"]] = result["results"]

    return AnalysisResponse(
        job_id=job_id,
        status=job["status"],
        results=results,
        error=job.get("error"),
        twelve_labs_video_id=job.get("twelve_labs_video_id"),
        twelve_labs_index_id=job.get("twelve_labs_index_id"),
        indexing_status=job.get("indexing_status"),
        indexing_progress=job.get("indexing_progress"),
        source_url=job.get("source_url"),
        source_metadata=job.get("source_metadata"),
    )

async def process_video_analysis(job_id: str):
    """
    Process video analysis in background
    """
    job_data = job_manager.get_job(job_id)
    if not job_data:
        logger.error(f"Job {job_id} not found in database")
        return

    job = {
        "video_path": job_data["video_path"],
        "analyses": job_data["analyses"],
        "source_metadata": job_data.get("source_metadata"),
    }

    try:
        video_path = str(job["video_path"])
        source_metadata = job.get("source_metadata")
        # Handle both string arrays and AnalysisType enum arrays
        if isinstance(job["analyses"][0], str):
            analysis_types = job["analyses"]
        else:
            analysis_types = [analysis.value for analysis in job["analyses"]]

        # Multimodal analysis is handled by process_twelvelabs_upload.
        analysis_types = [analysis for analysis in analysis_types if analysis != "multimodal"]
        if not analysis_types:
            return


        # Add job_id to job_manager for tracking
        job_manager.current_job_id = job_id

        # Run analysis in thread pool to avoid blocking the event loop
        loop = asyncio.get_event_loop()
        import functools
        results = await loop.run_in_executor(
            None,
            functools.partial(
                run_analysis,
                video_path,
                analysis_types,
                None,  # twelvelabs_video_id
                job_manager,
                results_manager,
                job_id,
                source_metadata,
            )
        )

        # Store results separately for each analysis type
        for analysis_type, result_data in results.items():
            results_manager.store_results(job_id, analysis_type, result_data, 0.0)  # TODO: track actual processing time

            # Any analysis-level error fails the whole job.
            status = result_data.get("status") if isinstance(result_data, dict) else None
            error = result_data.get("error", "Analysis failed") if isinstance(result_data, dict) else "Analysis failed"
            if status in {"error", "failed"}:
                job_manager.update_job_status(job_id, "failed", error)
                return

        finalize_job_if_complete(job_id)


    except Exception as e:
        error_msg = str(e)
        job_manager.update_job_status(job_id, "failed", error_msg)

    finally:
        # Cleanup temporary files after some delay
        asyncio.create_task(cleanup_job_files(job_id, delay=3600))  # 1 hour delay


async def process_twelvelabs_upload(job_id: str):
    """
    Handle TwelveLabs upload and indexing in background
    """
    job_data = job_manager.get_job(job_id)
    if not job_data:
        logger.error(f"Job {job_id} not found for TwelveLabs upload")
        return

    try:
        if not config.ENABLE_MULTIMODAL_ANALYSIS:
            job_manager.update_job_status(job_id, "failed", "Multimodal analysis is disabled by server configuration")
            return

        video_path = job_data["video_path"]

        # Always process the uploaded file for this job.
        from video_processor.multimodal import get_video_analysis

        # Set job_manager context
        job_manager.current_job_id = job_id

        result = await asyncio.get_event_loop().run_in_executor(
            None,
            get_video_analysis,
            config.TWELVE_LABS_API_KEY,
            video_path,
            config.TWELVE_LABS_INDEX_ID,
            config.TWELVE_LABS_INDEX_NAME,
            job_manager,
            job_id
        )

        # Store final results
        if result.get("status") == "completed":
            results_manager.store_results(job_id, "multimodal", result, 0.0)
            finalize_job_if_complete(job_id)
        else:
            job_manager.update_job_status(job_id, "failed", result.get("error"))


    except Exception as e:
        logger.error(f"Error in TwelveLabs upload for job {job_id}: {e}")
        job_manager.update_job_status(job_id, "failed", str(e))

async def cleanup_job_files(job_id: str, delay: int = 3600):
    """
    Clean up temporary files after delay
    """
    await asyncio.sleep(delay)

    job = job_manager.get_job(job_id)
    if job:
        temp_dir = job.get("temp_dir")
        if temp_dir:
            temp_dir_path = Path(temp_dir) if isinstance(temp_dir, str) else temp_dir
            if temp_dir_path.exists():
                shutil.rmtree(temp_dir_path)

@app.delete("/analyze/{job_id}")
async def cancel_analysis(job_id: str):
    """
    Cancel analysis job and cleanup files
    """
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Delete from MongoDB
    job_manager.delete_job(job_id)
    results_manager.delete_results(job_id)

    # Cleanup temp files
    temp_dir = job.get("temp_dir")
    if temp_dir:
        temp_dir_path = Path(temp_dir) if isinstance(temp_dir, str) else temp_dir
        if temp_dir_path.exists():
            shutil.rmtree(temp_dir_path)

    return {"message": "Job cancelled and files cleaned up"}

@app.get("/health")
async def health_check():
    """
    Health check endpoint
    """
    return {
        "status": "healthy",
        "version": "1.0.0",
        "mongodb": "connected",
        "storage": "mongodb"
    }

@app.get("/")
async def root():
    """
    Root endpoint with API info
    """
    return {
        "message": "Video Analysis API",
        "version": "1.0.0",
        "endpoints": {
            "POST /analyze": "Upload video and start analysis",
            "POST /analyze/url": "Download an Instagram reel from a URL and start analysis",
            "GET /analyze/{job_id}": "Get analysis status and results",
            "DELETE /analyze/{job_id}": "Cancel analysis job",
            "GET /health": "Health check"
        },
        "supported_analyses": get_supported_analysis_types(),
        "analysis_descriptions": get_analysis_descriptions()
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
