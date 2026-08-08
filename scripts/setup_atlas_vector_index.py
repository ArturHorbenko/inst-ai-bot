"""Provision and verify the Atlas Vector Search index used for video retrieval."""

from __future__ import annotations

import time
from typing import Any, Optional

from pymongo import MongoClient
from pymongo.operations import SearchIndexModel

from video_processor.config import get_config
from video_processor.retrieval import DEFAULT_EMBEDDING_DIMENSIONS


FILTER_PATHS = ("trait_schema", "prompt_version")
FAILED_STATUSES = {"FAILED", "ERROR", "DELETED", "DOES_NOT_EXIST"}


def index_definition(dimensions: int) -> dict[str, list[dict[str, Any]]]:
    """Return the definition required by the version-filtered search pipeline."""
    return {
        "fields": [
            {
                "type": "vector",
                "path": "embedding",
                "numDimensions": dimensions,
                "similarity": "cosine",
            },
            *[{"type": "filter", "path": path} for path in FILTER_PATHS],
        ],
    }


def _find_index(collection, index_name: str) -> Optional[dict[str, Any]]:
    return next(iter(collection.list_search_indexes(index_name)), None)


def _definition_errors(index: dict[str, Any], dimensions: int) -> list[str]:
    definition = index.get("latestDefinition") or index.get("definition") or {}
    fields = definition.get("fields")
    if not isinstance(fields, list):
        return ["its definition has no fields list"]

    vector_field = next(
        (
            field
            for field in fields
            if field.get("type") == "vector" and field.get("path") == "embedding"
        ),
        None,
    )
    errors = []
    if vector_field is None:
        errors.append("it has no vector field for embedding")
    elif vector_field.get("numDimensions") != dimensions:
        errors.append(
            f"embedding uses {vector_field.get('numDimensions')!r} dimensions, expected {dimensions}"
        )
    elif vector_field.get("similarity") != "cosine":
        errors.append("embedding does not use cosine similarity")

    filter_paths = {
        field.get("path") for field in fields if field.get("type") == "filter"
    }
    missing_filters = [path for path in FILTER_PATHS if path not in filter_paths]
    if missing_filters:
        errors.append("missing filter fields: " + ", ".join(missing_filters))
    return errors


def wait_until_usable(
    collection,
    *,
    index_name: str,
    dimensions: int,
    timeout: int,
    poll_interval: int,
) -> None:
    """Wait for one compatible index to become ready and queryable."""
    if timeout <= 0 or poll_interval <= 0:
        raise ValueError("ATLAS_VECTOR_INDEX_TIMEOUT and ATLAS_VECTOR_INDEX_POLL_INTERVAL must be positive")

    deadline = time.monotonic() + timeout
    while True:
        index = _find_index(collection, index_name)
        if index:
            errors = _definition_errors(index, dimensions)
            if errors:
                raise RuntimeError(
                    f"Atlas Vector Search index {index_name!r} is incompatible: {'; '.join(errors)}. "
                    "Create a new compatible index or update this index before deploying."
                )

            status = str(index.get("status", "UNKNOWN")).upper()
            queryable = index.get("queryable")
            if status == "READY" and queryable is not False:
                print(f"Atlas Vector Search index is ready: {index_name}")
                return
            if status in FAILED_STATUSES:
                raise RuntimeError(
                    f"Atlas Vector Search index {index_name!r} entered status {status!r}. "
                    "Inspect the Atlas Search index status and resolve it before retrying."
                )

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(
                f"Timed out after {timeout}s waiting for Atlas Vector Search index {index_name!r} "
                "to become READY and queryable. Check its Atlas Search status and definition."
            )
        time.sleep(min(poll_interval, remaining))


def main() -> None:
    config = get_config()
    dimensions = config.RETRIEVAL_EMBEDDING_DIMENSIONS
    if dimensions != DEFAULT_EMBEDDING_DIMENSIONS:
        print(f"Using configured retrieval embedding dimensions: {dimensions}")

    client = MongoClient(config.MONGODB_URI, serverSelectionTimeoutMS=10_000)
    try:
        collection = client[config.MONGODB_DB]["video_retrieval_documents"]
        existing = _find_index(collection, config.ATLAS_VECTOR_INDEX)
        if existing is None:
            name = collection.create_search_index(
                SearchIndexModel(
                    name=config.ATLAS_VECTOR_INDEX,
                    type="vectorSearch",
                    definition=index_definition(dimensions),
                )
            )
            print(f"Created Atlas Vector Search index: {name}")
        else:
            print(f"Verifying Atlas Vector Search index: {config.ATLAS_VECTOR_INDEX}")

        wait_until_usable(
            collection,
            index_name=config.ATLAS_VECTOR_INDEX,
            dimensions=dimensions,
            timeout=config.ATLAS_VECTOR_INDEX_TIMEOUT,
            poll_interval=config.ATLAS_VECTOR_INDEX_POLL_INTERVAL,
        )
    finally:
        client.close()


if __name__ == "__main__":
    main()
