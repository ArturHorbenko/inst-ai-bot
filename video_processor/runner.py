import uuid
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from .store import ArtifactStore, RunsStore

logger = logging.getLogger(__name__)


def run_prompt(
    artifact_hash: str,
    prompt: str,
    model: str,
    label: Optional[str],
    config,
    artifact_store: ArtifactStore,
    runs_store: RunsStore,
    metadata: Optional[dict[str, Any]] = None,
) -> dict:
    """
    Execute an opaque prompt against an indexed artifact. Returns the stored run record.
    """
    artifact = artifact_store.get_by_hash(artifact_hash)
    if not artifact:
        raise ArtifactNotFound(artifact_hash)

    provider, model_id = _parse_model(model)

    if provider == "google":
        from . import gemini as gemini_module

        output, file_ref = gemini_module.call_gemini(
            api_key=config.GEMINI_API_KEY,
            video_path=artifact["video_file_ref"],
            prompt=prompt,
            model=model_id,
            gemini_file_ref=artifact.get("gemini_file_ref"),
        )
        if file_ref and file_ref != artifact.get("gemini_file_ref"):
            artifact_store.update_gemini_ref(artifact_hash, file_ref)
    elif provider == "twelvelabs":
        from . import twelvelabs as twelvelabs_module

        output, video_id = twelvelabs_module.call_twelvelabs(
            api_key=config.TWELVE_LABS_API_KEY,
            video_path=artifact["video_file_ref"],
            prompt=prompt,
            model=model_id,
            twelvelabs_video_id=artifact.get("twelvelabs_video_id"),
            index_name=config.TWELVE_LABS_INDEX_NAME,
            index_id=config.TWELVE_LABS_INDEX_ID,
            poll_interval=config.INDEXING_POLL_INTERVAL,
        )
        if video_id and video_id != artifact.get("twelvelabs_video_id"):
            artifact_store.update_twelvelabs_ref(artifact_hash, video_id)
    else:
        raise NotImplementedError(
            f"Provider '{provider}' not yet supported. Implemented: google, twelvelabs"
        )

    run = {
        "run_id": str(uuid.uuid4()),
        "artifact_hash": artifact_hash,
        "prompt": prompt,
        "model": model,
        "label": label,
        "metadata": metadata or {},
        "output": output,
        "created_at": datetime.now(timezone.utc),
    }

    return runs_store.insert(run)


def _parse_model(model: str) -> tuple[str, str]:
    """Split 'google/gemini-2.5-pro' → ('google', 'gemini-2.5-pro'). Default provider: google."""
    if "/" in model:
        provider, model_id = model.split("/", 1)
        return provider.lower(), model_id
    return "google", model


class ArtifactNotFound(Exception):
    def __init__(self, artifact_hash: str):
        super().__init__(f"Artifact not found: {artifact_hash}")
        self.artifact_hash = artifact_hash
