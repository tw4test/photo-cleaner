"""
video_transcode.py（v0.7.0）— 把瀏覽器播不了的影片（AVI / WMV / FLV / 部分 MKV / HEVC…）轉成 H.264 MP4。

規則
- 原檔絕不改動；結果存在 DATA_DIR/video_cache/<前兩碼>/<key>.mp4，key = sha256(路徑、大小、mtime、解析度上限)。
  原檔被修改後 key 會變，舊快取自然不再命中，之後由容量上限淘汰。
- 一次只跑 1 個轉檔：換一支影片、或關閉播放器都會取消前一個；與全域任務鎖（掃描 / 分析）無關，互不阻擋。
- 取消**一律用 proc.kill()**，不用 terminate()：ffmpeg 收到 SIGTERM 會先把整支轉完（寫完檔尾）才結束，等於取消不了。
  取消後等待行程與執行緒結束、刪掉 .part，所以 cancel() 回傳時磁碟上已經乾淨。
- 先寫 <key>.mp4.part，成功（return code 0）才 os.replace 成 .mp4；失敗或取消不留半成品。
- 輸出：H.264（libx264 veryfast, crf 23, yuv420p）+ AAC 立體聲；短邊最高 VIDEO_TRANSCODE_MAX_HEIGHT（預設 1080）；
  +faststart，轉好的檔案可立刻用 Range 串流與拖曳。
- 快取上限 VIDEO_CACHE_MAX_BYTES（預設 5 GB）：超過時依「最近使用時間」（mtime，命中時會更新）由舊到新刪除，
  不刪剛完成的那一個；刪不掉的（被占用）略過。
- 進度：ffmpeg -progress pipe:1 的 out_time / 影片時長。時長未知時 progress = None（前端顯示不確定進度）。
"""
import atexit
import hashlib
import logging
import os
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from app.core.config import settings
from app.services import ffmpeg_service

logger = logging.getLogger(__name__)

_KEY_VERSION = "v1"
CANCEL_WAIT_S = 8.0


class FfmpegMissing(Exception):
    """找不到 ffmpeg。"""


class _Job:
    def __init__(self, asset_id: int, src: str, key: str, out: Path, duration: Optional[float]):
        self.asset_id = asset_id
        self.src = src
        self.key = key
        self.out = out
        self.part = out.with_name(out.name + ".part")
        self.duration = duration
        self.status = "running"            # running | done | error | cancelled
        self.progress: Optional[float] = 0.0
        self.speed: Optional[str] = None
        self.error: Optional[str] = None
        self.from_cache = False
        self.cancel_requested = False
        self.proc: Optional[subprocess.Popen] = None
        self.thread: Optional[threading.Thread] = None
        self.started_at = time.time()
        self.finished_at: Optional[float] = None

    def state(self) -> Dict[str, Any]:
        end = self.finished_at or time.time()
        return {
            "asset_id": self.asset_id, "status": self.status,
            "progress": None if self.progress is None else round(self.progress, 1),
            "duration": self.duration, "speed": self.speed, "error": self.error,
            "from_cache": self.from_cache, "elapsed_seconds": round(end - self.started_at, 1),
        }


_lock = threading.RLock()
_start_lock = threading.Lock()       # 讓 start() 一次只有一個在「取消前一個 → 啟動新的」，避免同時啟動兩個
_jobs: Dict[int, _Job] = {}          # 每個資產最近一次的工作（供查詢狀態）
_active: Optional[_Job] = None       # 目前正在跑的那一個（最多 1 個）


# ── 快取路徑與容量 ─────────────────────────────────────────────
def cache_key(src: str, size: int, mtime_ns: int) -> str:
    raw = f"{_KEY_VERSION}|{src}|{size}|{mtime_ns}|{settings.VIDEO_TRANSCODE_MAX_HEIGHT}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def cache_path_for(src: str) -> Optional[Path]:
    """目前這個原檔對應的快取路徑（不論檔案是否存在）；原檔不存在回 None。"""
    try:
        st = os.stat(src)
    except OSError:
        return None
    key = cache_key(src, st.st_size, st.st_mtime_ns)
    return settings.VIDEO_CACHE_DIR / key[:2] / f"{key}.mp4"


def cached_file(src: str) -> Optional[Path]:
    p = cache_path_for(src)
    try:
        if p is not None and p.is_file() and p.stat().st_size > 0:
            return p
    except OSError:
        pass
    return None


def touch(p: Path) -> None:
    try:
        os.utime(p, None)
    except OSError:
        pass


