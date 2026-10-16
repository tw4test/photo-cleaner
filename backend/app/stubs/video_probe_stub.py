import cv2
import logging
from pathlib import Path
from typing import Dict, Any, Optional
from PIL import Image

logger = logging.getLogger(__name__)


class VideoProbeError(RuntimeError):
    """影片無法開啟或讀不到任何容器資訊。"""


class VideoProbe:
    """
    真實影片解析與 Probe 服務。
    使用 OpenCV (cv2.VideoCapture) 提取影片容器資訊（時長、解析度、編碼）與擷取第 1 幀縮圖。

    v0.5：打不開或讀不到任何資訊時拋出 VideoProbeError，由 video_stage 記錄為失敗，
    不再寫入一筆欄位全空的 video_info 當成成功。
    """
    VIDEO_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.wmv', '.flv', '.webm', '.m4v'}

    def is_video(self, file_path: str) -> bool:
        return Path(file_path).suffix.lower() in self.VIDEO_EXTENSIONS

    def probe(self, file_path: str) -> Dict[str, Any]:
        """讀取影片基礎資訊 (時長、解析度、編碼)。失敗時拋出 VideoProbeError。

        v0.7.0（VD-21）：OpenCV 打不開、或讀不到時長 / 解析度時，改用 ffprobe 補讀（HEVC、部分 MKV / WMV 常見）。
        - OpenCV 讀到的欄位優先（與舊資料一致），只補它缺少的欄位；OpenCV 完全失敗時整組用 ffprobe 的值。
        - ffprobe 是選用的：沒有安裝就維持舊行為（OpenCV 失敗 → VideoProbeError）。
        """
        path = Path(file_path)
        if not path.exists():
            raise VideoProbeError(f"檔案不存在：{file_path}")
        size = path.stat().st_size
        info, cv_error = self._probe_opencv(path)
        if info is None or info["duration"] is None or info["width"] is None or info["height"] is None:
            fb = self._probe_ffprobe(str(path))
            if fb is not None:
                if info is None:
                    info = fb
                else:
                    for k in ("duration", "width", "height", "codec"):
                        if info[k] is None:
                            info[k] = fb[k]
        if info is None:
            raise VideoProbeError(cv_error or "讀不到任何影片資訊")
        info["size"] = size
        return info

    def _probe_opencv(self, path: Path):
        """回傳 (資訊 dict 或 None, 失敗原因)。"""
        cap = cv2.VideoCapture(str(path))
        try:
            if not cap.isOpened():
                return None, "OpenCV 無法開啟影片（格式不支援或檔案損壞）"
            frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
            fps = cap.get(cv2.CAP_PROP_FPS)
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or None
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or None
            fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
            codec: Optional[str] = None
            if fourcc > 0:
                codec = "".join([chr((fourcc >> 8 * i) & 0xFF) for i in range(4)]).strip().lower() or None
            duration: Optional[float] = None
            if fps and fps > 0 and frame_count and frame_count > 0:
                duration = round(frame_count / fps, 2)
        finally:
            cap.release()
        if duration is None and width is None and height is None and codec is None:
            return None, "讀不到任何影片資訊（時長、解析度、編碼皆為空）"
        return {"duration": duration, "width": width, "height": height, "codec": codec}, None

    def _probe_ffprobe(self, file_path: str) -> Optional[Dict[str, Any]]:
        from app.services import ffmpeg_service
        p = ffmpeg_service.probe(file_path)
        if not p or not p.get("video"):
            return None
        v = p["video"]
        dur = p.get("duration")
        return {
            "duration": round(dur, 2) if dur else None,
            "width": v.get("width") or None,
            "height": v.get("height") or None,
            "codec": v.get("codec"),
        }

    def extract_first_frame(self, file_path: str) -> Optional[Image.Image]:
        """擷取影片第 1 幀。先用 OpenCV；打不開或讀不到幀（常見於 HEVC / 部分 MKV、WMV）時改用 ffmpeg（v0.7.0，VD-20）。
        ffmpeg 是選用的：沒有安裝就回 None，維持舊行為。"""
        img = self._first_frame_opencv(file_path)
        if img is not None:
            return img
        from app.services import ffmpeg_service
        img = ffmpeg_service.extract_frame(str(file_path))
        if img is not None:
            logger.debug("OpenCV 讀不到第一幀，已改用 ffmpeg：%s", file_path)
        return img

    def _first_frame_opencv(self, file_path: str) -> Optional[Image.Image]:
        cap = None
        try:
            cap = cv2.VideoCapture(str(file_path))
            if not cap.isOpened():
                return None
            success, frame = cap.read()
            if success and frame is not None:
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                return Image.fromarray(rgb_frame)
        except Exception as e:
            logger.warning(f"Failed to extract first frame from {file_path}: {e}")
        finally:
            if cap is not None:
                cap.release()
        return None


video_probe = VideoProbe()
