"""
hash_stage.py
─────────────
Analysis stage: SHA-256 hashing for duplicate-size detection.

Strategy
--------
1. Build a set of file sizes that appear more than once across *all* active
   PRIMARY-role files (i.e. assets not deleted/missing).
2. Files whose size is UNIQUE get `hash_state='not_needed'` — no hash is
   required because they cannot be an exact duplicate.
3. Files whose size is DUPLICATED get SHA-256 computed; `hash_state` is set
   to 'done' on success or 'error' on failure.
4. Before processing, re-evaluate previously 'not_needed' files: if a
   same-size file has since been added, reset them to 'pending' so they get
   hashed in this run.
5. When `retry_failed=True`, 'error' files are also reset to 'pending'.
"""

import hashlib
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.models.schema import File, Asset, AnalysisFailure

logger = logging.getLogger(__name__)

DEFAULT_CHUNK = 1024 * 1024  # 1 MB read buffer


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _compute_sha256(path: str) -> str:
    """Stream-read a file and return its hex-encoded SHA-256 digest."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(DEFAULT_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _upsert_failure(session: Session, asset_id: int, stage: str, msg: str) -> None:
    """Replace any existing failure record for (asset_id, stage) with a fresh one."""
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


def _duplicate_sizes_subquery(session: Session):
    """
    Return a scalar subquery / set of sizes that appear more than once among
    PRIMARY files whose owning asset is NOT deleted or missing.
    """
    # Sizes that appear on ≥2 distinct primary files across active assets
    dup_sizes = (
        session.query(File.size)
        .join(Asset, Asset.id == File.asset_id)
        .filter(
            File.role == "primary",
            Asset.status.notin_(["deleted", "missing"]),
        )
        .group_by(File.size)
        .having(func.count(File.id) > 1)
        .subquery()
    )
    return dup_sizes


# ─────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────

def run_hash_stage(
    session: Session,
    retry_failed: bool = False,
    progress_tracker=None,
) -> dict:
    """
    Execute the hash analysis stage.

    Parameters
    ----------
    session        : Active SQLAlchemy sync session (caller manages commit/rollback).
    retry_failed   : If True, reset files with `hash_state='error'` to 'pending'
                     so they are retried in this run.
    progress_tracker : Optional ProgressTracker-like object; checked for
                     `is_cancel_requested()` on each iteration.

    Returns
    -------
    dict with keys: processed, done, not_needed, failed
    """
    stats = {"processed": 0, "done": 0, "not_needed": 0, "failed": 0}

    # ── Step 1: Optionally reset error files ──────────────────────────────────
    if retry_failed:
        # Fetch IDs first (join + update not allowed in SQLAlchemy legacy query)
        error_file_ids = [
            f.id for f in session.query(File.id)
            .join(Asset, Asset.id == File.asset_id)
            .filter(
                File.role == "primary",
                File.hash_state == "error",
                Asset.status.notin_(["deleted", "missing"]),
            )
            .all()
        ]
        if error_file_ids:
            session.query(File).filter(File.id.in_(error_file_ids)).update(
                {"hash_state": "pending"}, synchronize_session="fetch"
            )
            logger.info("hash_stage: reset %d error file(s) to pending", len(error_file_ids))

    # ── Step 2: Build the duplicate-size set ──────────────────────────────────
    dup_sizes_sub = _duplicate_sizes_subquery(session)

    # ── Step 3: Re-evaluate 'not_needed' files ────────────────────────────────
    # A file previously marked not_needed may now have a size-twin because new
    # assets were added since the last run.  Reset those to 'pending'.
    not_needed_to_reset = (
        session.query(File)
        .join(Asset, Asset.id == File.asset_id)
        .filter(
            File.role == "primary",
            File.hash_state == "not_needed",
            Asset.status.notin_(["deleted", "missing"]),
            File.size.in_(select(dup_sizes_sub)),
        )
        .all()
    )
    if not_needed_to_reset:
        for f in not_needed_to_reset:
            f.hash_state = "pending"
        session.flush()
        logger.info(
            "hash_stage: reset %d 'not_needed' file(s) to 'pending' (size now duplicated)",
            len(not_needed_to_reset),
        )

    # ── Step 4: Mark unique-size files as not_needed ──────────────────────────
    # Fetch IDs of pending primary files with unique sizes (join + update not allowed)
    unique_file_ids = [
        f.id for f in session.query(File.id)
        .join(Asset, Asset.id == File.asset_id)
        .filter(
            File.role == "primary",
            File.hash_state == "pending",
            Asset.status.notin_(["deleted", "missing"]),
            File.size.notin_(select(dup_sizes_sub)),
        )
        .all()
    ]
    unique_size_count = len(unique_file_ids)
    if unique_file_ids:
        session.query(File).filter(File.id.in_(unique_file_ids)).update(
            {"hash_state": "not_needed"}, synchronize_session="fetch"
        )
    stats["not_needed"] += unique_size_count
    stats["processed"] += unique_size_count
    if unique_size_count:
        logger.debug("hash_stage: %d file(s) marked 'not_needed' (unique size)", unique_size_count)

    session.flush()

    # ── Step 5: Fetch pending files that need a hash ───────────────────────────
    pending_files = (
        session.query(File)
        .join(Asset, Asset.id == File.asset_id)
        .filter(
            File.role == "primary",
            File.hash_state == "pending",
            Asset.status.notin_(["deleted", "missing"]),
            File.size.in_(select(dup_sizes_sub)),
        )
        .all()
    )

    total_hash_files = len(pending_files)
    if progress_tracker:
        progress_tracker.set_current_stage("hash", total=total_hash_files)

    logger.info("hash_stage: %d file(s) to hash", total_hash_files)

    # ── Step 6: Hash each pending file ────────────────────────────────────────
    for idx, file_record in enumerate(pending_files):
        # Respect cancellation request
        if progress_tracker is not None and progress_tracker.is_cancel_requested():
            logger.info("hash_stage: cancel requested, stopping early")
            break

        stats["processed"] += 1

        try:
            sha = _compute_sha256(file_record.path)
            file_record.sha256 = sha
            file_record.hash_state = "done"
            stats["done"] += 1
            logger.debug("hash_stage: hashed %s → %s", file_record.path, sha[:12])

        except Exception as exc:
            logger.warning("hash_stage: error hashing %s — %s", file_record.path, exc)
            file_record.hash_state = "error"
            _upsert_failure(session, file_record.asset_id, "hash", str(exc))
            stats["failed"] += 1

        finally:
            if progress_tracker:
                progress_tracker.update_analyze_progress(idx + 1, total_hash_files, file_record.path)

        # Flush periodically to keep memory usage low
        session.flush()

    logger.info(
        "hash_stage complete — processed=%d done=%d not_needed=%d failed=%d",
        stats["processed"],
        stats["done"],
        stats["not_needed"],
        stats["failed"],
    )
    return stats