def cache_usage() -> Dict[str, int]:
    total = n = 0
    root = settings.VIDEO_CACHE_DIR
    if root.is_dir():
        for f in root.rglob("*.mp4"):
            try:
                total += f.stat().st_size
                n += 1
            except OSError:
                pass
    return {"bytes": total, "files": n, "limit_bytes": int(settings.VIDEO_CACHE_MAX_BYTES)}


def enforce_cache_limit(protect: Optional[Path] = None) -> List[str]:
    """超過上限時由最久沒用的開始刪。回傳被刪掉的檔名。protect 不刪（即使它單獨就超過上限）。"""
    root = settings.VIDEO_CACHE_DIR
    if not root.is_dir():
        return []
    limit = int(settings.VIDEO_CACHE_MAX_BYTES)
    entries = []
    total = 0
    for f in root.rglob("*.mp4"):
        try:
            st = f.stat()
        except OSError:
            continue
        entries.append((st.st_mtime, f, st.st_size))
        total += st.st_size
    removed: List[str] = []
    if total <= limit:
        return removed
    entries.sort(key=lambda e: e[0])
    protect_r = protect.resolve() if protect else None
    for _mt, f, size in entries:
        if total <= limit:
            break
        if protect_r is not None and f.resolve() == protect_r:
            continue
        try:
            f.unlink()
        except OSError:
            continue                     # 被占用（Windows）或已不存在：略過
        total -= size
        removed.append(f.name)
        try:
            f.parent.rmdir()             # 分片資料夾空了就一併移除
        except OSError:
            pass
    if removed:
        logger.info("影片轉檔快取超過上限，已淘汰 %d 個舊檔", len(removed))
    return removed


def cleanup_partials() -> int:
    """啟動時刪掉上次異常結束留下的 .part。"""
    root = settings.VIDEO_CACHE_DIR
    n = 0
    if root.is_dir():
        for f in root.rglob("*.part"):
            try:
                f.unlink()
                n += 1
            except OSError:
                pass
    return n


# ── ffmpeg 指令 ─────────────────────────────────────────────────
def build_command(ffmpeg: str, src: str, dst: Path) -> List[str]:
    m = int(settings.VIDEO_TRANSCODE_MAX_HEIGHT)
    # 短邊最高 m（橫式限制高、直式限制寬）；另一邊用 -2 保持比例並取偶數；不放大
    vf = (f"scale='if(gt(iw,ih),-2,min({m},trunc(iw/2)*2))':'if(gt(iw,ih),min({m},trunc(ih/2)*2),-2)'")
    return [
        ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
        "-i", src,
        "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ac", "2",
        "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats",
        "-f", "mp4", str(dst),
    ]


def _spawn(cmd: List[str]) -> subprocess.Popen:
    """啟動 ffmpeg（測試會替換它來觀察 kill / terminate）。"""
    return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace", **ffmpeg_service.popen_flags())


def _drain_stderr(proc: subprocess.Popen, sink: Deque[str]) -> None:
    try:
        for line in proc.stderr:  # type: ignore[union-attr]
            line = line.strip()
            if line:
                sink.append(line)
    except Exception:  # noqa: BLE001
        pass


def _remove(p: Path) -> None:
    try:
        p.unlink()
    except OSError:
        pass


def _run(job: _Job, cmd: List[str]) -> None:
    global _active
    stderr_tail: Deque[str] = deque(maxlen=30)
    rc: Optional[int] = None
    spawn_error: Optional[str] = None
    try:
        job.part.parent.mkdir(parents=True, exist_ok=True)
        _remove(job.part)
        try:
            proc = _spawn(cmd)
        except OSError as exc:
            spawn_error = f"無法啟動 ffmpeg：{exc}"
            proc = None
        if proc is not None:
            with _lock:
                job.proc = proc
                if job.cancel_requested:          # 取消發生在行程建立之前
                    proc.kill()
            t = threading.Thread(target=_drain_stderr, args=(proc, stderr_tail), daemon=True)
            t.start()
            try:
                for raw in proc.stdout:  # type: ignore[union-attr]
                    k, _, v = raw.strip().partition("=")
                    if k in ("out_time_us", "out_time_ms") and v.lstrip("-").isdigit():
                        # 兩者單位都是微秒（out_time_ms 是 ffmpeg 歷史上的命名錯誤）
                        if job.duration:
                            job.progress = max(0.0, min(99.0, int(v) / 1_000_000 / job.duration * 100))
                    elif k == "speed" and v and v != "N/A":
                        job.speed = v
            finally:
                rc = proc.wait()
                t.join(timeout=2)
    except Exception as exc:  # noqa: BLE001
        logger.exception("影片轉檔執行緒發生例外")
        spawn_error = f"{type(exc).__name__}: {exc}"
    finally:
        with _lock:
            if job.cancel_requested:
                job.status = "cancelled"
                _remove(job.part)
            elif spawn_error is None and rc == 0 and job.part.is_file() and job.part.stat().st_size > 0:
                try:
                    os.replace(job.part, job.out)
                    job.status, job.progress = "done", 100.0
                except OSError as exc:
                    job.status, job.error = "error", f"無法寫入快取：{exc}"
                    _remove(job.part)
            else:
                job.status = "error"
                job.error = spawn_error or ("; ".join(list(stderr_tail)[-5:]) or f"ffmpeg 結束代碼 {rc}")
                _remove(job.part)
            job.finished_at = time.time()
            job.proc = None
            if _active is job:
                _active = None
        if job.status == "done":
            enforce_cache_limit(protect=job.out)
        logger.info("影片轉檔 asset=%s → %s", job.asset_id, job.status)


