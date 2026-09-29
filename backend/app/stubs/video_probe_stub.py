from pathlib import Path
from typing import Dict, Any, Optional

# TODO(GPU): 替換為 GPU 加速解碼並擷取第一幀作為高品質縮圖
class MockVideoProbe:
    """
    Mock 影片解析與 Probe。
    只讀取檔案基礎資訊，不進行影片內容解碼。
    """

    VIDEO_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.wmv', '.flv', '.webm', '.m4v'}

    def is_video(self, file_path: str) -> bool:
        return Path(file_path).suffix.lower() in self.VIDEO_EXTENSIONS

    def probe(self, file_path: str) -> Dict[str, Any]:
        """
        讀取影片基礎容器資訊 (時長、解析度、編碼)。
        若無 ffprobe 則回傳基礎預設值。
        """
        path = Path(file_path)
        size = path.stat().st_size if path.exists() else 0
        return {
            "duration": None,
            "width": None,
            "height": None,
            "codec": None,
            "size": size,
        }

video_probe = MockVideoProbe()
