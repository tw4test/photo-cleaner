"""
特徵提取基礎模組（v0.5）。

- open_image()：一次解碼、依 EXIF 轉正；JPEG 使用 draft 直接以約 1/2~1/8 解析度解碼。
- extract_basic()：在「最長邊 1024px」的縮小版上計算 pHash / 模糊 / 亮度，
  與原檔解析度無關，門檻在不同相機之間一致，也快很多。
- 讀不到圖時一律拋出 ImageDecodeError，由 basic_stage 記錄失敗；
  不再以檔名 MD5 冒充 pHash（舊版的假 pHash 會參與近似比對）。
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
from PIL import Image, ExifTags, ImageOps

from app.stubs.clip_stub import clip_extractor
from app.stubs.raw_stub import raw_decoder
from app.stubs.video_probe_stub import video_probe

logger = logging.getLogger(__name__)

try:  # HEIC/HEIF 支援
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:  # pragma: no cover
    pass

ANALYSIS_MAX_SIDE = 1024

_EXIF_IFD = getattr(getattr(ExifTags, "IFD", None), "Exif", 0x8769)
_TAG_DATETIME = 306            # DateTime（修改時間）
_TAG_DATETIME_ORIGINAL = 36867  # DateTimeOriginal（拍攝時間）
_TAG_SUBSEC_ORIGINAL = 37521   # SubsecTimeOriginal
_KEEP_TAGS = {"Make", "Model", "Software", "LensModel", "Orientation",
              "ExifImageWidth", "ExifImageHeight", "FocalLength", "ISOSpeedRatings"}


class ImageDecodeError(ValueError):
    """影像無法解碼。"""


def _parse_exif_datetime(val: Any, subsec: Any = None) -> Optional[datetime]:
    try:
        dt = datetime.strptime(str(val).strip("\x00 ").strip(), "%Y:%m:%d %H:%M:%S")
    except Exception:
        return None
    if subsec:
        digits = "".join(ch for ch in str(subsec) if ch.isdigit())[:6]
        if digits:
            dt = dt.replace(microsecond=int(digits.ljust(6, "0")))
    return dt


class FeaturePipeline:
    DEFAULT_ZS_LABELS = ["螢幕截圖", "文件", "誤拍", "梗圖"]

    def compute_sha256(self, file_path: str) -> str:
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                h.update(chunk)
        return h.hexdigest()

    def open_image(self, file_path: str, is_raw: bool = False,
                   max_side: Optional[int] = None) -> Tuple[Image.Image, Dict[str, Any]]:
        """
        解碼並依 EXIF 轉正，回傳 (RGB 影像, 中繼資料)。失敗拋出 ImageDecodeError。
        中繼資料：orig_width / orig_height（轉正後的原始尺寸）、exif（PIL Exif 物件）。
        """
        p = Path(file_path)
        if not p.exists():
            raise ImageDecodeError(f"檔案不存在：{file_path}")
        try:
            if is_raw or raw_decoder.is_raw(file_path):
                raw_bytes = raw_decoder.decode_raw(file_path)
                if not raw_bytes:
                    raise ImageDecodeError("RAW 解碼失敗（無內嵌預覽且無法後處理）")
                src = Image.open(io.BytesIO(raw_bytes))
            else:
                # 一次讀入記憶體，避免 NAS/SMB 上的隨機 seek
                src = Image.open(io.BytesIO(p.read_bytes()))
            exif = src.getexif()
            orig_w, orig_h = src.size
            if max_side and src.format == "JPEG":
                try:
                    src.draft("RGB", (max_side, max_side))
                except Exception:
                    pass
            src.load()
            img = ImageOps.exif_transpose(src)
            if img.mode != "RGB":
                img = img.convert("RGB")
            orientation = exif.get(274, 1) if exif else 1
            if orientation in (5, 6, 7, 8):
                orig_w, orig_h = orig_h, orig_w
            if max_side and max(img.size) > max_side:
                img.thumbnail((max_side, max_side), Image.Resampling.BILINEAR)
            return img, {"orig_width": orig_w, "orig_height": orig_h, "exif": exif}
        except ImageDecodeError:
            raise
        except Exception as e:
            raise ImageDecodeError(f"無法解碼影像：{e}") from e

    def _exif_fields(self, exif) -> Tuple[Optional[datetime], Dict[str, Any]]:
        taken_at: Optional[datetime] = None
        out: Dict[str, Any] = {}
        if not exif:
            return None, out
        try:
            sub = exif.get_ifd(_EXIF_IFD) or {}
        except Exception:
            sub = {}
        taken_at = _parse_exif_datetime(sub.get(_TAG_DATETIME_ORIGINAL), sub.get(_TAG_SUBSEC_ORIGINAL))
        if taken_at is None:
            taken_at = _parse_exif_datetime(exif.get(_TAG_DATETIME))
        for source in (exif, sub):
            for tag_id, val in source.items():
                name = ExifTags.TAGS.get(tag_id, str(tag_id))
                if name in _KEEP_TAGS:
                    if isinstance(val, bytes):
                        continue
                    if isinstance(val, (str, int, float)):
                        out[name] = val.strip("\x00 ") if isinstance(val, str) else val
                    else:
                        try:
                            out[name] = float(val)
                        except Exception:
                            pass
        return taken_at, out

    def compute_metrics(self, img: Image.Image) -> Dict[str, Any]:
        """在已縮小（最長邊 ≤ 1024）的 RGB 影像上計算 pHash / 模糊 / 亮度。"""
        if max(img.size) > ANALYSIS_MAX_SIDE:
            img = img.copy()
            img.thumbnail((ANALYSIS_MAX_SIDE, ANALYSIS_MAX_SIDE), Image.Resampling.BILINEAR)
        phash_str = str(imagehash.phash(img))
        gray = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2GRAY)
        blur_score = round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 2)
        brightness = round(float(gray.mean()), 2)
        return {"phash": phash_str, "blur_score": blur_score, "brightness": brightness}

    def extract_basic(
        self,
        file_path: str,
        is_raw: bool = False,
        preloaded_image: Optional[Image.Image] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """提取基本相片特徵；解碼失敗拋出 ImageDecodeError。"""
        if preloaded_image is None:
            img, meta = self.open_image(file_path, is_raw, max_side=ANALYSIS_MAX_SIDE)
        else:
            img = preloaded_image if preloaded_image.mode == "RGB" else preloaded_image.convert("RGB")
            meta = meta or {"orig_width": img.size[0], "orig_height": img.size[1], "exif": None}
        metrics = self.compute_metrics(img)
        taken_at, exif_dict = self._exif_fields(meta.get("exif"))
        if taken_at is None:
            p = Path(file_path)
            mtime = p.stat().st_mtime if p.exists() else datetime.utcnow().timestamp()
            taken_at = datetime.fromtimestamp(mtime)
            exif_dict["_taken_at_source"] = "mtime"
        return {
            **metrics,
            "width": int(meta.get("orig_width") or img.size[0]),
            "height": int(meta.get("orig_height") or img.size[1]),
            "taken_at": taken_at,
            "exif": exif_dict,
            "decode_status": "ok",
        }

    def extract_clip(
        self,
        file_path: str,
        preloaded_image: Optional[Image.Image] = None,
        zs_labels: Optional[list] = None,
        device: str = "auto",
    ) -> Dict[str, Any]:
        """單張 CLIP 向量與零樣本分類（失敗會拋出例外）。批次分析請用 clip_stage。"""
        if zs_labels is None:
            zs_labels = self.DEFAULT_ZS_LABELS
        emb, zs = clip_extractor.extract_all(file_path, zs_labels, image=preloaded_image, device=device)
        return {"embedding": emb, "zs": zs}

    def extract_features(self, file_path: str, is_raw: bool = False, is_video: bool = False) -> Dict[str, Any]:
        """【向下相容】提取所有特徵。"""
        sha = self.compute_sha256(file_path)
        if is_video:
            probe = video_probe.probe(file_path)
            return {
                "sha256": sha, "phash": None, "blur_score": None, "brightness": None,
                "width": probe.get("width"), "height": probe.get("height"),
                "taken_at": None, "exif": {}, "clip_embedding": None, "zs_scores": {},
                "decode_status": "ok",
            }
        img, meta = self.open_image(file_path, is_raw, max_side=ANALYSIS_MAX_SIDE)
        basic = self.extract_basic(file_path, is_raw, preloaded_image=img, meta=meta)
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
