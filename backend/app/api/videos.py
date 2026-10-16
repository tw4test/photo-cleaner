"""
videos.py（v0.7.0）— 影片播放：原檔 Range 串流、播放判斷、ffmpeg 轉檔。

  GET  /api/videos                             所有影片清單（搜尋 / 排序 / 分頁；只讀，VD-01 至 VD-05）
  GET  /api/videos/summary                     影片總數與總容量（篩選欄入口顯示用）
  GET  /api/videos/ffmpeg                      ffmpeg 狀態（有沒有、版本、安裝說明）與轉檔快取用量
  GET  /api/videos/{id}/info                   影片資訊 + 瀏覽器能不能直接播 + 轉檔狀態
  GET  /api/videos/{id}/stream                 原檔串流（支援 Range / HEAD）
  POST /api/videos/{id}/transcode              開始轉檔（冪等；沒有 ffmpeg 回 503 並附安裝說明）
  GET  /api/videos/{id}/transcode              轉檔狀態（idle / running / done / error / cancelled）
  POST /api/videos/{id}/transcode/cancel       取消轉檔（冪等；關閉播放器 / 換片 / 關閉分頁時前端用 fetch keepalive 呼叫）
  GET  /api/videos/{id}/transcoded             轉好的 MP4（支援 Range / HEAD）

只讀：不改資料庫、不動使用者的檔案（SF-01）；寫入的只有 DATA_DIR/video_cache。
路徑一律來自資料庫（只接受資產 id，SF-12）。靜態路徑（/ffmpeg）必須定義在 /{asset_id}/… 之前。
"""
import os
from pathlib import Path
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import selectinload

from app.core.database import SyncSessionLocal
from app.models.schema import Asset
from app.services import ffmpeg_service, video_library, video_playback, video_transcode
from app.services.range_file import range_response

router = APIRouter(prefix="/api/videos", tags=["videos"])


def _load(asset_id: int) -> Dict[str, Any]:
    with SyncSessionLocal() as session:
        a = (session.query(Asset).options(selectinload(Asset.video_info))
             .filter(Asset.id == asset_id).first())
        if a is None or a.media_type != "video" or a.status in ("deleted", "missing"):
            raise HTTPException(status_code=404, detail="找不到指定的影片")
        vi = a.video_info
        return {
            "id": a.id, "path": a.primary_path, "size_total": a.size_total,
            "db": None if vi is None else {"duration": vi.duration, "width": vi.width,
                                           "height": vi.height, "codec": vi.codec},
        }


def _stat_or_404(path: str) -> os.stat_result:
    try:
        return os.stat(path)
    except OSError:
        raise HTTPException(status_code=404, detail="檔案不存在（可能已移動、刪除或磁碟離線）")


def _filename(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def _with_url(state: Dict[str, Any], asset_id: int, src: str) -> Dict[str, Any]:
    if state["status"] == "done":
        f = video_transcode.cached_file(src)
        if f is not None:
            state = {**state, "url": f"/api/videos/{asset_id}/transcoded?v={int(f.stat().st_mtime)}"}
    return state


@router.get("")
def list_all_videos(
    q: Optional[str] = Query(None, description="關鍵字（空白分隔，每個都要出現在完整路徑裡）"),
    sort: Literal["name", "date", "size", "duration", "resolution"] = "name",
    order: Optional[Literal["asc", "desc"]] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=video_library.MAX_LIMIT),
) -> Dict[str, Any]:
    """資料庫裡所有影片（不論有沒有命中規則；排除已刪除 / 遺失）。只讀：不能在這裡加入待刪。"""
    with SyncSessionLocal() as session:
        return video_library.list_videos(session, q=q, sort=sort, order=order, offset=offset, limit=limit)


@router.get("/summary")
def videos_summary() -> Dict[str, Any]:
    with SyncSessionLocal() as session:
        return video_library.summary(session)


@router.get("/ffmpeg")
def get_ffmpeg_status() -> Dict[str, Any]:
    return {**ffmpeg_service.status(), "cache": video_transcode.cache_usage()}


@router.get("/{asset_id}/info")
def get_video_info(asset_id: int) -> Dict[str, Any]:
    a = _load(asset_id)
    st = _stat_or_404(a["path"])
    ext = Path(a["path"]).suffix
    probe = ffmpeg_service.probe(a["path"])
    db = a["db"] or {}
    direct, _ = video_playback.decide(ext, probe, db.get("codec"))
    playback, reason = video_playback.classify(ext, probe, db.get("codec"))
    pv = (probe or {}).get("video") or {}
    ff = ffmpeg_service.find_ffmpeg()
    return {
        "asset_id": asset_id,
        "filename": _filename(a["path"]),
        "size": st.st_size,
        "extension": ext.lower(),
        "container": (probe or {}).get("container"),
        "duration": (probe or {}).get("duration") or db.get("duration"),
        "width": pv.get("width") or db.get("width"),
        "height": pv.get("height") or db.get("height"),
        "video_codec": pv.get("codec") or db.get("codec"),
        "audio_codec": ((probe or {}).get("audio") or {}).get("codec"),
        # playback：direct（保證可播）/ try_first（HEVC 等，先試播原檔，失敗再轉檔）/ transcode（一定要轉檔）
        "playback": playback,
        "direct_playable": direct,          # 保證可播（舊欄位）
        "needs_transcode": not direct,      # 不保證可播（舊欄位；是否一定要轉檔看 playback）
        "reason": reason,
        "ffmpeg_available": ff is not None,
        "install_hint": None if ff else ffmpeg_service.INSTALL_HINT,
        "stream_url": f"/api/videos/{asset_id}/stream?v={int(st.st_mtime)}",
        "transcode": _with_url(video_transcode.get_state(asset_id, a["path"]), asset_id, a["path"]),
    }


@router.head("/{asset_id}/stream", include_in_schema=False)
@router.get("/{asset_id}/stream")
def stream_original(asset_id: int, request: Request) -> Response:
    a = _load(asset_id)
    mime = video_playback.mime_for(Path(a["path"]).suffix)
    return range_response(a["path"], mime, request.headers.get("range"), head_only=request.method == "HEAD")


@router.post("/{asset_id}/transcode")
def start_transcode(asset_id: int) -> Dict[str, Any]:
    a = _load(asset_id)
    _stat_or_404(a["path"])
    duration = (a["db"] or {}).get("duration")
    try:
        state = video_transcode.start(asset_id, a["path"], duration=duration)
    except video_transcode.FfmpegMissing as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="檔案不存在（可能已移動、刪除或磁碟離線）")
    return _with_url(state, asset_id, a["path"])


@router.get("/{asset_id}/transcode")
def get_transcode_state(asset_id: int) -> Dict[str, Any]:
    a = _load(asset_id)
    return _with_url(video_transcode.get_state(asset_id, a["path"]), asset_id, a["path"])


@router.post("/{asset_id}/transcode/cancel")
def cancel_transcode(asset_id: int) -> Dict[str, Any]:
    _load(asset_id)
    return video_transcode.cancel(asset_id)


@router.head("/{asset_id}/transcoded", include_in_schema=False)
@router.get("/{asset_id}/transcoded")
def stream_transcoded(asset_id: int, request: Request) -> Response:
    a = _load(asset_id)
    f: Optional[Path] = video_transcode.cached_file(a["path"])
    if f is None:
        raise HTTPException(status_code=404, detail="這支影片還沒有轉檔結果")
    video_transcode.touch(f)
    return range_response(str(f), "video/mp4", request.headers.get("range"), head_only=request.method == "HEAD")
