import os
import hashlib
import logging
from pathlib import Path
from typing import Optional, Tuple, Set, Dict, Any

from PIL import Image, ImageOps
import pillow_heif

from app.core.config import settings
from app.stubs.raw_stub import raw_decoder
from app.stubs.video_probe_stub import video_probe

logger = logging.getLogger(__name__)

# Register HEIF opener with Pillow for Apple HEIC/HEIF images
pillow_heif.register_heif_opener()

THUMB_SIZE: Tuple[int, int] = (320, 320)
_IMAGE_COMPANION_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}


def normalize_windows_path(path_str: str) -> str:
    """
    Standardize Windows / UNC / POSIX file paths to a uniform, canonical format:
    - Resolves relative path symbols
    - Uses uppercase drive letter on Windows (e.g. 'D:\\...')
    - Standardizes backslash delimiters for Windows / UNC
    """
    if not path_str:
        return ""
    try:
        p = Path(path_str).resolve()
        norm = str(p)
        if len(norm) >= 2 and norm[1] == ":" and norm[0].isalpha():
            norm = norm[0].upper() + norm[1:]
        return norm.replace("/", "\\")
    except Exception:
        return path_str.replace("/", "\\")


def pick_image_companion(paired_paths) -> Optional[str]:
    """從配對檔案中挑出可當縮圖來源的圖片（RAW+JPG 的 JPG），忽略 MOV 等影片。"""
    for p in paired_paths or []:
        if Path(p).suffix.lower() in _IMAGE_COMPANION_EXTS:
            return p
    return None


def thumb_key_args(asset) -> Dict[str, Any]:
    """
    由 Asset 推算縮圖快取鍵所需參數。basic_stage、clip_stage、縮圖 API、
    cleanup 一律透過此函式，確保快取鍵一致。
    """
    primary_file = None
    for f in (asset.files or []):
        if f.role == "primary":
            primary_file = f
            break
    if primary_file is None and asset.files:
        primary_file = asset.files[0]
    return {
        "file_path": asset.primary_path,
        "mtime": primary_file.mtime if primary_file else None,
        "size": primary_file.size if primary_file else None,
        "companion_path": pick_image_companion(asset.paired_paths),
    }


