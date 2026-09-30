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
"""

import hashlib
import logging
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

def _thumb_exists(primary_path: str) -> bool:
    """
    Return True when the thumbnail for *primary_path* is already present in
    the THUMBS_CACHE_DIR.  Mirrors the naming scheme used by ThumbnailService.
    """
    sha = hashlib.sha256(primary_path.encode()).hexdigest()
    thumb_path = settings.THUMBS_CACHE_DIR / sha[:2] / f"{sha}.jpg"
    return thumb_path.exists()


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
    - Its thumbnail is absent from the cache (may have been purged).
    - retry_failed=True AND features.decode_status == 'error'.
    """
    feat: Optional[Feature] = asset.features

    # No feature record at all
    if feat is None:
        return True

    # Thumbnail missing on disk
    if not _thumb_exists(asset.primary_path):
        return True

    # Errored decode and caller wants a retry
    if retry_failed and feat.decode_status == "error":
        return True

    return False


# ─────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────

def run_basic_stage(
    session: Session,
    retry_failed: bool = False,
    merge_with_clip: bool = False,
    progress_tracker=None,
) -> dict:
    """
    Execute the basic analysis stage.

    Parameters
    ----------
    session        : Active SQLAlchemy sync session.  The caller is
                     responsible for commit / rollback.
    retry_failed   : If True, assets whose Feature.decode_status == 'error'
                     are re-processed.
    merge_with_clip: If True (set by analyze_job when running basic+clip together),
                     the opened PIL image is stored in the Feature record for
                     the clip_stage to reuse (A-3 merge decode).
                     Currently the stub doesn't need actual image object sharing,
                     so this flag is reserved for future real implementation.
    progress_tracker : Optional object with `is_cancel_requested() -> bool`.

    Returns
    -------
    dict with keys: processed, done, failed
    """
    stats = {"processed": 0, "done": 0, "failed": 0}

    # Fetch candidate assets: photo and raw only, not deleted/missing
    candidates = (
        session.query(Asset)
        .filter(
            Asset.media_type.in_(["photo", "raw"]),
            Asset.status.notin_(["deleted", "missing"]),
        )
        # Eagerly load the features relationship to avoid N+1 queries
        .all()
    )

    logger.info("basic_stage: evaluating %d candidate asset(s)", len(candidates))

    for asset in candidates:
        # Respect cancellation
        if progress_tracker is not None and progress_tracker.is_cancel_requested():
            logger.info("basic_stage: cancel requested, stopping early")
            break

        if not _needs_processing(asset, retry_failed):
            continue

        stats["processed"] += 1
        primary_path = asset.primary_path
        is_raw = asset.media_type == "raw"

        try:
            # ── 1. Generate / refresh thumbnail ──────────────────────────────
            thumbnail_service.generate_thumbnail(primary_path)

            # ── 2. Extract basic features ─────────────────────────────────────
            feats = feature_pipeline.extract_basic(primary_path, is_raw=is_raw)
            # feats keys: phash, blur_score, brightness, width, height,
            #             taken_at, exif, decode_status

            # ── 3. Upsert Feature record ───────────────────────────────────────
            feat_record: Optional[Feature] = asset.features
            if feat_record is None:
                feat_record = Feature(asset_id=asset.id)
                session.add(feat_record)

            feat_record.phash        = feats["phash"]
            feat_record.blur_score   = feats["blur_score"]
            feat_record.brightness   = feats["brightness"]
            feat_record.width        = feats["width"]
            feat_record.height       = feats["height"]
            feat_record.taken_at     = feats["taken_at"]
            feat_record.exif         = feats["exif"]
            feat_record.decode_status = feats["decode_status"]
            feat_record.analyzed_at  = datetime.utcnow()

            # ── 4. Promote asset status ────────────────────────────────────────
            if asset.status == "unanalysed":
                asset.status = "analysed"

            # ── 5. Clear any prior failure record for this stage ──────────────
            session.query(AnalysisFailure).filter(
                AnalysisFailure.asset_id == asset.id,
                AnalysisFailure.stage == "basic",
            ).delete()

            session.flush()
            stats["done"] += 1
            logger.debug("basic_stage: completed asset %d (%s)", asset.id, primary_path)

        except Exception as exc:
            logger.warning(
                "basic_stage: error processing asset %d (%s) — %s",
                asset.id, primary_path, exc
            )

            # Ensure a Feature row exists so we can record the error status
            feat_record = asset.features
            if feat_record is None:
                feat_record = Feature(
                    asset_id=asset.id,
                    decode_status="error",
                    analyzed_at=datetime.utcnow(),
                )
                session.add(feat_record)
            else:
                feat_record.decode_status = "error"
                feat_record.analyzed_at   = datetime.utcnow()

            _upsert_failure(session, asset.id, "basic", str(exc))
            session.flush()
            stats["failed"] += 1

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
        if a.features is not None and not _thumb_exists(a.primary_path)
    ]

    logger.info("rebuild_thumbs: %d asset(s) need thumbnail rebuild", len(candidates_for_rebuild))

    for asset in candidates_for_rebuild:
        if progress_tracker is not None and progress_tracker.is_cancel_requested():
            break
        stats["processed"] += 1
        try:
            thumbnail_service.generate_thumbnail(asset.primary_path)
            stats["done"] += 1
        except Exception as exc:
            logger.warning("rebuild_thumbs: failed for %s — %s", asset.primary_path, exc)
            stats["failed"] += 1

    return stats

