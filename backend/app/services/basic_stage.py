"""
basic_stage.py
──────────────
Analysis stage: thumbnail generation + basic feature extraction.

For each photo/raw asset that is missing a Feature record, has a failed
decode, or is missing its cached thumbnail on disk, this module:
  1. Generates (or regenerates) the thumbnail via ThumbnailService.
  2. Calls FeaturePipeline.extract_basic() to obtain pHash, blur score,
     brightness, dimensions, EXIF, taken_at, and decode_status.
  3. Upserts the Feature record.
  4. Promotes asset.status from 'unanalysed' → 'analysed' on success.
  5. Records any per-file error in analysis_failures and sets
     features.decode_status = 'error'.

Video assets are deliberately skipped — they have their own stage.
Deleted / missing assets are also skipped.

Multi-threading
---------------
Candidate filtering (_needs_processing) runs in the main thread.
Actual thumbnail + feature extraction is dispatched across `runners`
threads via ThreadPoolExecutor.  Each worker opens its own
SyncSessionLocal session.  A threading.Lock-protected counter drives
progress reporting to progress_tracker.
"""

import logging
import math
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.schema import Asset, Feature, AnalysisFailure
from app.services.thumbnail import thumbnail_service
from app.services.feature_pipeline import feature_pipeline

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _thumb_exists(asset: Asset) -> bool:
    """
    Return True when the thumbnail for *asset* is already present in
    the THUMBS_CACHE_DIR, using content/metadata aware cache key.
    """
    primary_file = None
    if asset.files:
        for f in asset.files:
            if f.role == "primary":
                primary_file = f
                break
    mtime = primary_file.mtime if primary_file else None
    size = primary_file.size if primary_file else None
    companion = asset.paired_paths[0] if (asset.paired_paths and len(asset.paired_paths) > 0) else None
    return thumbnail_service.has_thumbnail(
        file_path=asset.primary_path,
        mtime=mtime,
        size=size,
        companion_path=companion
    )


def _upsert_failure(session: Session, asset_id: int, stage: str, msg: str) -> None:
    """Replace any existing failure record for (asset_id, stage) with a new one."""
    session.query(AnalysisFailure).filter(
        AnalysisFailure.asset_id == asset_id,
        AnalysisFailure.stage == stage,
    ).delete()
    session.add(AnalysisFailure(
        asset_id=asset_id,
        stage=stage,
        error_msg=msg,
        occurred_at=datetime.utcnow(),
    ))


def _needs_processing(asset: Asset, retry_failed: bool) -> bool:
    """
    Decide whether *asset* should pass through the basic stage.

    An asset needs processing when ANY of:
    - It has no Feature record yet.
    - Its thumbnail is absent from the cache (may have been purged or modified).
    - retry_failed=True AND features.decode_status == 'error'.
    """
    feat: Optional[Feature] = asset.features

    # No feature record at all
    if feat is None:
        return True

    # Thumbnail missing on disk
    if not _thumb_exists(asset):
        return True

    # Errored decode and caller wants a retry
    if retry_failed and feat.decode_status == "error":
        return True

    return False


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

