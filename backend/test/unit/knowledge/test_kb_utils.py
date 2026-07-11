import pytest

from yuxi.knowledge.utils.kb_utils import parse_minio_url, prepare_item_metadata


async def test_prepare_item_metadata_preserves_uploaded_file_size():
    item = "minio://knowledgebases/db/upload/demo.txt"
    params = {
        "content_hashes": {item: "hash"},
        "file_sizes": {item: 1234},
    }

    metadata = await prepare_item_metadata(item, "file", "db", params=params)

    assert metadata["size"] == 1234
    assert "file_sizes" not in (metadata.get("processing_params") or {})


async def test_prepare_item_metadata_uses_source_path_as_display_filename():
    item = "minio://knowledgebases/db/upload/intro_1710000000000.md"
    params = {
        "content_hashes": {item: "hash"},
        "source_path": "guides/setup/Intro.MD",
    }

    metadata = await prepare_item_metadata(item, "file", "db", params=params)

    assert metadata["filename"] == "guides/setup/Intro.MD"
    assert metadata["file_type"] == "md"
    assert metadata["path"] == item


def test_parse_minio_url_preserves_semicolon_object_name():
    item = (
        "http://localhost:9000/knowledgebases/kb_8nqjxjpitx/upload/"
        "FFE186A3-2E47-4E01-AF15-1E1048CB42D2_丙酮氰醇;2-氰基丙基-2-醇_75-86-5_1783737364782.pdf"
    )

    bucket_name, object_name = parse_minio_url(item)

    assert bucket_name == "knowledgebases"
    assert object_name == (
        "kb_8nqjxjpitx/upload/"
        "FFE186A3-2E47-4E01-AF15-1E1048CB42D2_丙酮氰醇;2-氰基丙基-2-醇_75-86-5_1783737364782.pdf"
    )


async def test_prepare_item_metadata_preserves_semicolon_pdf_filename():
    item = (
        "http://localhost:9000/knowledgebases/kb_8nqjxjpitx/upload/"
        "FFE186A3-2E47-4E01-AF15-1E1048CB42D2_丙酮氰醇;2-氰基丙基-2-醇_75-86-5_1783737364782.pdf"
    )
    params = {"content_hashes": {item: "hash"}}

    metadata = await prepare_item_metadata(item, "file", "kb_8nqjxjpitx", params=params)

    assert metadata["filename"] == "FFE186A3-2E47-4E01-AF15-1E1048CB42D2_丙酮氰醇;2-氰基丙基-2-醇_75-86-5.pdf"
    assert metadata["file_type"] == "pdf"
    assert metadata["path"] == item


async def test_prepare_item_metadata_preserves_preprocessed_file_size():
    item = "minio://knowledgebases/db/upload/page.html"
    params = {
        "_preprocessed_map": {
            item: {
                "path": item,
                "content_hash": "hash",
                "filename": "https://example.com",
                "file_size": 5678,
            }
        }
    }

    metadata = await prepare_item_metadata(item, "file", "db", params=params)

    assert metadata["size"] == 5678
    assert "_preprocessed_map" not in (metadata.get("processing_params") or {})


async def test_prepare_item_metadata_rejects_direct_url_content_type():
    with pytest.raises(ValueError, match="Unsupported content_type"):
        await prepare_item_metadata("https://example.com", "url", "db")
