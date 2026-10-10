"""
video_stage.py
==============
影片分析階段服務。

負責對所有影片類型資產執行容器資訊探查（duration、resolution、codec），
並將結果 upsert 至 `video_info` 資料表。

支援以下情境：
- 首次分析（無 `video_info` 記錄）
- 重試先前失敗的資產（`retry_failed=True`）

完成分析後，若資產狀態為 'unanalysed'，自動更新為 'analysed'。

Multi-threading
---------------
候選資產查詢在主線程完成。video_probe 探查分發至 `runners` 個 worker
執行緒（ThreadPoolExecutor），每個 worker 使用獨立 SyncSessionLocal session。
進度以 threading.Lock 保護的計數器推進。
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
    """回傳指定階段有失敗記錄的資產 ID 集合。"""
    rows = (
        session.query(AnalysisFailure.asset_id)
        .filter(AnalysisFailure.stage == stage)
        .all()
    )
    return {r.asset_id for r in rows}


def _upsert_analysis_failure(
    session, asset_id: int, stage: str, error_msg: str
) -> None:
    """
    Upsert 分析失敗記錄。

    若同一資產同一階段已有失敗記錄，更新錯誤訊息與時間；
    否則新增一筆記錄。
    """
    existing = (
        session.query(AnalysisFailure)
        .filter_by(asset_id=asset_id, stage=stage)
        .first()
    )
    if existing:
        existing.error_msg = error_msg
        existing.occurred_at = datetime.utcnow()
    else:
        session.add(
            AnalysisFailure(
                asset_id=asset_id,
                stage=stage,
                error_msg=error_msg,
                occurred_at=datetime.utcnow(),
            )
        )


def _clear_analysis_failure(session, asset_id: int, stage: str) -> None:
    """清除指定資產指定階段的失敗記錄（分析成功後呼叫）。"""
    session.query(AnalysisFailure).filter_by(asset_id=asset_id, stage=stage).delete()


def _split_chunks(items: list, n: int) -> list[list]:
    """將 items 均分為最多 n 個非空 chunk。"""
    if not items:
        return []
    n = min(n, len(items))
    size = math.ceil(len(items) / n)
    return [items[i : i + size] for i in range(0, len(items), size)]


# ─────────────────────────────────────────────
# Worker
# ─────────────────────────────────────────────

def _video_worker(
    asset_ids: list[int],
    failed_ids: set[int],
    cancel_event: threading.Event,
    progress_lock: threading.Lock,
    progress_counter: list,
    total_videos: int,
    progress_tracker,
) -> dict:
    """
    Worker 函式：在獨立 session 中對指定影片資產列表執行探查。

    Returns
    -------
    dict with keys: processed, done, failed
    """
    from app.core.database import SyncSessionLocal

    processed = 0
    done = 0
    failed = 0

    with SyncSessionLocal() as session:
        for asset_id in asset_ids:
            if cancel_event.is_set():
                break

            asset: Optional[Asset] = session.get(Asset, asset_id)
            if asset is None:
                with progress_lock:
                    progress_counter[0] += 1
                continue

            primary_path = asset.primary_path

            # 確認實體檔案存在
            if not Path(primary_path).exists():
                logger.debug(
                    "[video_stage] 跳過 asset_id=%d，檔案不存在：%s",
                    asset.id, primary_path,
                )
                with progress_lock:
                    progress_counter[0] += 1
                    current = progress_counter[0]
                if progress_tracker:
                    try:
                        progress_tracker.update_analyze_progress(current, total_videos, primary_path)
                    except InterruptedError:
                        cancel_event.set()
                continue

            # 判斷是否需要處理
            video_rec: Optional[VideoInfo] = asset.video_info
            is_missing = video_rec is None
            is_failed_retry = asset.id in failed_ids

            if not is_missing and not is_failed_retry:
                done += 1
                with progress_lock:
                    progress_counter[0] += 1
                    current = progress_counter[0]
                if progress_tracker:
                    try:
                        progress_tracker.update_analyze_progress(current, total_videos, primary_path)
                    except InterruptedError:
                        cancel_event.set()
                continue

            # 呼叫 video_probe 取得容器資訊
            try:
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
                    logger.debug(
                        "[video_stage] asset_id=%d 狀態更新：unanalysed → analysed",
                        asset.id,
                    )

                session.flush()
                session.commit()
                processed += 1
                logger.debug(
                    "[video_stage] asset_id=%d probe 完成：duration=%s, %sx%s, codec=%s",
                    asset.id,
                    probe_result.get("duration"),
                    probe_result.get("width"),
                    probe_result.get("height"),
                    probe_result.get("codec"),
                )

            except Exception as exc:
                session.rollback()
                error_msg = str(exc)
                logger.warning(
                    "[video_stage] asset_id=%d 分析失敗：%s", asset.id, error_msg
                )
                try:
                    _upsert_analysis_failure(session, asset.id, "video", error_msg)
                    session.flush()
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


# ─────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────

def run_video_stage(
    session,
    retry_failed: bool = False,
    progress_tracker=None,
    runners: Optional[int] = None,
) -> dict:
    """
    執行影片分析階段。

    遍歷所有影片類型資產，呼叫 `video_probe.probe()` 取得容器資訊，
    並將結果 upsert 至 `video_info` 資料表。

    需要分析的資產條件（符合其一即處理）：
    1. 尚無 `video_info` 記錄
    2. 若 `retry_failed=True`：之前在 'video' 階段失敗的資產

    跳過條件：
    - `asset.status` 為 'deleted' 或 'missing'
    - `asset.primary_path` 所指向的實體檔案不存在

    分析成功後，若資產狀態為 'unanalysed'，自動更新為 'analysed'。

    Args:
        session:          SQLAlchemy 同步 Session（候選資產查詢用）。
        retry_failed:     若為 True，亦重新處理有失敗記錄的資產。
        progress_tracker: 可選的進度追蹤物件。
        runners:          並行 worker 線程數。預設使用 settings.VIDEO_RUNNERS。

    Returns:
        dict with keys:
            - processed (int): 本次成功處理的資產數
            - done      (int): 現有記錄已存在且跳過的資產數
            - failed    (int): 本次分析失敗的資產數
    """
    if runners is None:
        runners = settings.VIDEO_RUNNERS
    runners = max(1, runners)

    # 1. 取得有 video 失敗記錄的資產 ID（若需重試）
    failed_ids: set[int] = set()
    if retry_failed:
        failed_ids = _get_failed_asset_ids(session, "video")
        logger.info(
            "[video_stage] 找到 %d 筆先前影片分析失敗記錄，將重試", len(failed_ids)
        )

    # 2. 查詢所有影片類型、且未刪除/遺失的資產
    video_assets: list[Asset] = (
        session.query(Asset)
        .filter(
            Asset.media_type == "video",
            Asset.status.notin_(["deleted", "missing"]),
        )
        .all()
    )

    total_videos = len(video_assets)
    asset_ids = [a.id for a in video_assets]

    if progress_tracker:
        progress_tracker.set_current_stage("video", total=total_videos)

    logger.info(
        "[video_stage] %d 個影片資產，使用 %d runner(s)", total_videos, runners
    )

    if total_videos == 0:
        result = {"processed": 0, "done": 0, "failed": 0}
        logger.info("[video_stage] 完成：%s", result)
        return result

    # 3. 並行執行前 commit，確保 worker session 可見主 session 的最新資料
    session.commit()

    cancel_event = threading.Event()
    if progress_tracker and progress_tracker.is_cancel_requested():
        cancel_event.set()

    progress_lock = threading.Lock()
    progress_counter = [0]

    chunks = _split_chunks(asset_ids, runners)
    actual_runners = len(chunks)
    logger.info(
        "[video_stage] dispatching %d asset(s) across %d thread(s)",
        total_videos, actual_runners,
    )

    total_processed = 0
    total_done = 0
    total_failed = 0

    with ThreadPoolExecutor(max_workers=actual_runners) as executor:
        futures = [
            executor.submit(
                _video_worker,
                chunk,
                failed_ids,
                cancel_event,
                progress_lock,
                progress_counter,
                total_videos,
                progress_tracker,
            )
            for chunk in chunks
        ]
        for future in as_completed(futures):
            try:
                r = future.result()
                total_processed += r["processed"]
                total_done += r["done"]
                total_failed += r["failed"]
            except Exception as exc:
                logger.error("[video_stage] worker raised unexpected error: %s", exc, exc_info=True)

    # Worker 在獨立 session 中 commit，主 session 的 identity map 已過期。
    session.expire_all()

    result = {
        "processed": total_processed,
        "done": total_done,
        "failed": total_failed,
    }
    logger.info("[video_stage] 完成：%s", result)
    return result
