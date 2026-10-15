"""
video_stage.py（v0.5）
======================
影片容器資訊探查（duration、resolution、codec），結果 upsert 至 video_info。
- 檔案不存在、OpenCV 打不開或讀不到資訊 → 記錄到 analysis_failures（不再當成功）。
"""
import logging
import math
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.core.config import settings
from app.models.schema import Asset, VideoInfo, AnalysisFailure
from app.stubs.video_probe_stub import video_probe

logger = logging.getLogger(__name__)


def _get_failed_asset_ids(session, stage: str) -> set[int]:
    rows = session.query(AnalysisFailure.asset_id).filter(AnalysisFailure.stage == stage).all()
    return {r.asset_id for r in rows}


def _upsert_analysis_failure(session, asset_id: int, stage: str, error_msg: str) -> None:
    existing = session.query(AnalysisFailure).filter_by(asset_id=asset_id, stage=stage).first()
    if existing:
        existing.error_msg = error_msg
        existing.occurred_at = datetime.utcnow()
    else:
        session.add(AnalysisFailure(asset_id=asset_id, stage=stage, error_msg=error_msg,
                                    occurred_at=datetime.utcnow()))


def _clear_analysis_failure(session, asset_id: int, stage: str) -> None:
    session.query(AnalysisFailure).filter_by(asset_id=asset_id, stage=stage).delete()


def _split_chunks(items: list, n: int) -> list[list]:
    if not items:
        return []
    n = min(n, len(items))
    size = math.ceil(len(items) / n)
    return [items[i: i + size] for i in range(0, len(items), size)]


def _video_worker(asset_ids, failed_ids, cancel_event, progress_lock, progress_counter,
                  total_videos, progress_tracker) -> dict:
    from app.core.database import SyncSessionLocal
    processed = done = failed = 0
    with SyncSessionLocal() as session:
        for asset_id in asset_ids:
            if cancel_event.is_set():
                break
            asset: Optional[Asset] = session.get(Asset, asset_id)
            if asset is None:
                continue
            primary_path = asset.primary_path
            try:
                video_rec: Optional[VideoInfo] = asset.video_info
                if video_rec is not None and asset.id not in failed_ids:
                    done += 1
                    continue
                if not Path(primary_path).exists():
                    raise FileNotFoundError(f"檔案不存在：{primary_path}")
                probe_result: dict = video_probe.probe(primary_path)
                if video_rec is None:
                    video_rec = VideoInfo(asset_id=asset.id)
                    session.add(video_rec)
                video_rec.duration = probe_result.get("duration")
                video_rec.width = probe_result.get("width")
                video_rec.height = probe_result.get("height")
                video_rec.codec = probe_result.get("codec")
                _clear_analysis_failure(session, asset.id, "video")
                if asset.status == "unanalysed":
                    asset.status = "analysed"
                session.commit()
                processed += 1
            except Exception as exc:
                session.rollback()
                logger.warning("[video_stage] asset_id=%d 分析失敗：%s", asset_id, exc)
                try:
                    _upsert_analysis_failure(session, asset_id, "video", f"{type(exc).__name__}: {exc}")
                    session.commit()
                except Exception:
                    session.rollback()
                failed += 1
            finally:
                with progress_lock:
                    progress_counter[0] += 1
                    current = progress_counter[0]
                if progress_tracker:
                    try:
                        progress_tracker.update_analyze_progress(current, total_videos, primary_path)
                    except InterruptedError:
                        cancel_event.set()
    return {"processed": processed, "done": done, "failed": failed}


def run_video_stage(session, retry_failed: bool = False, progress_tracker=None,
                    runners: Optional[int] = None) -> dict:
    """
    處理對象：尚無 video_info 的影片；retry_failed=True 時也重試有失敗記錄者。
    回傳 {processed, done, failed}。
    """
    if runners is None:
        runners = settings.VIDEO_RUNNERS
    runners = max(1, runners)
    failed_ids: set[int] = _get_failed_asset_ids(session, "video") if retry_failed else set()
    video_assets = (
        session.query(Asset)
        .filter(Asset.media_type == "video", Asset.status.notin_(["deleted", "missing"]))
        .all()
    )
    # 已有失敗記錄、但這次不重試的，直接略過
    all_failed = _get_failed_asset_ids(session, "video")
    asset_ids = [
        a.id for a in video_assets
        if (a.video_info is None and (a.id not in all_failed or retry_failed)) or a.id in failed_ids
    ]
    total_videos = len(asset_ids)
    if progress_tracker:
        progress_tracker.set_current_stage("video", total=total_videos, runners=runners)
    logger.info("[video_stage] %d 個影片待處理（共 %d），%d runner(s)", total_videos, len(video_assets), runners)
    if total_videos == 0:
        return {"processed": 0, "done": len(video_assets), "failed": 0}
    session.commit()
    cancel_event = threading.Event()
    if progress_tracker and progress_tracker.is_cancel_requested():
        cancel_event.set()
    progress_lock = threading.Lock()
    progress_counter = [0]
    chunks = _split_chunks(asset_ids, runners)
    totals = {"processed": 0, "done": 0, "failed": 0}
    with ThreadPoolExecutor(max_workers=len(chunks)) as executor:
        futures = [
            executor.submit(_video_worker, chunk, failed_ids, cancel_event, progress_lock,
                            progress_counter, total_videos, progress_tracker)
            for chunk in chunks
        ]
        for future in as_completed(futures):
            try:
                r = future.result()
                for k in totals:
                    totals[k] += r[k]
            except Exception as exc:
                logger.error("[video_stage] worker raised unexpected error: %s", exc, exc_info=True)
    session.expire_all()
    if cancel_event.is_set() and progress_tracker:
        raise InterruptedError("使用者已取消分析任務")
    logger.info("[video_stage] 完成：%s", totals)
    return totals
