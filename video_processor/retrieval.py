"""Versioned retrieval documents and Atlas Vector Search access for video Artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from google import genai
from google.genai import types
from pymongo import ASCENDING


DEFAULT_EMBEDDING_DIMENSIONS = 768


@dataclass(frozen=True)
class RetrievalContract:
    """The schema and prompt version that make one retrieval corpus."""

    trait_schema: str
    prompt_version: str


ACTIVE_RETRIEVAL_CONTRACT = RetrievalContract(
    trait_schema="reel-retrieval/v1",
    prompt_version="2026-08-07",
)


def resolve_retrieval_contract(
    *,
    trait_schema: Optional[str] = None,
    prompt_version: Optional[str] = None,
) -> RetrievalContract:
    """Return one complete retrieval contract; never combine version defaults."""
    if trait_schema is None and prompt_version is None:
        return ACTIVE_RETRIEVAL_CONTRACT
    if trait_schema is None or prompt_version is None:
        raise ValueError("trait_schema and prompt_version must be supplied together")
    return RetrievalContract(
        trait_schema=_required_string(trait_schema, "trait_schema"),
        prompt_version=_required_string(prompt_version, "prompt_version"),
    )


def _required_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _required_number(value: Any, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    return float(value)


def build_retrieval_documents(
    *,
    media_id: str,
    content_hash: str,
    trait_schema: str,
    prompt_version: str,
    retrieval: dict[str, Any],
) -> list[dict[str, Any]]:
    """Convert the validated dashboard retrieval contract into embeddable documents."""
    media_id = _required_string(media_id, "media_id")
    content_hash = _required_string(content_hash, "content_hash")
    trait_schema = _required_string(trait_schema, "trait_schema")
    prompt_version = _required_string(prompt_version, "prompt_version")
    if not isinstance(retrieval, dict):
        raise ValueError("retrieval must be an object")

    video_summary = _required_string(retrieval.get("videoSummary"), "retrieval.videoSummary")
    tags = retrieval.get("retrievalTags")
    chunks = retrieval.get("chunks")
    if not isinstance(tags, list) or not all(isinstance(tag, str) and tag.strip() for tag in tags):
        raise ValueError("retrieval.retrievalTags must be a list of non-empty strings")
    if not isinstance(chunks, list) or not chunks:
        raise ValueError("retrieval.chunks must be a non-empty list")

    base = {
        "media_id": media_id,
        "content_hash": content_hash,
        "trait_schema": trait_schema,
        "prompt_version": prompt_version,
        "retrieval_tags": [tag.strip() for tag in tags],
    }
    documents = [{
        **base,
        "chunk_id": "video",
        "kind": "video",
        "start_sec": None,
        "end_sec": None,
        "text": "\n".join([
            f"Video summary: {video_summary}",
            f"Retrieval tags: {', '.join(base['retrieval_tags'])}",
        ]),
    }]
    for index, chunk in enumerate(chunks):
        if not isinstance(chunk, dict):
            raise ValueError("retrieval.chunks entries must be objects")
        start_sec = _required_number(chunk.get("startSec"), f"retrieval.chunks[{index}].startSec")
        end_sec = _required_number(chunk.get("endSec"), f"retrieval.chunks[{index}].endSec")
        if start_sec < 0 or end_sec <= start_sec:
            raise ValueError(f"retrieval.chunks[{index}] must have 0 <= startSec < endSec")
        text = _required_string(chunk.get("text"), f"retrieval.chunks[{index}].text")
        visual_summary = _required_string(chunk.get("visualSummary"), f"retrieval.chunks[{index}].visualSummary")
        concepts = chunk.get("concepts")
        if not isinstance(concepts, list) or not all(isinstance(item, str) and item.strip() for item in concepts):
            raise ValueError(f"retrieval.chunks[{index}].concepts must be a list of non-empty strings")
        documents.append({
            **base,
            "chunk_id": f"chunk-{index}",
            "kind": "chunk",
            "start_sec": start_sec,
            "end_sec": end_sec,
            "text": "\n".join([
                f"Video summary: {video_summary}",
                f"Time range: {start_sec:g}s-{end_sec:g}s",
                f"Transcript or event: {text}",
                f"Visual summary: {visual_summary}",
                f"Concepts: {', '.join(item.strip() for item in concepts)}",
                f"Retrieval tags: {', '.join(base['retrieval_tags'])}",
            ]),
        })
    return documents


class RetrievalStore:
    """MongoDB persistence and Atlas Vector Search queries for retrieval documents."""

    def __init__(self, db):
        self._col = db["video_retrieval_documents"]
        self._col.create_index(
            [("media_id", ASCENDING), ("content_hash", ASCENDING), ("trait_schema", ASCENDING), ("prompt_version", ASCENDING), ("chunk_id", ASCENDING)],
            unique=True,
        )
        self._col.create_index([("content_hash", ASCENDING), ("start_sec", ASCENDING)])

    def replace_documents(self, documents: list[dict[str, Any]], embeddings: list[list[float]]) -> int:
        if not documents or len(documents) != len(embeddings):
            raise ValueError("documents and embeddings must be non-empty and have equal lengths")
        first = documents[0]
        identity = {key: first[key] for key in ("media_id", "content_hash", "trait_schema", "prompt_version")}
        if any(any(document[key] != value for key, value in identity.items()) for document in documents):
            raise ValueError("all retrieval documents must share a media and retrieval version")

        now = datetime.now(timezone.utc)
        chunk_ids = []
        for document, embedding in zip(documents, embeddings):
            if not embedding:
                raise ValueError("embedding response was empty")
            chunk_ids.append(document["chunk_id"])
            self._col.update_one(
                {**identity, "chunk_id": document["chunk_id"]},
                {"$set": {**document, "embedding": embedding, "updated_at": now}, "$setOnInsert": {"created_at": now}},
                upsert=True,
            )
        self._col.delete_many({**identity, "chunk_id": {"$nin": chunk_ids}})
        return len(documents)

    def search(
        self,
        *,
        query_embedding: list[float],
        index_name: str,
        limit: int,
        contract: RetrievalContract = ACTIVE_RETRIEVAL_CONTRACT,
    ) -> list[dict[str, Any]]:
        if not query_embedding:
            raise ValueError("query embedding was empty")
        if limit < 1 or limit > 25:
            raise ValueError("limit must be between 1 and 25")
        pipeline = [
            {"$vectorSearch": {
                "index": index_name,
                "path": "embedding",
                "queryVector": query_embedding,
                "numCandidates": min(max(limit * 20, 40), 500),
                "limit": limit,
                "filter": {
                    "trait_schema": contract.trait_schema,
                    "prompt_version": contract.prompt_version,
                },
            }},
            {"$project": {
                "_id": 0,
                "media_id": 1,
                "content_hash": 1,
                "trait_schema": 1,
                "prompt_version": 1,
                "chunk_id": 1,
                "kind": 1,
                "start_sec": 1,
                "end_sec": 1,
                "text": 1,
                "retrieval_tags": 1,
                "score": {"$meta": "vectorSearchScore"},
            }},
        ]
        return list(self._col.aggregate(pipeline))

    def get_context(
        self,
        *,
        content_hash: str,
        media_id: Optional[str] = None,
        contract: RetrievalContract = ACTIVE_RETRIEVAL_CONTRACT,
    ) -> list[dict[str, Any]]:
        query: dict[str, Any] = {
            "content_hash": content_hash,
            "trait_schema": contract.trait_schema,
            "prompt_version": contract.prompt_version,
        }
        if media_id:
            query["media_id"] = media_id
        return list(self._col.find(
            query,
            {"_id": 0, "embedding": 0},
        ).sort([("kind", ASCENDING), ("start_sec", ASCENDING)]))


def embed_texts(*, api_key: str, texts: list[str], model: str, dimensions: int, task_type: str) -> list[list[float]]:
    """Embed texts with a fixed dimensionality so the Atlas index is stable."""
    if not api_key:
        raise ValueError("GEMINI_API_KEY is required for video retrieval embeddings")
    if not texts:
        return []
    client = genai.Client(api_key=api_key)
    response = client.models.embed_content(
        model=model,
        contents=texts,
        config=types.EmbedContentConfig(task_type=task_type, output_dimensionality=dimensions),
    )
    embeddings = [list(item.values) for item in response.embeddings]
    if len(embeddings) != len(texts) or any(len(item) != dimensions for item in embeddings):
        raise ValueError("Gemini returned an unexpected embedding count or dimensionality")
    return embeddings


def index_retrieval_documents(
    *,
    store: RetrievalStore,
    config,
    media_id: str,
    content_hash: str,
    trait_schema: str,
    prompt_version: str,
    retrieval: dict[str, Any],
) -> int:
    documents = build_retrieval_documents(
        media_id=media_id,
        content_hash=content_hash,
        trait_schema=trait_schema,
        prompt_version=prompt_version,
        retrieval=retrieval,
    )
    embeddings = embed_texts(
        api_key=config.GEMINI_API_KEY,
        texts=[document["text"] for document in documents],
        model=config.RETRIEVAL_EMBEDDING_MODEL,
        dimensions=config.RETRIEVAL_EMBEDDING_DIMENSIONS,
        task_type="RETRIEVAL_DOCUMENT",
    )
    return store.replace_documents(documents, embeddings)


def search_retrieval_documents(
    *,
    store: RetrievalStore,
    config,
    query: str,
    limit: int,
    trait_schema: Optional[str] = None,
    prompt_version: Optional[str] = None,
) -> list[dict[str, Any]]:
    query = _required_string(query, "query")
    contract = resolve_retrieval_contract(
        trait_schema=trait_schema,
        prompt_version=prompt_version,
    )
    embedding = embed_texts(
        api_key=config.GEMINI_API_KEY,
        texts=[query],
        model=config.RETRIEVAL_EMBEDDING_MODEL,
        dimensions=config.RETRIEVAL_EMBEDDING_DIMENSIONS,
        task_type="RETRIEVAL_QUERY",
    )[0]
    return store.search(
        query_embedding=embedding,
        index_name=config.ATLAS_VECTOR_INDEX,
        limit=limit,
        contract=contract,
    )