class ThumbnailService:
    """
    高效能縮圖生成與快取服務（支援 100k+ 巨量相片規模）：
    1. 純中繼資料快取鍵 sha256(path + size + mtime + companion)。
    2. 雙層目錄分片 (65,536 子目錄)，只在寫入時建立目錄。
    3. Pillow draft() DCT 降採樣加速與 RAW 內嵌預覽提取。
    4. RAW+JPG 伴生圖優先、影片擷取第一幀。
    5. 失敗不快取佔位圖。
    """

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = Path(cache_dir or settings.THUMBS_CACHE_DIR)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get_cache_key(
        self,
        file_path: str,
        mtime: Optional[float] = None,
        size: Optional[int] = None,
        companion_path: Optional[str] = None
    ) -> str:
        norm_path = normalize_windows_path(file_path)
        norm_companion = normalize_windows_path(companion_path) if companion_path else ""
        if mtime is None or size is None:
            try:
                p = Path(norm_path)
                if p.exists():
                    st = p.stat()
                    mtime = st.st_mtime if mtime is None else mtime
                    size = st.st_size if size is None else size
            except Exception:
                pass
        mtime_val = f"{mtime:.4f}" if mtime is not None else "0"
        size_val = str(size) if size is not None else "0"
        canonical_str = f"{norm_path}|{size_val}|{mtime_val}|{norm_companion}"
        return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()

    def get_thumbnail_path(
        self,
        file_path: str,
        mtime: Optional[float] = None,
        size: Optional[int] = None,
        companion_path: Optional[str] = None
    ) -> Path:
        """`cache_dir / key[0:2] / key[2:4] / {key}.jpg`（不建立目錄）。"""
        if len(file_path) == 64 and all(c in "0123456789abcdefABCDEF" for c in file_path):
            cache_key = file_path.lower()
        else:
            cache_key = self.get_cache_key(file_path=file_path, mtime=mtime, size=size,
                                           companion_path=companion_path)
        return self.cache_dir / cache_key[:2] / cache_key[2:4] / f"{cache_key}.jpg"

    def has_thumbnail(
        self,
        file_path: str,
        mtime: Optional[float] = None,
        size: Optional[int] = None,
        companion_path: Optional[str] = None
    ) -> bool:
        return self.get_thumbnail_path(file_path, mtime=mtime, size=size, companion_path=companion_path).exists()

    def _write(self, thumb_path: Path, img: Image.Image) -> Path:
        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = thumb_path.with_suffix(f".{os.getpid()}.{id(img)}.tmp")
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.save(temp_path, format="JPEG", quality=80)
        temp_path.replace(thumb_path)
        return thumb_path

    def save_from_image(
        self,
        img: Image.Image,
        file_path: str,
        companion_path: Optional[str] = None,
        mtime: Optional[float] = None,
        size: Optional[int] = None,
        target_size: Tuple[int, int] = THUMB_SIZE,
    ) -> Path:
        """
        由已解碼（且已依 EXIF 轉正）的影像直接寫入縮圖快取。
        basic_stage 用它來避免同一張相片被讀兩次。
        """
        thumb_path = self.get_thumbnail_path(file_path, mtime=mtime, size=size, companion_path=companion_path)
        t = img.copy()
        t.thumbnail(target_size, Image.Resampling.LANCZOS)
        return self._write(thumb_path, t)

    def generate_thumbnail(
        self,
        file_path: str,
        companion_path: Optional[str] = None,
        mtime: Optional[float] = None,
        size: Optional[int] = None,
        target_size: Tuple[int, int] = THUMB_SIZE
    ) -> Optional[Path]:
        """
        生成指定檔案之縮圖並存入分片快取；已存在則直接回傳。失敗回傳 None（不寫佔位圖）。
        """
        thumb_path = self.get_thumbnail_path(file_path=file_path, mtime=mtime, size=size,
                                             companion_path=companion_path)
        if thumb_path.exists():
            return thumb_path
        img: Optional[Image.Image] = None
        try:
            # 1. RAW+JPG：優先讀伴生 JPG
            if companion_path and Path(companion_path).exists():
                if Path(companion_path).suffix.lower() in _IMAGE_COMPANION_EXTS:
                    try:
                        with Image.open(companion_path) as src:
                            try:
                                src.draft("RGB", target_size)
                            except Exception:
                                pass
                            img = ImageOps.exif_transpose(src).convert("RGB")
                            img.thumbnail(target_size, Image.Resampling.LANCZOS)
                    except Exception as e:
                        logger.debug(f"Companion decode failed for {companion_path}: {e}")
                        img = None
            # 2. 影片
            if img is None and video_probe.is_video(file_path):
                img = video_probe.extract_first_frame(file_path)
                if img is not None:
                    img.thumbnail(target_size, Image.Resampling.LANCZOS)
            # 3. RAW
            elif img is None and raw_decoder.is_raw(file_path):
                img = raw_decoder.decode_thumbnail(file_path, target_size)
            # 4. 一般圖片
            elif img is None and Path(file_path).exists():
                with Image.open(file_path) as src:
                    try:
                        src.draft("RGB", target_size)
                    except Exception:
                        pass
                    img = ImageOps.exif_transpose(src).convert("RGB")
                    img.thumbnail(target_size, Image.Resampling.LANCZOS)
            if img is not None:
                return self._write(thumb_path, img)
        except Exception as exc:
            logger.warning(f"Failed to generate thumbnail for {file_path}: {exc}")
        return None

    def cleanup_orphan_thumbnails(self, valid_cache_keys: Set[str]) -> Dict[str, Any]:
        """移除不在 valid_cache_keys 中的孤兒縮圖。"""
        deleted_count = 0
        reclaimed_bytes = 0
        valid_keys_lower = {k.lower() for k in valid_cache_keys}
        if not self.cache_dir.exists():
            return {"deleted_count": 0, "reclaimed_bytes": 0}
        for root, dirs, files in os.walk(self.cache_dir, topdown=False):
            for file in files:
                if file.endswith(".jpg"):
                    stem = Path(file).stem.lower()
                    full_file_path = Path(root) / file
                    if stem not in valid_keys_lower:
                        try:
                            reclaimed_bytes += full_file_path.stat().st_size
                            full_file_path.unlink(missing_ok=True)
                            deleted_count += 1
                        except Exception as e:
                            logger.warning(f"Failed to delete orphan thumbnail {full_file_path}: {e}")
            for d in dirs:
                dir_path = Path(root) / d
                try:
                    if not any(dir_path.iterdir()):
                        dir_path.rmdir()
                except Exception:
                    pass
        return {"deleted_count": deleted_count, "reclaimed_bytes": reclaimed_bytes}


thumbnail_service = ThumbnailService()
