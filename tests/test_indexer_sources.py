from video_processor.indexer import _build_source


def test_uploaded_graph_media_keeps_authorized_source_provenance():
    source = _build_source(
        "https://www.instagram.com/reel/example/",
        "instagram_graph_api",
        {"instagram_media_id": "media-1"},
        "provided_upload",
    )

    assert source["type"] == "instagram_graph_api"
    assert source["url"] == "https://www.instagram.com/reel/example/"
    assert source["fetcher"] == "provided_upload"
    assert source["metadata"] == {"instagram_media_id": "media-1"}
