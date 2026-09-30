import io
import logging
from pathlib import Path
from typing import Optional
from PIL import Image

logger = logging.getLogger(__name__)

class RawDecoder:
    """
    真實 RAW 檔案解碼器。
    使用 rawpy (LibRaw) 提取內嵌高畫質預覽圖或解碼為 RGB 影像。
    若 rawpy 不可用或解碼發生例外，自動降級至 Pillow 原生解碼。
    """

    RAW_EXTENSIONS = {'.cr2', '.cr3', '.nef', '.arw', '.dng', '.orf', '.rw2', '.pef', '.raf'}

    def is_raw(self, file_path: str) -> bool:
        return Path(file_path).suffix.lower() in self.RAW_EXTENSIONS

    def decode_raw(self, file_path: str) -> Optional[bytes]:
        """
        解碼 RAW 檔案並回傳 JPEG/RGB 位元組資料。
        """
        try:
            import rawpy
            with rawpy.imread(file_path) as raw:
                # 優先提取內嵌 JPEG 縮圖 (速度最快且品質高)
                try:
                    thumb = raw.extract_thumb()
                    if thumb.format == rawpy.ThumbFormat.JPEG:
                        return thumb.data
                    elif thumb.format == rawpy.ThumbFormat.BITMAP:
                        img = Image.fromarray(thumb.data)
                        buf = io.BytesIO()
                        img.save(buf, format="JPEG", quality=85)
                        return buf.getvalue()
                except Exception:
                    pass

                # 若無內嵌縮圖，使用 half_size 快速後處理
                rgb = raw.postprocess(half_size=True, use_camera_wb=True)
                img = Image.fromarray(rgb)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=85)
                return buf.getvalue()
        except Exception as e:
            logger.debug(f"rawpy decoding failed for {file_path}, trying Pillow fallback: {e}")

        # Fallback: Pillow原生讀取 (適用於 DNG 或 TIFF 基礎 RAW)
        try:
            with Image.open(file_path) as img:
                buf = io.BytesIO()
                img.convert("RGB").save(buf, format="JPEG", quality=85)
                return buf.getvalue()
        except Exception as e:
            logger.warning(f"Raw fallback decoding also failed for {file_path}: {e}")
            return None

    def decode_thumbnail(self, file_path: str, target_size: tuple[int, int] = (320, 240)) -> Optional[Image.Image]:
        """
        解碼 RAW 檔案並產生指定尺寸之 PIL 預覽圖。
        """
        raw_bytes = self.decode_raw(file_path)
        if raw_bytes:
            try:
                img = Image.open(io.BytesIO(raw_bytes))
                img.thumbnail(target_size)
                return img
            except Exception:
                pass
        return None

raw_decoder = RawDecoder()
