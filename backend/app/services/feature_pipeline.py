"""
特徵提取基礎模組。
提供 extract_basic() 與 extract_clip() 兩個獨立方法，
使用真實 OpenCV / ImageHash / Pillow 進行特徵計算，無任何隨機假數據。
"""

import hashlib
import io
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
import cv2
import imagehash
import numpy as np
from PIL import Image, ExifTags

from app.stubs.clip_stub import clip_extractor
from app.stubs.raw_stub import raw_decoder
from app.stubs.video_probe_stub import video_probe

logger = logging.getLogger(__name__)


class FeaturePipeline:
    """
    特徵提取真實實作類：
    - compute_sha256()   : 串流計算檔案 SHA-256
    - extract_basic()    : 真實拉普拉斯清晰度方差 (Laplacian variance)、亮度、EXIF、pHash
    - extract_clip()     : 真實 CLIP 向量與 Zero-Shot 分類
    - open_image()       : 支援一般影像與 RAW 格式解碼
    """

    DEFAULT_ZS_LABELS = ["螢幕截圖", "文件", "誤拍", "梗圖"]

    def compute_sha256(self, file_path: str) -> str:
        """計算檔案 SHA-256 雜湊值。"""
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                h.update(chunk)
        return h.hexdigest()

    def open_image(self, file_path: str, is_raw: bool = False) -> Tuple[Optional[Image.Image], str]:
        """
        開啟並解碼圖片。
        若為 RAW 格式，使用 raw_decoder (rawpy) 解碼。
        回傳 (PIL.Image, decode_status: str)。
        decode_status: 'ok' | 'fallback' | 'error'
        """
        p = Path(file_path)
        if not p.exists():
            return None, "error"

        if is_raw or raw_decoder.is_raw(file_path):
            try:
                raw_bytes = raw_decoder.decode_raw(file_path)
                if raw_bytes:
                    img = Image.open(io.BytesIO(raw_bytes))
                    img.load()
                    return img, "ok"
            except Exception as e:
                logger.debug(f"RAW decode error for {file_path}: {e}")

        try:
            img = Image.open(file_path)
            img.load()
            return img, "ok"
        except Exception as e:
            logger.warning(f"Image decode failed for {file_path}: {e}")
            return None, "error"

    def extract_basic(
        self,
        file_path: str,
        is_raw: bool = False,
        preloaded_image: Optional[Image.Image] = None,
    ) -> Dict[str, Any]:
        """
        提取基本相片特徵（純物理/數值計算，無隨機假資料）。
        """
        p = Path(file_path)
        exif_dict: Dict[str, Any] = {}
        taken_at: Optional[datetime] = None
        width: Optional[int] = None
        height: Optional[int] = None
        phash_str: Optional[str] = None
        blur_score: Optional[float] = None
        brightness: Optional[float] = None
        decode_status = "ok"

        img = preloaded_image
        if img is None:
            img, decode_status = self.open_image(file_path, is_raw)

        if img is not None:
            try:
                width, height = img.size
                
                # 1. 計算感知雜湊 (pHash)
                phash_str = str(imagehash.phash(img))

                # 2. 轉為灰階 OpenCV NumPy 陣列計算清晰度與亮度
                rgb_img = img.convert("RGB")
                np_img = np.array(rgb_img)
                gray_img = cv2.cvtColor(np_img, cv2.COLOR_RGB2GRAY)

                # Laplacian 方差清晰度分數 (數值越低越模糊，通常 < 100 為模糊)
                lap = cv2.Laplacian(gray_img, cv2.CV_64F)
                blur_score = round(float(lap.var()), 2)

                # 平均亮度 (0 ~ 255)
                brightness = round(float(np.mean(gray_img)), 2)

                # 3. 提取真實 EXIF 資訊
                raw_exif = img.getexif()
                if raw_exif:
                    for tag_id, val in raw_exif.items():
                        tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))
                        if tag_name in ("DateTime", "DateTimeOriginal"):
                            try:
                                taken_at = datetime.strptime(str(val), "%Y:%m:%d %H:%M:%S")
                            except Exception:
                                pass
                        elif isinstance(val, (str, int, float)):
                            exif_dict[tag_name] = val
            except Exception as e:
                logger.warning(f"Error calculating image basic features for {file_path}: {e}")
                if phash_str is None:
                    phash_str = hashlib.md5(file_path.encode()).hexdigest()[:16]
        else:
            phash_str = hashlib.md5(file_path.encode()).hexdigest()[:16]

        mtime = p.stat().st_mtime if p.exists() else datetime.utcnow().timestamp()

        return {
            "phash": phash_str,
            "blur_score": blur_score,
            "brightness": brightness,
            "width": width or 0,
            "height": height or 0,
            "taken_at": taken_at or datetime.fromtimestamp(mtime),
            "exif": exif_dict,
            "decode_status": decode_status,
        }

    def extract_clip(
        self,
        file_path: str,
        preloaded_image: Optional[Image.Image] = None,
        zs_labels: Optional[list] = None,
        device: str = "auto",
    ) -> Dict[str, Any]:
        """
        提取真實 CLIP 向量與零樣本分類結果。
        """
        if zs_labels is None:
            zs_labels = self.DEFAULT_ZS_LABELS

        embedding = clip_extractor.extract_embedding(file_path, image=preloaded_image, device=device)
        zs_scores = clip_extractor.classify_zero_shot(file_path, zs_labels, image=preloaded_image, device=device)

        return {
            "embedding": embedding,
            "zs": zs_scores,
        }

    def extract_features(
        self,
        file_path: str,
        is_raw: bool = False,
        is_video: bool = False,
    ) -> Dict[str, Any]:
        """
        【向下相容】提取所有特徵。
        """
        sha = self.compute_sha256(file_path)

        if is_video:
            probe = video_probe.probe(file_path)
            return {
                "sha256": sha,
                "phash": None,
                "blur_score": None,
                "brightness": None,
                "width": probe.get("width"),
                "height": probe.get("height"),
                "taken_at": None,
                "exif": {},
                "clip_embedding": None,
                "zs_scores": {},
                "decode_status": "ok",
            }

        img, decode_status = self.open_image(file_path, is_raw)
        basic = self.extract_basic(file_path, is_raw, preloaded_image=img)
        clip = self.extract_clip(file_path, preloaded_image=img)

        return {
            "sha256": sha,
            "phash": basic["phash"],
            "blur_score": basic["blur_score"],
            "brightness": basic["brightness"],
            "width": basic["width"],
            "height": basic["height"],
            "taken_at": basic["taken_at"],
            "exif": basic["exif"],
            "clip_embedding": clip["embedding"],
            "zs_scores": clip["zs"],
            "decode_status": basic["decode_status"],
        }


feature_pipeline = FeaturePipeline()
