import cv2
import logging
from pathlib import Path
from typing import Dict, Any, Optional
from PIL import Image

logger = logging.getLogger(__name__)

class VideoProbe:
    """
    真實影片解析與 Probe 服務。
    使用 OpenCV (cv2.VideoCapture) 提取影片容器資訊（時長、解析度、編碼）與擷取第 1 幀縮圖。
    """

    VIDEO_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.wmv', '.flv', '.webm', '.m4v'}

    def is_video(self, file_path: str) -> bool:
        return Path(file_path).suffix.lower() in self.VIDEO_EXTENSIONS

    def probe(self, file_path: str) -> Dict[str, Any]:
        """
        讀取影片基礎資訊 (時長、解析度、編碼)。
        """
        path = Path(file_path)
        size = path.stat().st_size if path.exists() else 0
        
        duration: Optional[float] = None
        width: Optional[int] = None
        height: Optional[int] = None
        codec: Optional[str] = None

        if not path.exists():
            return {
                "duration": None,
                "width": None,
                "height": None,
                "codec": None,
                "size": 0,
            }

        try:
            cap = cv2.VideoCapture(str(path))
            if cap.isOpened():
                frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
                fps = cap.get(cv2.CAP_PROP_FPS)
                width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or None
                height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or None
                fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
                if fourcc > 0:
                    codec = "".join([chr((fourcc >> 8 * i) & 0xFF) for i in range(4)]).strip().lower()

                if fps and fps > 0 and frame_count and frame_count > 0:
                    duration = round(frame_count / fps, 2)
            cap.release()
        except Exception as e:
            logger.warning(f"VideoProbe error for {file_path}: {e}")

        return {
            "duration": duration,
            "width": width,
            "height": height,
            "codec": codec,
            "size": size,
        }

    def extract_first_frame(self, file_path: str) -> Optional[Image.Image]:
        """
        擷取影片第 1 幀（或前數幀中的有效畫面）作為縮圖。
        """
        try:
            cap = cv2.VideoCapture(str(file_path))
            if not cap.isOpened():
                return None
            
            success, frame = cap.read()
            cap.release()
            
            if success and frame is not None:
                # BGR -> RGB
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                return Image.fromarray(rgb_frame)
        except Exception as e:
            logger.warning(f"Failed to extract first frame from {file_path}: {e}")
            
        return None

video_probe = VideoProbe()
