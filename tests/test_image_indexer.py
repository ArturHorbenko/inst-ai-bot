from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from video_processor.image_indexer import (
    create_contact_sheet,
    hash_ordered_image_bytes,
    index_images,
    validate_image_paths,
)


def make_image(path, color, size=(12, 6)):
    Image.new("RGB", size, color).save(path)


class MemoryArtifactStore:
    def __init__(self):
        self.artifacts = {}
        self.upsert_calls = 0

    def get_by_hash(self, content_hash):
        return self.artifacts.get(content_hash)

    def upsert(self, artifact):
        self.upsert_calls += 1
        self.artifacts.setdefault(artifact["content_hash"], artifact)
        return self.artifacts[artifact["content_hash"]]

    def append_source(self, content_hash, source):
        self.artifacts[content_hash]["sources"].append(source)


def test_validate_image_paths_rejects_empty_too_many_and_unsupported(tmp_path):
    with pytest.raises(ValueError, match="between 1 and 10"):
        validate_image_paths([])
    with pytest.raises(ValueError, match="between 1 and 10"):
        validate_image_paths([tmp_path / f"{number}.png" for number in range(11)])

    unsupported = tmp_path / "slide.gif"
    unsupported.write_bytes(b"not an image")
    with pytest.raises(ValueError, match="Unsupported image format: .gif"):
        validate_image_paths([unsupported])


def test_ordered_original_bytes_produce_order_sensitive_hash(tmp_path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    make_image(first, "red")
    make_image(second, "blue")

    assert hash_ordered_image_bytes([first, second]) != hash_ordered_image_bytes([second, first])


def test_create_contact_sheet_preserves_order_and_writes_png(tmp_path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    output = tmp_path / "carousel.png"
    make_image(first, "red")
    make_image(second, "blue")

    create_contact_sheet([first, second], output)

    with Image.open(output) as contact_sheet:
        assert contact_sheet.format == "PNG"
        assert contact_sheet.size == (24, 6)
        assert contact_sheet.getpixel((2, 2)) == (255, 0, 0)
        assert contact_sheet.getpixel((14, 2)) == (0, 0, 255)


def test_index_images_is_idempotent_and_keeps_image_provenance(tmp_path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    make_image(first, "red")
    make_image(second, "blue")
    store = MemoryArtifactStore()
    config = SimpleNamespace(VIDEO_DIR=str(tmp_path / "artifacts"))

    artifact = index_images(
        [first, second], config, store,
        source_url="https://www.instagram.com/p/example/",
        source_type="instagram_graph_api",
        source_metadata={"instagram_media_id": "media-1"},
    )
    repeated = index_images([first, second], config, store)

    assert repeated["content_hash"] == artifact["content_hash"]
    assert store.upsert_calls == 1
    assert artifact["video_file_ref"].endswith(".png")
    assert artifact["transcript"] == {"text": "", "segments": [], "model": None}
    assert artifact["sources"][0]["type"] == "instagram_graph_api"
    assert artifact["sources"][0]["metadata"]["instagram_media_id"] == "media-1"
    assert artifact["sources"][0]["metadata"]["image_upload"] == {
        "count": 2,
        "filenames": ["first.png", "second.png"],
        "representation": "contact_sheet",
    }


def test_duplicate_image_bytes_reuse_artifact_and_link_distinct_competitor_provenance(tmp_path):
    image = tmp_path / "image.png"
    make_image(image, "red")
    store = MemoryArtifactStore()
    config = SimpleNamespace(VIDEO_DIR=str(tmp_path / "artifacts"))

    first = index_images(
        [image], config, store, source_type="instagram_graph_api_competitor",
        source_metadata={"competitor_id": "one", "instagram_media_id": "media-1"},
    )
    repeated = index_images(
        [image], config, store, source_type="instagram_graph_api_competitor",
        source_metadata={"competitor_id": "two", "instagram_media_id": "media-2"},
    )

    assert repeated["content_hash"] == first["content_hash"]
    assert store.upsert_calls == 1
    assert [source["metadata"]["competitor_id"] for source in repeated["sources"]] == ["one", "two"]


def test_images_endpoint_accepts_multiple_authorized_uploads(tmp_path, monkeypatch):
    import server

    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    make_image(first, "red")
    make_image(second, "blue")
    monkeypatch.setattr(server, "artifact_store", MemoryArtifactStore())
    monkeypatch.setattr(server, "config", SimpleNamespace(VIDEO_DIR=str(tmp_path / "artifacts")))
    headers = {"X-API-Key": server.API_KEY} if server.API_KEY else {}

    response = TestClient(server.app).post(
        "/artifacts/images",
        headers=headers,
        data={
            "source_url": "https://www.instagram.com/p/example/",
            "source_type": "instagram_graph_api",
            "source_metadata_json": '{"instagram_media_id":"media-1"}',
        },
        files=[
            ("images", ("first.png", first.read_bytes(), "image/png")),
            ("images", ("second.png", second.read_bytes(), "image/png")),
        ],
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["sources"][0]["metadata"]["image_upload"]["filenames"] == [
        "first.png", "second.png"
    ]


def test_existing_video_upload_endpoint_remains_available(monkeypatch):
    import server

    expected = {"content_hash": "sha256:video"}
    monkeypatch.setattr(server, "index_video", lambda *args, **kwargs: expected)
    headers = {"X-API-Key": server.API_KEY} if server.API_KEY else {}

    response = TestClient(server.app).post(
        "/artifacts/upload",
        headers=headers,
        files={"video": ("clip.mp4", b"authorized-video-bytes", "video/mp4")},
    )

    assert response.status_code == 200
    assert response.json() == expected