# ── 對外介面 ────────────────────────────────────────────────────
def start(asset_id: int, src: str, duration: Optional[float] = None) -> Dict[str, Any]:
    """開始轉檔（冪等）。已有快取 → 立即 done；同一支正在轉 → 回傳目前狀態；別支正在轉 → 先取消它。"""
    with _start_lock:
        return _start_locked(asset_id, src, duration)


def _start_locked(asset_id: int, src: str, duration: Optional[float]) -> Dict[str, Any]:
    global _active
    ffmpeg = ffmpeg_service.find_ffmpeg()
    if not ffmpeg:
        raise FfmpegMissing(ffmpeg_service.INSTALL_HINT)
    st = os.stat(src)                    # 不存在 → FileNotFoundError，由 API 轉成 404
    key = cache_key(src, st.st_size, st.st_mtime_ns)
    out = settings.VIDEO_CACHE_DIR / key[:2] / f"{key}.mp4"

    hit = cached_file(src)
    if hit is not None:
        touch(hit)
        with _lock:
            job = _Job(asset_id, src, key, out, duration)
            job.status, job.progress, job.from_cache, job.finished_at = "done", 100.0, True, time.time()
            _jobs[asset_id] = job
        return job.state()

    to_cancel: Optional[_Job] = None
    with _lock:
        cur = _active
        if cur is not None and cur.status == "running":
            if cur.asset_id == asset_id and cur.key == key:
                return cur.state()
            to_cancel = cur
    if to_cancel is not None:
        _cancel_job(to_cancel)

    if duration is None:
        info = ffmpeg_service.probe(src)
        duration = info["duration"] if info else None
    with _lock:
        job = _Job(asset_id, src, key, out, duration)
        job.progress = 0.0 if duration else None
        _jobs[asset_id] = job
        _active = job
        job.thread = threading.Thread(target=_run, args=(job, build_command(ffmpeg, src, job.part)),
                                      name=f"video-transcode-{asset_id}", daemon=True)
        job.thread.start()
        return job.state()


def _cancel_job(job: _Job) -> None:
    with _lock:
        if job.status != "running":
            return
        job.cancel_requested = True
        proc = job.proc
        if proc is not None:
            try:
                proc.kill()              # 不用 terminate()：ffmpeg 收到 SIGTERM 會先轉完整支才結束
            except OSError:
                pass
        th = job.thread
    if th is not None and th is not threading.current_thread():
        th.join(timeout=CANCEL_WAIT_S)


def cancel(asset_id: int) -> Dict[str, Any]:
    """取消這個資產的轉檔（冪等）。回傳時行程已結束、.part 已刪。"""
    with _lock:
        job = _jobs.get(asset_id)
    if job is not None:
        _cancel_job(job)
        return job.state()
    return {"asset_id": asset_id, "status": "idle", "progress": 0.0, "duration": None, "speed": None,
            "error": None, "from_cache": False, "elapsed_seconds": 0.0}


def get_state(asset_id: int, src: Optional[str] = None) -> Dict[str, Any]:
    with _lock:
        job = _jobs.get(asset_id)
        if job is not None and not (job.status == "done" and src and cached_file(src) is None):
            return job.state()
    if src and cached_file(src) is not None:
        return {"asset_id": asset_id, "status": "done", "progress": 100.0, "duration": None, "speed": None,
                "error": None, "from_cache": True, "elapsed_seconds": 0.0}
    return {"asset_id": asset_id, "status": "idle", "progress": 0.0, "duration": None, "speed": None,
            "error": None, "from_cache": False, "elapsed_seconds": 0.0}


def shutdown() -> None:
    """後端結束時殺掉還在跑的 ffmpeg，避免留下孤兒行程。"""
    with _lock:
        job = _active
    if job is not None:
        _cancel_job(job)


atexit.register(shutdown)
