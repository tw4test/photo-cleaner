import hashlib
from pathlib import Path
from PIL import Image, ImageDraw
import pillow_heif

from app.core.config import settings
from app.stubs.raw_stub import raw_decoder
from app.stubs.video_probe_stub import video_probe

# Register HEIF opener with Pillow
pillow_heif.register_heif_opener()

class ThumbnailService:
    """
    縮圖生成與快取服務。
    統一生成長邊 320px 之高品質縮圖並快取於 thumbs_cache 目錄。
    """

    def __init__(self):
        self.cache_dir = settings.THUMBS_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get_thumbnail_path(self, file_path: str) -> Path:
        """根據檔案路徑的 SHA-256 生成快取路徑"""
        cache_key = hashlib.sha256(file_path.encode('utf-8')).hexdigest()
        sub_dir = self.cache_dir / cache_key[:2]
        sub_dir.mkdir(parents=True, exist_ok=True)
        return sub_dir / f"{cache_key}.jpg"

    def generate_thumbnail(self, file_path: str, size: tuple[int, int] = (320, 320)) -> Path:
        thumb_path = self.get_thumbnail_path(file_path)
        if thumb_path.exists():
            return thumb_path

        p = Path(file_path)
        ext = p.suffix.lower()

        try:
            if raw_decoder.is_raw(file_path):
                img = raw_decoder.decode_thumbnail(file_path, size)
            elif video_probe.is_video(file_path):
                # 影片通用縮圖
                img = Image.new("RGB", size, color=(15, 23, 42))
                draw = ImageDraw.Draw(img)
                draw.text((size[0] // 2 - 30, size[1] // 2 - 10), "VIDEO", fill=(148, 163, 184))
            else:
                with Image.open(file_path) as src:
                    img = src.convert("RGB")
                    img.thumbnail(size, Image.Resampling.LANCZOS)

            img.save(thumb_path, format="JPEG", quality=80)
            return thumb_path
        except Exception:
            # 建立錯誤佔位圖
            err_img = Image.new("RGB", size, color=(30, 41, 59))
            draw = ImageDraw.Draw(err_img)
            draw.text((size[0] // 2 - 40, size[1] // 2 - 10), "PREVIEW ERR", fill=(239, 68, 68))
            err_img.save(thumb_path, format="JPEG", quality=80)
            return thumb_path

thumbnail_service = ThumbnailService()
