import importlib.util
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "setup_atlas_vector_index.py"
SPEC = importlib.util.spec_from_file_location("setup_atlas_vector_index", SCRIPT_PATH)
setup_index = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup_index)


class _SearchIndexCollection:
    def __init__(self, index):
        self.index = index

    def list_search_indexes(self, name):
        assert name == "video_retrieval_vector"
        return [self.index]


def test_index_definition_indexes_version_filter_fields():
    fields = setup_index.index_definition(768)["fields"]

    assert {field["path"] for field in fields if field["type"] == "filter"} == {
        "trait_schema",
        "prompt_version",
    }


def test_existing_index_must_be_ready_and_compatible():
    collection = _SearchIndexCollection({
        "status": "READY",
        "queryable": True,
        "definition": setup_index.index_definition(768),
    })

    setup_index.wait_until_usable(
        collection,
        index_name="video_retrieval_vector",
        dimensions=768,
        timeout=1,
        poll_interval=1,
    )


def test_existing_index_missing_version_filter_is_rejected():
    collection = _SearchIndexCollection({
        "status": "READY",
        "definition": {
            "fields": [{
                "type": "vector",
                "path": "embedding",
                "numDimensions": 768,
                "similarity": "cosine",
            }],
        },
    })

    with pytest.raises(RuntimeError, match="missing filter fields"):
        setup_index.wait_until_usable(
            collection,
            index_name="video_retrieval_vector",
            dimensions=768,
            timeout=1,
            poll_interval=1,
        )
