import shutil
import tempfile
import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from video_processor.config import get_config, validate_video_format
from video_processor.store import DatabaseConnection, ArtifactStore, RunsStore, UrlCacheStore
from video_processor.indexer import index_video
from video_processor.runner import run_prompt, ArtifactNotFound

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="inst-ai-bot", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

config = get_config()
db_connection = DatabaseConnection(config)
artifact_store: ArtifactStore = None
runs_store: RunsStore = None
url_cache: UrlCacheStore = None


@app.on_event("startup")
async def startup():
    global artifact_store, runs_store, url_cache
    if not db_connection.connect():
        raise RuntimeError("MongoDB connection required")
    artifact_store = ArtifactStore(db_connection.db)
    runs_store = RunsStore(db_connection.db)
    url_cache = UrlCacheStore(db_connection.db)
    logger.info("Server ready")


@app.on_event("shutdown")
async def shutdown():
    db_connection.close()


# ── Artifacts ─────────────────────────────────────────────────────────────────

class IndexUrlRequest(BaseModel):
    url: str


@app.post("/artifacts")
def create_artifact_from_url(request: IndexUrlRequest):
    """Index a video from a URL. Idempotent — returns existing artifact if already indexed."""
    try:
        return index_video(request.url, config, artifact_store, url_cache)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/artifacts/upload")
def create_artifact_from_file(video: UploadFile = File(...)):
    """Index an uploaded video file. Idempotent — returns existing artifact if already indexed."""
    if not validate_video_format(video.filename, config):
        raise HTTPException(status_code=400, detail=f"Unsupported format: {Path(video.filename).suffix}")

    temp_dir = Path(tempfile.mkdtemp(prefix="inst_ai_upload_"))
    try:
        dest = temp_dir / video.filename
        with open(dest, "wb") as f:
            shutil.copyfileobj(video.file, f)
        return index_video(str(dest), config, artifact_store, url_cache)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@app.get("/artifacts")
def list_artifacts():
    return artifact_store.list()


@app.get("/artifacts/{content_hash:path}")
def get_artifact(content_hash: str):
    artifact = artifact_store.get_by_hash(content_hash)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return artifact


# ── Runs ──────────────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    artifact: str
    prompt: str
    model: str = "google/gemini-2.5-pro"
    label: Optional[str] = None


@app.post("/runs")
def create_run(request: RunRequest):
    """Run an opaque prompt against an indexed artifact."""
    try:
        return run_prompt(
            artifact_hash=request.artifact,
            prompt=request.prompt,
            model=request.model,
            label=request.label,
            config=config,
            artifact_store=artifact_store,
            runs_store=runs_store,
        )
    except ArtifactNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except NotImplementedError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Run failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/runs")
def list_runs(artifact: Optional[str] = None):
    return runs_store.list(artifact_hash=artifact)


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    run = runs_store.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "healthy", "version": "2.0.0"}
