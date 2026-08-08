from types import SimpleNamespace
from unittest.mock import patch

import pytest

from video_processor.retrieval import (
    ACTIVE_RETRIEVAL_CONTRACT,
    RetrievalStore,
    build_retrieval_documents,
    index_retrieval_documents,
    search_retrieval_documents,
)


RETRIEVAL = {
    "videoSummary": "A creator builds a fence while acting out a relationship meme.",
    "retrievalTags": ["home renovation", "couple meme", "fence"],
    "chunks": [{
        "startSec": 0,
        "endSec": 5,
        "text": "A man uses a drill while reacting to advice.",
        "visualSummary": "Low-angle view of a wooden fence and power drill.",
        "concepts": ["DIY", "power drill", "relationship humour"],
    }],
}


def test_build_retrieval_documents_keeps_whole_video_and_timestamped_chunk():
    documents = build_retrieval_documents(
        media_id="media-1",
        content_hash="sha256:example",
        trait_schema="reel-retrieval/v1",
        prompt_version="2026-08-07",
        retrieval=RETRIEVAL,
    )

    assert [document["chunk_id"] for document in documents] == ["video", "chunk-0"]
    assert documents[1]["start_sec"] == 0
    assert documents[1]["end_sec"] == 5
    assert "power drill" in documents[1]["text"]


def test_retrieval_indexing_embeds_document_text_before_storing():
    class Store:
        def replace_documents(self, documents, embeddings):
            self.documents = documents
            self.embeddings = embeddings
            return len(documents)

    store = Store()
    config = SimpleNamespace(
        GEMINI_API_KEY="key",
        RETRIEVAL_EMBEDDING_MODEL="gemini-embedding-001",
        RETRIEVAL_EMBEDDING_DIMENSIONS=3,
    )
    with patch("video_processor.retrieval.embed_texts", return_value=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]) as embed:
        count = index_retrieval_documents(
            store=store,
            config=config,
            media_id="media-1",
            content_hash="sha256:example",
            trait_schema="reel-retrieval/v1",
            prompt_version="2026-08-07",
            retrieval=RETRIEVAL,
        )

    assert count == 2
    assert len(store.documents) == 2
    assert store.embeddings[0] == [0.1, 0.2, 0.3]
    assert embed.call_args.kwargs["task_type"] == "RETRIEVAL_DOCUMENT"


class _RecordingCursor:
    def __init__(self, documents):
        self.documents = documents
        self.sort_args = None

    def sort(self, sort_args):
        self.sort_args = sort_args
        return self

    def __iter__(self):
        return iter(self.documents)


class _RecordingCollection:
    def __init__(self):
        self.pipeline = None
        self.find_query = None
        self.find_projection = None

    def aggregate(self, pipeline):
        self.pipeline = pipeline
        return [{"content_hash": "active-result"}]

    def find(self, query, projection):
        self.find_query = query
        self.find_projection = projection
        return _RecordingCursor([{"content_hash": "active-context"}])


def _store_with_recording_collection():
    store = object.__new__(RetrievalStore)
    store._col = _RecordingCollection()
    return store


def test_search_defaults_to_the_active_retrieval_contract():
    store = _store_with_recording_collection()
    config = SimpleNamespace(
        GEMINI_API_KEY="key",
        RETRIEVAL_EMBEDDING_MODEL="gemini-embedding-001",
        RETRIEVAL_EMBEDDING_DIMENSIONS=3,
        ATLAS_VECTOR_INDEX="video_retrieval_vector",
    )

    with patch("video_processor.retrieval.embed_texts", return_value=[[0.1, 0.2, 0.3]]):
        results = search_retrieval_documents(
            store=store,
            config=config,
            query="fence installation",
            limit=8,
        )

    assert results == [{"content_hash": "active-result"}]
    vector_search = store._col.pipeline[0]["$vectorSearch"]
    assert vector_search["filter"] == {
        "trait_schema": ACTIVE_RETRIEVAL_CONTRACT.trait_schema,
        "prompt_version": ACTIVE_RETRIEVAL_CONTRACT.prompt_version,
    }


def test_context_defaults_to_the_active_retrieval_contract():
    store = _store_with_recording_collection()

    context = store.get_context(content_hash="sha256:example", media_id="media-1")

    assert context == [{"content_hash": "active-context"}]
    assert store._col.find_query == {
        "content_hash": "sha256:example",
        "media_id": "media-1",
        "trait_schema": ACTIVE_RETRIEVAL_CONTRACT.trait_schema,
        "prompt_version": ACTIVE_RETRIEVAL_CONTRACT.prompt_version,
    }


def test_search_rejects_a_partial_contract_override():
    with pytest.raises(ValueError, match="must be supplied together"):
        search_retrieval_documents(
            store=_store_with_recording_collection(),
            config=SimpleNamespace(),
            query="fence installation",
            limit=8,
            trait_schema="reel-retrieval/v2",
        )
