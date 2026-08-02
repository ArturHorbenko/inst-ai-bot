import os
import json
import secrets
import shutil
import tempfile
import threading
import time
import logging
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Header, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from video_processor.config import get_config, validate_video_format
from video_processor.store import DatabaseConnection, ArtifactStore, RunsStore, UrlCacheStore
from video_processor.indexer import index_video
from video_processor.image_indexer import index_images
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

API_KEY = os.environ.get("INST_AI_BOT_API_KEY", "").strip()
if API_KEY:
    logger.info("API key auth: ENABLED (header X-API-Key required on all routes except /health)")
else:
    logger.warning("API key auth: DISABLED (set INST_AI_BOT_API_KEY to require X-API-Key)")


def require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    if not API_KEY:
        return
    if not x_api_key or not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")


RATE_LIMIT_PER_MIN = int(os.environ.get("INST_AI_BOT_RATE_LIMIT_PER_MIN", "120") or "0")
RATE_WINDOW_SEC = 60
_rate_buckets: Dict[str, Deque[float]] = {}
_rate_lock = threading.Lock()

if RATE_LIMIT_PER_MIN > 0:
    logger.info(f"Rate limit: {RATE_LIMIT_PER_MIN} req / {RATE_WINDOW_SEC}s per client IP (per worker)")
else:
    logger.warning("Rate limit: DISABLED (set INST_AI_BOT_RATE_LIMIT_PER_MIN > 0 to enable)")


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if RATE_LIMIT_PER_MIN <= 0:
        return await call_next(request)
    ip = _client_ip(request)
    now = time.monotonic()
    cutoff = now - RATE_WINDOW_SEC
    with _rate_lock:
        bucket = _rate_buckets.setdefault(ip, deque())
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= RATE_LIMIT_PER_MIN:
            retry_after = int(RATE_WINDOW_SEC - (now - bucket[0])) + 1
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
                headers={"Retry-After": str(retry_after)},
            )
        bucket.append(now)
    return await call_next(request)

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


@app.post("/artifacts", dependencies=[Depends(require_api_key)])
def create_artifact_from_url(request: IndexUrlRequest):
    """Index a video from a URL. Idempotent — returns existing artifact if already indexed."""
    try:
        return index_video(request.url, config, artifact_store, url_cache)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/artifacts/upload", dependencies=[Depends(require_api_key)])
def create_artifact_from_file(
    video: UploadFile = File(...),
    source_url: Optional[str] = Form(default=None),
    source_type: str = Form(default="upload"),
    source_metadata_json: Optional[str] = Form(default=None),
):
    """Index an uploaded video file with optional source provenance.

    Callers that already have authorized video bytes can identify the Source
    without sending the URL through the downloader path.
    """
    if not validate_video_format(video.filename, config):
        raise HTTPException(status_code=400, detail=f"Unsupported format: {Path(video.filename).suffix}")
    try:
        source_metadata = json.loads(source_metadata_json) if source_metadata_json else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="source_metadata_json must be valid JSON")
    if not isinstance(source_metadata, dict):
        raise HTTPException(status_code=400, detail="source_metadata_json must be an object")

    temp_dir = Path(tempfile.mkdtemp(prefix="inst_ai_upload_"))
    try:
        dest = temp_dir / video.filename
        with open(dest, "wb") as f:
            shutil.copyfileobj(video.file, f)
        return index_video(
            str(dest),
            config,
            artifact_store,
            url_cache,
            source_url=source_url,
            source_type=source_type,
            source_metadata=source_metadata,
            source_fetcher="provided_upload" if source_url else None,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@app.post("/artifacts/images", dependencies=[Depends(require_api_key)])
def create_artifact_from_images(
    images: List[UploadFile] = File(...),
    source_url: Optional[str] = Form(default=None),
    source_type: str = Form(default="upload"),
    source_metadata_json: Optional[str] = Form(default=None),
):
    """Index one authorized image or an ordered Instagram-style image carousel."""
    try:
        source_metadata = json.loads(source_metadata_json) if source_metadata_json else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="source_metadata_json must be valid JSON")
    if not isinstance(source_metadata, dict):
        raise HTTPException(status_code=400, detail="source_metadata_json must be an object")

    temp_dir = Path(tempfile.mkdtemp(prefix="inst_ai_images_"))
    try:
        image_paths = []
        for index, image in enumerate(images):
            # Keep the original extension for format validation, while ensuring
            # duplicate client filenames cannot overwrite a previous slide.
            filename = Path(image.filename or f"image-{index}.bin").name
            destination = temp_dir / str(index) / filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            with open(destination, "wb") as file:
                shutil.copyfileobj(image.file, file)
            image_paths.append(destination)
        return index_images(
            image_paths,
            config,
            artifact_store,
            url_cache,
            source_url=source_url,
            source_type=source_type,
            source_metadata=source_metadata,
            source_fetcher="provided_upload",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Image indexing failed")
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@app.get("/artifacts", dependencies=[Depends(require_api_key)])
def list_artifacts():
    return artifact_store.list()


@app.get("/artifacts/{content_hash:path}", dependencies=[Depends(require_api_key)])
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
    metadata: Dict[str, Any] = Field(default_factory=dict)


@app.post("/runs", dependencies=[Depends(require_api_key)])
def create_run(request: RunRequest):
    """Run an opaque prompt against an indexed artifact."""
    try:
        return run_prompt(
            artifact_hash=request.artifact,
            prompt=request.prompt,
            model=request.model,
            label=request.label,
            metadata=request.metadata,
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


@app.get("/runs", dependencies=[Depends(require_api_key)])
def list_runs(artifact: Optional[str] = None):
    return runs_store.list(artifact_hash=artifact)


@app.get("/runs/{run_id}", dependencies=[Depends(require_api_key)])
def get_run(run_id: str):
    run = runs_store.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "healthy", "version": "2.0.0"}