def _basic_worker(
    asset_ids: list[int],
    cancel_event: threading.Event,
    progress_lock: threading.Lock,
    progress_counter: list,
    total_assets: int,
    progress_tracker,
) -> dict:
    """
    Worker 函式：在獨立 session 中對指定資產列表執行縮圖生成與基本特徵提取。

    Returns
    -------
    dict with keys: done, failed
    """
    from app.core.database import SyncSessionLocal

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
            is_raw = asset.media_type == "raw"

            try:
                # ── 1. Generate / refresh thumbnail ──────────────────────────
                primary_file = None
                if asset.files:
                    for f in asset.files:
                        if f.role == "primary":
                            primary_file = f
                            break
                mtime_v = primary_file.mtime if primary_file else None
                size_v = primary_file.size if primary_file else None
                companion_v = (
                    asset.paired_paths[0]
                    if (asset.paired_paths and len(asset.paired_paths) > 0)
                    else None
                )

                thumbnail_service.generate_thumbnail(
                    file_path=primary_path,
                    companion_path=companion_v,
                    mtime=mtime_v,
                    size=size_v,
                )

                # ── 2. Extract basic features ─────────────────────────────────
                feats = feature_pipeline.extract_basic(primary_path, is_raw=is_raw)

                # ── 3. Upsert Feature record ──────────────────────────────────
                feat_record: Optional[Feature] = asset.features
                if feat_record is None:
                    feat_record = Feature(asset_id=asset.id)
                    session.add(feat_record)

                feat_record.phash         = feats["phash"]
                feat_record.blur_score    = feats["blur_score"]
                feat_record.brightness    = feats["brightness"]
                feat_record.width         = feats["width"]
                feat_record.height        = feats["height"]
                feat_record.taken_at      = feats["taken_at"]
                feat_record.exif          = feats["exif"]
                feat_record.decode_status = feats["decode_status"]
                feat_record.analyzed_at   = datetime.utcnow()

                # ── 4. Promote asset status ───────────────────────────────────
                if asset.status == "unanalysed":
                    asset.status = "analysed"

                # ── 5. Clear any prior failure record ─────────────────────────
                session.query(AnalysisFailure).filter(
                    AnalysisFailure.asset_id == asset.id,
                    AnalysisFailure.stage == "basic",
                ).delete()

                session.flush()
                session.commit()
                done += 1
                logger.debug("basic_stage: completed asset %d (%s)", asset.id, primary_path)

            except Exception as exc:
                session.rollback()
                logger.warning(
                    "basic_stage: error processing asset %d (%s) — %s",
                    asset_id, primary_path, exc,
                )
                try:
                    asset = session.get(Asset, asset_id)
                    feat_record = asset.features if asset else None
                    if feat_record is None and asset:
                        feat_record = Feature(
                            asset_id=asset_id,
                            decode_status="error",
                            analyzed_at=datetime.utcnow(),
                        )
                        session.add(feat_record)
                    elif feat_record:
                        feat_record.decode_status = "error"
                        feat_record.analyzed_at = datetime.utcnow()

                    _upsert_failure(session, asset_id, "basic", str(exc))
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
                        progress_tracker.update_analyze_progress(
                            current, total_assets, primary_path
                        )
                    except InterruptedError:
                        cancel_event.set()

    return {"done": done, "failed": failed}


# ─────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────

