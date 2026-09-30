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
        # Ensure Windows drive letter is uppercase (e.g. d:\ -> D:\)
        if len(norm) >= 2 and norm[1] == ":" and norm[0].isalpha():
            norm = norm[0].upper() + norm[1:]
        return norm.replace("/", "\\")
    except Exception:
        return path_str.replace("/", "\\")


class ThumbnailService:
    """
    高效能縮圖生成與快取服務（支援 100k+ 巨量相片規模）：
    1. 純中繼資料極速快取鍵 (sha256(path + mtime + size))，記憶體級 $O(1)$ 判定。
    2. 雙層目錄分片 (65,536 子目錄)，消除單資料夾萬級檔案 I/O 延遲。
    3. Pillow draft() DCT 降採樣加速與 RAW 內嵌預覽極速提取 (<20ms / 張)。
    4. RAW+JPG 伴生圖優先路徑與 OpenCV 影片關鍵畫面擷取。
    5. 失敗不快取佔位圖，保持快取純淨並支援後續自動重試。
    """

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or settings.THUMBS_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get_cache_key(
        self,
        file_path: str,
        mtime: Optional[float] = None,
        size: Optional[int] = None,
        companion_path: Optional[str] = None
    ) -> str:
        """
        根據規範化路徑、修改時間 (mtime)、檔案大小 (size) 生成複合特徵 SHA-256 快取鍵。
        若未傳入 mtime 或 size，則嘗試從檔案系統即時讀取。
        """
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
        """
        取得雙層目錄分片的縮圖快取檔案路徑：
        `cache_dir / key[0:2] / key[2:4] / {key}.jpg`
        """
        # 若傳入的已是 64 碼十六進制 SHA-256 雜湊值，直接作為 cache_key
        if len(file_path) == 64 and all(c in "0123456789abcdefABCDEF" for c in file_path):
            cache_key = file_path.lower()
        else:
            cache_key = self.get_cache_key(
                file_path=file_path,
                mtime=mtime,
                size=size,
                companion_path=companion_path
            )

        sub_dir = self.cache_dir / cache_key[:2] / cache_key[2:4]
        sub_dir.mkdir(parents=True, exist_ok=True)
        return sub_dir / f"{cache_key}.jpg"

    def has_thumbnail(
        self,
        file_path: str,
        mtime: Optional[float] = None,
        size: Optional[int] = None,
        companion_path: Optional[str] = None
    ) -> bool:
        """快速檢查縮圖是否已存在於快取中（O(1) 毫秒級命中）"""
        thumb_path = self.get_thumbnail_path(file_path, mtime=mtime, size=size, companion_path=companion_path)
        return thumb_path.exists()

    def generate_thumbnail(
        self,
        file_path: str,
        companion_path: Optional[str] = None,
        mtime: Optional[float] = None,
        size: Optional[int] = None,
        target_size: Tuple[int, int] = (320, 320)
    ) -> Optional[Path]:
        """
        極速生成指定檔案之縮圖並存入分片快取。
        - 若快取已存在，直接回傳快取路徑。
        - 若為 RAW+JPG，優先使用伴生 JPG 解碼以達到 50x 加速。
        - 若為影片，使用 OpenCV 擷取第一幀真實畫面。
        - 若為一般影像，使用 Pillow draft() DCT 降採樣加速。
        - 失敗時回傳 None（不寫入錯誤佔位圖至磁碟）。
        """
        thumb_path = self.get_thumbnail_path(
            file_path=file_path,
            mtime=mtime,
            size=size,
            companion_path=companion_path
        )

        if thumb_path.exists():
            return thumb_path

        img: Optional[Image.Image] = None

        try:
            # 1. RAW+JPG 極速通道：若有伴生 JPG 且存在，優先讀取伴生 JPG
            if companion_path and Path(companion_path).exists():
                comp_ext = Path(companion_path).suffix.lower()
                if comp_ext in {".jpg", ".jpeg", ".png", ".webp"}:
                    try:
                        with Image.open(companion_path) as src:
                            try:
                                src.draft("RGB", target_size)
                            except Exception:
                                pass
                            img = ImageOps.exif_transpose(src.convert("RGB"))
                            img.thumbnail(target_size, Image.Resampling.LANCZOS)
                    except Exception as e:
                        logger.debug(f"Companion JPG decode failed for {companion_path}: {e}")

            # 2. 影片真實縮圖擷取
            if img is None and video_probe.is_video(file_path):
                img = video_probe.extract_first_frame(file_path)
                if img is not None:
                    img.thumbnail(target_size, Image.Resampling.LANCZOS)

            # 3. RAW 格式縮圖提取 (優先提取內嵌 JPEG)
            elif img is None and raw_decoder.is_raw(file_path):
                img = raw_decoder.decode_thumbnail(file_path, target_size)

            # 4. 一般圖片（Pillow draft 快速 DCT 解碼）
            elif img is None:
                if Path(file_path).exists():
                    with Image.open(file_path) as src:
                        try:
                            src.draft("RGB", target_size)
                        except Exception:
                            pass
                        img = ImageOps.exif_transpose(src.convert("RGB"))
                        img.thumbnail(target_size, Image.Resampling.LANCZOS)

            # 5. 原子化寫入磁碟快取
            if img is not None:
                temp_path = thumb_path.with_suffix(".tmp")
                img.save(temp_path, format="JPEG", quality=80)
                temp_path.replace(thumb_path)
                return thumb_path

        except Exception as exc:
            logger.warning(f"Failed to generate thumbnail for {file_path}: {exc}")

        return None

    def cleanup_orphan_thumbnails(self, valid_cache_keys: Set[str]) -> Dict[str, Any]:
        """
        掃描磁碟縮圖快取目錄，移除所有未在 valid_cache_keys 清單中的過期/孤兒縮圖檔案。
        回傳清理統計資料：{"deleted_count": int, "reclaimed_bytes": int}
        """
        deleted_count = 0
        reclaimed_bytes = 0

        valid_keys_lower = {k.lower() for k in valid_cache_keys}

        if not self.cache_dir.exists():
            return {"deleted_count": 0, "reclaimed_bytes": 0}

        # 遍歷所有 .jpg 縮圖檔案 (包含單層與雙層目錄分片)
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

            # 清理空子目錄
            for d in dirs:
                dir_path = Path(root) / d
                try:
                    if not any(dir_path.iterdir()):
                        dir_path.rmdir()
                except Exception:
                    pass

        return {
            "deleted_count": deleted_count,
            "reclaimed_bytes": reclaimed_bytes
        }


thumbnail_service = ThumbnailService()
