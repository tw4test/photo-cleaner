import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional
from PIL import Image, ExifTags
import imagehash
import numpy as np

from app.stubs.clip_stub import clip_extractor
from app.stubs.raw_stub import raw_decoder
from app.stubs.video_probe_stub import video_probe

class FeaturePipeline:
    """
    特徵提取流程：
    1. 計算檔案 SHA-256
    2. 計算 pHash 感知雜湊
    3. 計算模糊分數 (無 GPU 模式下回傳假數據/基礎分數)
    4. 計算亮度與曝光值
    5. 提取 EXIF 資訊
    6. 調用 Mock CLIP 提取虛擬 512 維向量與 Zero-Shot 分數
    """

    def compute_sha256(self, file_path: str) -> str:
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                h.update(chunk)
        return h.hexdigest()

    def extract_features(self, file_path: str, is_raw: bool = False, is_video: bool = False) -> Dict[str, Any]:
        p = Path(file_path)

        if is_video:
            sha = self.compute_sha256(file_path)
            return {
                "sha256": sha,
                "phash": None,
                "blur_score": None,
                "brightness": None,
                "width": None,
                "height": None,
                "taken_at": None,
                "exif": {},
                "clip_embedding": None,
                "zs_scores": {},
                "decode_status": "ok",
            }

        # Photo or RAW
        sha = self.compute_sha256(file_path)
        exif_dict: Dict[str, Any] = {}
        taken_at: Optional[datetime] = None
        width: Optional[int] = None
        height: Optional[int] = None
        phash_str: Optional[str] = None

        try:
            with Image.open(file_path) as img:
                width, height = img.size
                phash_str = str(imagehash.phash(img))
                
                # Extract basic EXIF
                raw_exif = img.getexif()
                if raw_exif:
                    for tag_id, val in raw_exif.items():
                        tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))
                        if tag_name == "DateTime" or tag_name == "DateTimeOriginal":
                            try:
                                taken_at = datetime.strptime(str(val), "%Y:%m:%d %H:%M:%S")
                            except Exception:
                                pass
                        elif isinstance(val, (str, int, float)):
                            exif_dict[tag_name] = val
        except Exception:
            # Non-standard or raw fallback
            phash_str = hashlib.md5(file_path.encode()).hexdigest()[:16]

        # 假數據生成 (遵循規則：features 表的 blur_score / clip_embedding 先回傳假數據)
        fake_blur_score = float(np.random.uniform(20.0, 300.0))
        fake_brightness = float(np.random.uniform(10.0, 240.0))
        fake_clip_embedding = clip_extractor.extract_embedding(file_path)
        fake_zs_scores = clip_extractor.classify_zero_shot(file_path, ["螢幕截圖", "文件", "誤拍", "梗圖"])

        return {
            "sha256": sha,
            "phash": phash_str,
            "blur_score": fake_blur_score,
            "brightness": fake_brightness,
            "width": width or 1920,
            "height": height or 1080,
            "taken_at": taken_at or datetime.fromtimestamp(p.stat().st_mtime),
            "exif": exif_dict,
            "clip_embedding": fake_clip_embedding,
            "zs_scores": fake_zs_scores,
            "decode_status": "ok",
        }

feature_pipeline = FeaturePipeline()
