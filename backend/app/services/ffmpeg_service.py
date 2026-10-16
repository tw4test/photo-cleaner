"""
ffmpeg_service.py（v0.7.0）— 找 ffmpeg / ffprobe，並用 ffprobe 讀影片的容器與編碼資訊。

ffmpeg 是**選用**的：找不到時 `find_ffmpeg()` 回傳 None，呼叫端（轉檔、抽幀）自行降級，
其他功能完全不受影響。尋找順序：環境變數 / .env 的 FFMPEG_PATH → PATH。
"""
import json
import logging
import os
import shutil
import subprocess
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_S = 20
INSTALL_HINT = (
    "找不到 ffmpeg。影片轉檔與部分影片縮圖需要它，其他功能不受影響。"
    "Windows：以系統管理員開啟 PowerShell 執行 `winget install Gyan.FFmpeg`，安裝後重新開啟終端機並重新啟動後端；"
    "或自行下載 ffmpeg 並加入 PATH；也可在 backend/.env 設定 FFMPEG_PATH=完整路徑\\ffmpeg.exe。"
)

# Windows：不要為每個 ffmpeg / ffprobe 子行程彈出主控台視窗
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def popen_flags() -> Dict[str, Any]:
    return {"creationflags": CREATE_NO_WINDOW} if CREATE_NO_WINDOW else {}


def _resolve(configured: str, name: str) -> Optional[str]:
    if configured:
        p = Path(configured)
        if p.is_file():
            return str(p)
        logger.warning("設定的 %s 路徑不存在：%s", name, configured)
        return None
    return shutil.which(name)


def find_ffmpeg() -> Optional[str]:
    return _resolve(settings.FFMPEG_PATH, "ffmpeg")


def find_ffprobe() -> Optional[str]:
    """先看 FFPROBE_PATH；再找與 ffmpeg 同資料夾的 ffprobe；最後找 PATH。"""
    if settings.FFPROBE_PATH:
        return _resolve(settings.FFPROBE_PATH, "ffprobe")
    ff = find_ffmpeg()
    if ff:
        exe = "ffprobe.exe" if os.name == "nt" else "ffprobe"
        sibling = Path(ff).with_name(exe)
        if sibling.is_file():
            return str(sibling)
    return shutil.which("ffprobe")


def version() -> Optional[str]:
    ff = find_ffmpeg()
    if not ff:
        return None
    try:
        out = subprocess.run([ff, "-hide_banner", "-version"], capture_output=True, text=True,
                             timeout=10, **popen_flags()).stdout
        first = out.splitlines()[0] if out else ""
        return first.strip() or None
    except Exception:  # noqa: BLE001
        return None


def status() -> Dict[str, Any]:
    ff, fp = find_ffmpeg(), find_ffprobe()
    return {
        "available": ff is not None,
        "ffmpeg_path": ff,
        "ffprobe_available": fp is not None,
        "version": version() if ff else None,
        "install_hint": None if ff else INSTALL_HINT,
    }


# ── ffprobe ────────────────────────────────────────────────────
_probe_cache: "OrderedDict[Tuple[str, int, int], Dict[str, Any]]" = OrderedDict()
_probe_lock = threading.Lock()
_PROBE_CACHE_ENTRIES = 256


def clear_probe_cache() -> None:
    with _probe_lock:
        _probe_cache.clear()


def _to_float(v: Any) -> Optional[float]:
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def parse_probe(data: Dict[str, Any]) -> Dict[str, Any]:
    """把 ffprobe -print_format json 的輸出整理成我們要的欄位。"""
    streams: List[Dict[str, Any]] = data.get("streams") or []
    fmt = data.get("format") or {}
    v = next((s for s in streams if s.get("codec_type") == "video"
              and not (s.get("disposition") or {}).get("attached_pic")), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = _to_float(fmt.get("duration")) or (_to_float(v.get("duration")) if v else None)
    return {
        "container": fmt.get("format_name"),
        "duration": duration,
        "video": None if v is None else {
            "codec": (v.get("codec_name") or "").lower() or None,
            "pix_fmt": v.get("pix_fmt"),
            "width": v.get("width"),
            "height": v.get("height"),
        },
        "audio": None if a is None else {"codec": (a.get("codec_name") or "").lower() or None},
    }


def probe(path: str) -> Optional[Dict[str, Any]]:
    """用 ffprobe 讀影片資訊；沒有 ffprobe、檔案讀不到或逾時一律回 None（不拋例外）。"""
    fp = find_ffprobe()
    if not fp:
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (path, st.st_mtime_ns, st.st_size)
    with _probe_lock:
        hit = _probe_cache.get(key)
        if hit is not None:
            _probe_cache.move_to_end(key)
            return hit
    try:
        r = subprocess.run([fp, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                           capture_output=True, timeout=PROBE_TIMEOUT_S, **popen_flags())
        if r.returncode != 0:
            return None
        info = parse_probe(json.loads(r.stdout.decode("utf-8", errors="replace") or "{}"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("ffprobe 失敗 %s — %s", path, exc)
        return None
    if info["video"] is None:
        return None
    with _probe_lock:
        _probe_cache[key] = info
        _probe_cache.move_to_end(key)
        while len(_probe_cache) > _PROBE_CACHE_ENTRIES:
            _probe_cache.popitem(last=False)
    return info


# ── 抽幀（OpenCV 讀不到第一幀時的備援）──────────────────────────
FRAME_TIMEOUT_S = 30


def extract_frame(path: str, seek: float = 0.0, max_side: int = 2560):
    """用 ffmpeg 抽一幀，回傳 PIL.Image（RGB）；沒有 ffmpeg、讀不到或逾時回 None（不拋例外）。

    max_side：最長邊上限（只縮小不放大），避免 4K 影片的整幀經管線傳輸。
    ffmpeg 預設會依影片的旋轉資訊轉正（手機直拍影片）。
    """
    ff = find_ffmpeg()
    if not ff:
        return None
    try:
        os.stat(path)
    except OSError:
        return None
    import io
    from PIL import Image
    scale = f"scale='if(gt(iw,ih),min({max_side},iw),-2)':'if(gt(iw,ih),-2,min({max_side},ih))'"
    cmd = [ff, "-hide_banner", "-nostdin", "-loglevel", "error"]
    if seek and seek > 0:
        cmd += ["-ss", f"{seek:.3f}"]
    cmd += ["-i", path, "-an", "-sn", "-frames:v", "1", "-vf", scale, "-f", "image2pipe", "-c:v", "png", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=FRAME_TIMEOUT_S, **popen_flags())
        if r.returncode != 0 or not r.stdout:
            return None
        img = Image.open(io.BytesIO(r.stdout))
        img.load()
        return img.convert("RGB")
    except Exception as exc:  # noqa: BLE001
        logger.warning("ffmpeg 抽幀失敗 %s — %s", path, exc)
        return None
