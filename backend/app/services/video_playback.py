"""
video_playback.py（v0.7.0）— 判斷一支影片要怎麼播。三種結果（`classify`）：

- `direct`（保證可播）：容器是 mp4 / m4v / mov / webm / mkv，影片編碼是 h264（8-bit）/ vp8 / vp9 / av1，
  音訊是 aac / mp3 / opus / vorbis / flac（或沒有音訊）。直接播原檔。
- `try_first`（瀏覽器與硬體決定）：**影像**編碼是 HEVC（h265）或 10-bit h264，其他都沒問題。
  能不能播取決於使用者的瀏覽器與顯示卡（例如 Edge / Chrome + 有 HEVC 硬體解碼的 Windows 就能播），
  後端無從得知，所以**先試著播原檔**，前端偵測到失敗才提示轉檔（VD-11）。
  失敗不一定有 error 事件：沒有 HEVC 解碼器的瀏覽器常常只播出聲音、沒有畫面，前端要自己偵測（videoWidth = 0 / 沒有解碼的畫格）。
- `transcode`（一定要轉檔）：avi / wmv / flv 等瀏覽器不支援的容器，或影像編碼不是上面兩類（mpeg4、wmv…），
  或音訊編碼瀏覽器不支援（AC-3 / DTS…；影像能播但沒有聲音，前端偵測不到，所以不冒險）。

沒有 ffprobe 時只能靠副檔名與資料庫裡 OpenCV 讀到的 codec（fourcc）：HEVC 系列算 `try_first`，其餘當作 `direct`。
只做判斷，不做轉檔；轉檔在 video_transcode.py。
"""
from typing import Any, Dict, Optional, Tuple

DIRECT_CONTAINER_EXTS = {".mp4", ".m4v", ".mov", ".webm", ".mkv"}
VIDEO_MIME = {
    ".mp4": "video/mp4", ".m4v": "video/mp4",
    ".mov": "video/mp4",             # Chrome 對 video/quicktime 的 canPlayType 是空字串；h264 的 mov 用 mp4 解多工
    ".webm": "video/webm", ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo", ".wmv": "video/x-ms-wmv", ".flv": "video/x-flv",
}
OK_VIDEO_CODECS = {"h264", "vp8", "vp9", "av1"}
OK_AUDIO_CODECS = {"aac", "mp3", "opus", "vorbis", "flac"}
HEVC_FOURCC = {"hevc", "hev1", "hvc1", "hev2", "h265", "x265"}
DIRECT, TRY_FIRST, TRANSCODE = "direct", "try_first", "transcode"


def mime_for(ext: str) -> str:
    return VIDEO_MIME.get(ext.lower(), "application/octet-stream")


def decide(ext: str, probe: Optional[Dict[str, Any]], db_codec: Optional[str]) -> Tuple[bool, str]:
    """回傳 (直接播放?, 原因)。"""
    ext = ext.lower()
    if ext not in DIRECT_CONTAINER_EXTS:
        return False, f"{ext or '此'} 容器格式瀏覽器無法播放"
    if probe:
        v = probe.get("video") or {}
        vc = v.get("codec")
        if vc not in OK_VIDEO_CODECS:
            return False, f"影片編碼 {vc or '未知'} 瀏覽器不一定能播放"
        if vc == "h264" and v.get("pix_fmt") and "10" in str(v["pix_fmt"]):
            return False, "10-bit H.264 瀏覽器無法播放"
        ac = (probe.get("audio") or {}).get("codec")
        if ac is not None and ac not in OK_AUDIO_CODECS:
            return False, f"音訊編碼 {ac} 瀏覽器無法播放"
        return True, "瀏覽器可直接播放"
    c = (db_codec or "").lower()
    if c in HEVC_FOURCC:
        return False, "HEVC（H.265）瀏覽器不一定能播放"
    return True, "依副檔名判斷可直接播放（沒有 ffprobe 可確認編碼）"


def classify(ext: str, probe: Optional[Dict[str, Any]], db_codec: Optional[str]) -> Tuple[str, str]:
    """回傳 (`direct` | `try_first` | `transcode`, 原因)。"""
    direct, reason = decide(ext, probe, db_codec)
    if direct:
        return DIRECT, reason
    if ext.lower() not in DIRECT_CONTAINER_EXTS:
        return TRANSCODE, reason
    if probe:
        v = probe.get("video") or {}
        vc = v.get("codec")
        hevc = vc in ("hevc", "h265")
        hi10 = vc == "h264" and "10" in str(v.get("pix_fmt") or "")
        ac = (probe.get("audio") or {}).get("codec")
        audio_ok = ac is None or ac in OK_AUDIO_CODECS
        if (hevc or hi10) and audio_ok:
            return TRY_FIRST, reason + "；先嘗試直接播放，失敗再轉檔"
        return TRANSCODE, reason
    if (db_codec or "").lower() in HEVC_FOURCC:
        return TRY_FIRST, reason + "；先嘗試直接播放，失敗再轉檔"
    return TRANSCODE, reason
