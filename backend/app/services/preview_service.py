"""
preview_service.py（v0.6.5）— 審核頁「大圖預覽」的圖片來源。

規則
- 瀏覽器能直接顯示的格式（jpg / png / webp / gif / bmp）：原檔直接回傳（完整畫質，可放大到 100%）。
- 其他格式（HEIC / TIFF / RAW）與影片：解碼後縮成最長邊 PREVIEW_MAX_SIDE 的 JPEG；影片只取第一幀。
- RAW + JPG：優先用伴生的 JPG。
- 路徑一律來自資料庫（asset id），這裡不接受任何由使用者傳入的路徑。
- 解碼結果放在記憶體 LRU（CACHE_ENTRIES 張），來回切換同一張不會重複解碼。
"""
import io
import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image

logger = logging.getLogger(__name__)

PREVIEW_MAX_SIDE = 2560
JPEG_QUALITY = 88
CACHE_ENTRIES = 12

NATIVE_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
}
RAW_EXTS = {".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".rw2", ".pef", ".raf"}


class PreviewNotFound(Exception):
    """檔案不在磁碟上（已移動、刪除或磁碟離線）。"""


class PreviewDecodeError(Exception):
    """檔案存在但無法解碼成圖片。"""


@dataclass(frozen=True)
class Preview:
    media_type: str
    path: Optional[str] = None     # 直接回傳原檔
    data: Optional[bytes] = None   # 或回傳已解碼的 JPEG


_cache: "OrderedDict[Tuple[str, int, int], bytes]" = OrderedDict()
_lock = threading.Lock()


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def pick_source(primary: str, companion: Optional[str], media_type: str) -> str:
    """RAW + JPG 時優先用伴生 JPG（瀏覽器可直接顯示、速度也快很多）。"""
    if media_type != "video" and companion and Path(companion).exists():
        return companion
    return primary


def _decode(src: str, is_raw: bool, is_video: bool) -> Optional[Image.Image]:
    """真正解碼的地方（測試會替換）。延遲匯入，避免載入 OpenCV / imagehash 等較重的模組。"""
    if is_video:
        from app.stubs.video_probe_stub import video_probe
        return video_probe.extract_first_frame(src)
    from app.services.feature_pipeline import feature_pipeline
    img, _meta = feature_pipeline.open_image(src, is_raw=is_raw, max_side=PREVIEW_MAX_SIDE)
    return img


def _encode_jpeg(img: Image.Image) -> bytes:
    if img.mode != "RGB":
        has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
        if has_alpha:
            rgba = img.convert("RGBA")
            bg = Image.new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.split()[-1])
            img = bg
        else:
            img = img.convert("RGB")
    if max(img.size) > PREVIEW_MAX_SIDE:
        img.thumbnail((PREVIEW_MAX_SIDE, PREVIEW_MAX_SIDE), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY)
    return buf.getvalue()


def render(primary: str, companion: Optional[str], media_type: str) -> Preview:
    src = pick_source(primary, companion, media_type)
    p = Path(src)
    try:
        st = p.stat()
    except OSError as exc:
        raise PreviewNotFound(src) from exc

    is_video = media_type == "video"
    ext = p.suffix.lower()
    if not is_video and ext in NATIVE_TYPES:
        return Preview(media_type=NATIVE_TYPES[ext], path=src)

    key = (src, st.st_mtime_ns, st.st_size)
    with _lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return Preview(media_type="image/jpeg", data=hit)

    try:
        img = _decode(src, ext in RAW_EXTS, is_video)
    except Exception as exc:  # noqa: BLE001
        logger.warning("preview: 解碼失敗 %s — %s", src, exc)
        raise PreviewDecodeError(f"{type(exc).__name__}: {exc}") from exc
    if img is None:
        raise PreviewDecodeError("讀不到影像內容")
    try:
        data = _encode_jpeg(img)
    except Exception as exc:  # noqa: BLE001
        raise PreviewDecodeError(f"{type(exc).__name__}: {exc}") from exc

    with _lock:
        _cache[key] = data
        _cache.move_to_end(key)
        while len(_cache) > CACHE_ENTRIES:
            _cache.popitem(last=False)
    return Preview(media_type="image/jpeg", data=data)
