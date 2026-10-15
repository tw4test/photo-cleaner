"""v0.6.5：大圖預覽的圖片來源（不需要資料庫）。"""
import io
from pathlib import Path

import pytest
from PIL import Image

from app.services import preview_service as ps


@pytest.fixture(autouse=True)
def _clean():
    ps.clear_cache()
    yield
    ps.clear_cache()


def _img(path: Path, size=(3000, 2000), fmt="JPEG"):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (200, 80, 40)).save(path, format=fmt)
    return path


class Dec:
    """替換 ps._decode：記錄呼叫並用 Pillow 讀檔（測試環境沒有 pillow-heif / rawpy）。"""

    def __init__(self, mp, result=None, boom=None):
        self.calls, self.result, self.boom = [], result, boom
        mp.setattr(ps, "_decode", self)

    def __call__(self, src, is_raw, is_video):
        self.calls.append((src, is_raw, is_video))
        if self.boom:
            raise self.boom
        return self.result if self.result is not None else Image.open(src)


@pytest.mark.parametrize("name,fmt,ctype", [("a.jpg", "JPEG", "image/jpeg"), ("a.png", "PNG", "image/png"), ("a.webp", "WEBP", "image/webp")])
def test_native_formats_are_returned_as_the_original_file(tmp_path, name, fmt, ctype):
    f = _img(tmp_path / name, size=(300, 200), fmt=fmt)
    pv = ps.render(str(f), None, "photo")
    assert (pv.path, pv.data, pv.media_type) == (str(f), None, ctype)


def test_companion_jpg_is_preferred_for_raw(tmp_path, mp=None):
    raw = tmp_path / "IMG.cr2"
    raw.write_bytes(b"\x00raw")
    jpg = _img(tmp_path / "IMG.jpg", size=(100, 80))
    assert ps.render(str(raw), str(jpg), "raw").path == str(jpg)
    assert ps.pick_source(str(raw), str(tmp_path / "nope.jpg"), "raw") == str(raw)   # 伴生檔不存在 → 退回主檔
    assert ps.pick_source(str(raw), str(jpg), "video") == str(raw)                  # 影片永遠不用伴生檔


def test_non_native_is_resized_to_jpeg_and_cached(tmp_path, monkeypatch):
    tif = _img(tmp_path / "big.tiff", size=(6000, 4000), fmt="TIFF")
    dec = Dec(monkeypatch)
    pv = ps.render(str(tif), None, "photo")
    out = Image.open(io.BytesIO(pv.data))
    assert pv.media_type == "image/jpeg" and out.format == "JPEG" and out.size == (2560, 1707)
    ps.render(str(tif), None, "photo")
    ps.render(str(tif), None, "photo")
    assert dec.calls == [(str(tif), False, False)]


def test_small_image_is_not_upscaled_and_raw_flag_is_passed(tmp_path, monkeypatch):
    raw = _img(tmp_path / "x.cr2", size=(320, 200), fmt="TIFF")
    dec = Dec(monkeypatch)
    out = Image.open(io.BytesIO(ps.render(str(raw), None, "raw").data))
    assert out.size == (320, 200) and dec.calls[0][1] is True


def test_cache_follows_file_changes_and_is_bounded(tmp_path, monkeypatch):
    tif = _img(tmp_path / "c.tiff", size=(500, 400), fmt="TIFF")
    dec = Dec(monkeypatch)
    ps.render(str(tif), None, "photo")
    _img(tif, size=(700, 400), fmt="TIFF")
    assert Image.open(io.BytesIO(ps.render(str(tif), None, "photo").data)).size == (700, 400) and len(dec.calls) == 2
    for i in range(ps.CACHE_ENTRIES + 5):
        ps.render(str(_img(tmp_path / f"{i}.tif", size=(40 + i, 30), fmt="TIFF")), None, "photo")
    assert len(ps._cache) == ps.CACHE_ENTRIES


@pytest.mark.parametrize("mode", ["RGBA", "P", "L"])
def test_other_modes_become_rgb_jpeg(tmp_path, monkeypatch, mode):
    tif = _img(tmp_path / "m.tif", size=(50, 40), fmt="TIFF")
    Dec(monkeypatch, result=Image.new(mode, (50, 40)))
    assert Image.open(io.BytesIO(ps.render(str(tif), None, "photo").data)).mode == "RGB"


def test_video_goes_through_the_decoder_with_the_video_flag(tmp_path, monkeypatch):
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"\x00v")
    dec = Dec(monkeypatch, result=Image.new("RGB", (320, 180)))
    assert ps.render(str(mp4), None, "video").media_type == "image/jpeg"
    assert dec.calls == [(str(mp4), False, True)]


def test_errors(tmp_path, monkeypatch):
    with pytest.raises(ps.PreviewNotFound):
        ps.render(str(tmp_path / "ghost.jpg"), None, "photo")
    tif = _img(tmp_path / "bad.tif", size=(10, 10), fmt="TIFF")
    Dec(monkeypatch, boom=ValueError("壞掉的檔案"))
    with pytest.raises(ps.PreviewDecodeError, match="壞掉的檔案"):
        ps.render(str(tif), None, "photo")
    monkeypatch.setattr(ps, "_decode", lambda *a: None)
    with pytest.raises(ps.PreviewDecodeError):
        ps.render(str(tif), None, "photo")