def run_basic_stage(
    session: Session,
    retry_failed: bool = False,
    merge_with_clip: bool = False,
    progress_tracker=None,
    runners: Optional[int] = None,
) -> dict:
    """
    Execute the basic analysis stage.

    Parameters
    ----------
    session        : Active SQLAlchemy sync session.  Used only for the
                     initial candidate query; workers open their own sessions.
    retry_failed   : If True, assets whose Feature.decode_status == 'error'
                     are re-processed.
    merge_with_clip: Reserved for A-3 merge-decode optimization (currently
                     no-op at the worker level).
    progress_tracker : Optional object with `is_cancel_requested() -> bool`.
    runners        : Number of parallel worker threads.  Defaults to
                     settings.BASIC_RUNNERS.

    Returns
    -------
    dict with keys: processed, done, failed
    """
    if runners is None:
        runners = settings.BASIC_RUNNERS
    runners = max(1, runners)

    stats = {"processed": 0, "done": 0, "failed": 0}

    # ── Fetch candidates (main thread) ────────────────────────────────────────
    candidates = (
        session.query(Asset)
        .filter(
            Asset.media_type.in_(["photo", "raw"]),
            Asset.status.notin_(["deleted", "missing"]),
        )
        .all()
    )

    total_candidates = len(candidates)
    if progress_tracker:
        progress_tracker.set_current_stage("basic", total=total_candidates)

    logger.info("basic_stage: evaluating %d candidate asset(s)", total_candidates)

    # ── Filter assets that need processing ────────────────────────────────────
    to_process_ids = [
        asset.id
        for asset in candidates
        if _needs_processing(asset, retry_failed)
    ]

    total_to_process = len(to_process_ids)
    not_needed = total_candidates - total_to_process

    # 未需要處理的資產也需推進進度計數（保持進度條正確）
    # 但已全部放在 total_candidates 的 total 中，worker 進度會從 0 跑到 total_to_process
    # 更新 total 以反映實際工作量
    if progress_tracker:
        progress_tracker.set_current_stage("basic", total=total_to_process)

    logger.info(
        "basic_stage: %d to process, %d already done, using %d runner(s)",
        total_to_process, not_needed, runners,
    )

    if total_to_process == 0:
        logger.info("basic_stage complete — nothing to process")
        return stats

    # ── Commit before dispatch ─────────────────────────────────────────────────
    # Worker 使用獨立 session，只能看到已 commit 的資料。
    # 此處 commit 確保主 session 中 flush 但未 commit 的資料對 worker 可見。
    session.commit()

    # ── Cancel check before dispatch ──────────────────────────────────────────
    cancel_event = threading.Event()
    if progress_tracker and progress_tracker.is_cancel_requested():
        cancel_event.set()

    progress_lock = threading.Lock()
    progress_counter = [0]

    chunks = _split_chunks(to_process_ids, runners)
    actual_runners = len(chunks)
    logger.info(
        "basic_stage: dispatching %d asset(s) across %d thread(s)",
        total_to_process, actual_runners,
    )

    # ── Parallel execution ────────────────────────────────────────────────────
    with ThreadPoolExecutor(max_workers=actual_runners) as executor:
        futures = [
            executor.submit(
                _basic_worker,
                chunk,
                cancel_event,
                progress_lock,
                progress_counter,
                total_to_process,
                progress_tracker,
            )
            for chunk in chunks
        ]
        for future in as_completed(futures):
            try:
                result = future.result()
                stats["done"] += result["done"]
                stats["failed"] += result["failed"]
                stats["processed"] += result["done"] + result["failed"]
            except Exception as exc:
                logger.error("basic_stage: worker raised unexpected error: %s", exc, exc_info=True)

    # Worker 在獨立 session 中 commit，主 session 的 identity map 已過期。
    # expire_all 確保後續查詢從 DB 重新讀取最新資料。
    session.expire_all()

    logger.info(
        "basic_stage complete — processed=%d done=%d failed=%d",
        stats["processed"],
        stats["done"],
        stats["failed"],
    )
    return stats


def run_rebuild_thumbs_only(
    session: Session,
    progress_tracker=None,
) -> dict:
    """
    補算缺少縮圖的相片，不重跑其他分析（對應 A-20）。
    只針對縮圖檔不存在、且已有 Feature 記錄（basic 已完成）的資產。
    """
    stats = {"processed": 0, "done": 0, "failed": 0}

    assets = (
        session.query(Asset)
        .filter(
            Asset.media_type.in_(["photo", "raw"]),
            Asset.status.notin_(["deleted", "missing"]),
        )
        .all()
    )

    candidates_for_rebuild = [
        a for a in assets
        if a.features is not None and not _thumb_exists(a)
    ]

    total = len(candidates_for_rebuild)
    if progress_tracker:
        progress_tracker.state.total_files = total
        progress_tracker.broadcast()

    logger.info("rebuild_thumbs: %d asset(s) need thumbnail rebuild", total)

    for idx, asset in enumerate(candidates_for_rebuild):
        if progress_tracker is not None and progress_tracker.is_cancel_requested():
            break
        stats["processed"] += 1
        try:
            primary_file = None
            if asset.files:
                for f in asset.files:
                    if f.role == "primary":
                        primary_file = f
                        break
            mtime_v = primary_file.mtime if primary_file else None
            size_v = primary_file.size if primary_file else None
            companion_v = asset.paired_paths[0] if (asset.paired_paths and len(asset.paired_paths) > 0) else None

            thumbnail_service.generate_thumbnail(
                file_path=asset.primary_path,
                companion_path=companion_v,
                mtime=mtime_v,
                size=size_v
            )
            stats["done"] += 1
        except Exception as exc:
            logger.warning("rebuild_thumbs: failed for %s — %s", asset.primary_path, exc)
            stats["failed"] += 1
        finally:
            if progress_tracker:
                progress_tracker.update_analyze_progress(idx + 1, total, asset.primary_path)

    return stats
