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
"""

import logging
from pathlib import Path
from typing import Optional

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
    from datetime import datetime

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


def run_video_stage(
    session,
    retry_failed: bool = False,
    progress_tracker=None,
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
        session:          SQLAlchemy 同步 Session。
        retry_failed:     若為 True，亦重新處理有失敗記錄的資產。
        progress_tracker: 可選的進度追蹤物件（呼叫 .update(1) 推進進度）。

    Returns:
        dict with keys:
            - processed (int): 本次成功處理的資產數
            - done      (int): 現有記錄已存在且跳過的資產數
            - failed    (int): 本次分析失敗的資產數
    """
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
    if progress_tracker:
        progress_tracker.set_current_stage("video", total=total_videos)

    # 統計計數器
    processed = 0
    done = 0
    failed = 0

    for idx, asset in enumerate(video_assets):
        if progress_tracker and progress_tracker.is_cancel_requested():
            logger.info("[video_stage] 收到使用者取消請求，提前停止")
            break

        # 3. 確認實體檔案存在
        if not Path(asset.primary_path).exists():
            logger.debug(
                "[video_stage] 跳過 asset_id=%d，檔案不存在：%s",
                asset.id,
                asset.primary_path,
            )
            if progress_tracker:
                progress_tracker.update_analyze_progress(idx + 1, total_videos, asset.primary_path)
            continue

        # 4. 判斷是否需要處理
        video_rec: Optional[VideoInfo] = asset.video_info
        is_missing = video_rec is None
        is_failed_retry = asset.id in failed_ids

        if not is_missing and not is_failed_retry:
            # video_info 已存在，且不在重試清單中，跳過
            done += 1
            if progress_tracker:
                progress_tracker.update_analyze_progress(idx + 1, total_videos, asset.primary_path)
            continue

        # 5. 呼叫 video_probe 取得容器資訊
        try:
            probe_result: dict = video_probe.probe(asset.primary_path)
            # probe_result 包含：duration, width, height, codec（以及 size，但不儲存）

            # 6. Upsert VideoInfo
            if video_rec is None:
                video_rec = VideoInfo(asset_id=asset.id)
                session.add(video_rec)

            video_rec.duration = probe_result.get("duration")
            video_rec.width = probe_result.get("width")
            video_rec.height = probe_result.get("height")
            video_rec.codec = probe_result.get("codec")

            # 7. 成功後：清除失敗記錄、更新資產狀態
            _clear_analysis_failure(session, asset.id, "video")

            if asset.status == "unanalysed":
                asset.status = "analysed"
                logger.debug(
                    "[video_stage] asset_id=%d 狀態更新：unanalysed → analysed",
                    asset.id,
                )

            session.flush()
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
            # 8. 記錄失敗，繼續處理下一個資產
            error_msg = str(exc)
            logger.warning(
                "[video_stage] asset_id=%d 分析失敗：%s", asset.id, error_msg
            )
            _upsert_analysis_failure(session, asset.id, "video", error_msg)
            session.flush()
            failed += 1

        finally:
            if progress_tracker:
                progress_tracker.update_analyze_progress(idx + 1, total_videos, asset.primary_path)

    # 9. 統一提交
    session.commit()

    result = {
        "processed": processed,
        "done": done,
        "failed": failed,
    }
    logger.info("[video_stage] 完成：%s", result)
    return result
