import os
import io
import shutil
import tempfile
from pathlib import Path
from PIL import Image
import pytest

from app.services.thumbnail import ThumbnailService, normalize_windows_path
from app.stubs.video_probe_stub import video_probe


@pytest.fixture
def temp_service(tmp_path):
    cache_dir = tmp_path / "thumbs_cache"
    return ThumbnailService(cache_dir=cache_dir)


def test_normalize_windows_path():
    p1 = "d:/test/folder/photo.jpg"
    norm1 = normalize_windows_path(p1)
    assert "/" not in norm1
    assert norm1.startswith("D:\\") or norm1.startswith("d:\\")


def test_cache_key_invalidation_on_mtime_or_size_change(temp_service):
    path = "D:\\Photos\\IMG_0001.JPG"
    key1 = temp_service.get_cache_key(path, mtime=1000.0, size=50000)
    key2 = temp_service.get_cache_key(path, mtime=1000.0, size=50000)
    # Same metadata -> same key
    assert key1 == key2

    # Modified mtime -> new key
    key3 = temp_service.get_cache_key(path, mtime=2000.0, size=50000)
    assert key1 != key3

    # Modified size -> new key
    key4 = temp_service.get_cache_key(path, mtime=1000.0, size=60000)
    assert key1 != key4


def test_same_filename_different_paths(temp_service):
    path_a = "D:\\FolderA\\IMG_0001.JPG"
    path_b = "D:\\FolderB\\IMG_0001.JPG"
    key_a = temp_service.get_cache_key(path_a, mtime=1000.0, size=50000)
    key_b = temp_service.get_cache_key(path_b, mtime=1000.0, size=50000)
    assert key_a != key_b


def test_sharded_thumbnail_path(temp_service):
    path = "D:\\Photos\\test.jpg"
    thumb_path = temp_service.get_thumbnail_path(path, mtime=1000.0, size=50000)
    key = temp_service.get_cache_key(path, mtime=1000.0, size=50000)

    # Must be 2-level directory sharding: cache_dir / key[:2] / key[2:4] / key.jpg
    expected_rel = Path(key[:2]) / key[2:4] / f"{key}.jpg"
    assert thumb_path.resolve() == (temp_service.cache_dir / expected_rel).resolve()


def test_generate_thumbnail_standard_image(temp_service, tmp_path):
    img_path = tmp_path / "sample.jpg"
    img = Image.new("RGB", (1000, 800), color=(120, 180, 240))
    img.save(img_path, format="JPEG")

    thumb = temp_service.generate_thumbnail(str(img_path))
    assert thumb is not None
    assert thumb.exists()

    with Image.open(thumb) as out:
        assert out.size[0] <= 320 and out.size[1] <= 320


def test_companion_jpg_priority(temp_service, tmp_path):
    raw_path = tmp_path / "photo.CR2"
    raw_path.write_bytes(b"dummy_raw_bytes")
    jpg_path = tmp_path / "photo.JPG"
    img = Image.new("RGB", (600, 400), color=(255, 0, 0))
    img.save(jpg_path, format="JPEG")

    thumb = temp_service.generate_thumbnail(
        file_path=str(raw_path),
        companion_path=str(jpg_path)
    )
    assert thumb is not None
    assert thumb.exists()

    # Loaded image is from companion JPG (red color)
    with Image.open(thumb) as out:
        rgb = out.getpixel((10, 10))
        assert rgb[0] > 200 and rgb[1] < 50


def test_no_error_placeholder_saved_to_disk_on_failure(temp_service, tmp_path):
    corrupt_file = tmp_path / "corrupt.jpg"
    corrupt_file.write_bytes(b"not_an_image")

    thumb = temp_service.generate_thumbnail(str(corrupt_file))
    assert thumb is None

    # Disk must not contain error placeholders
    assert not temp_service.has_thumbnail(str(corrupt_file))


def test_cleanup_orphan_thumbnails(temp_service, tmp_path):
    img1 = tmp_path / "active.jpg"
    img2 = tmp_path / "orphan.jpg"
    Image.new("RGB", (200, 200), color=(10, 20, 30)).save(img1)
    Image.new("RGB", (200, 200), color=(40, 50, 60)).save(img2)

    thumb1 = temp_service.generate_thumbnail(str(img1))
    thumb2 = temp_service.generate_thumbnail(str(img2))
    assert thumb1.exists() and thumb2.exists()

    key1 = temp_service.get_cache_key(str(img1))
    # Only key1 is valid in database
    result = temp_service.cleanup_orphan_thumbnails(valid_cache_keys={key1})

    assert result["deleted_count"] >= 1
    assert thumb1.exists()
    assert not thumb2.exists()
