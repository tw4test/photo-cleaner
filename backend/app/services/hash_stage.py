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

Multi-threading
---------------
Steps 1-4 run in the main thread (batch UPDATEs via the caller session).
Step 5 dispatches hashing work across `runners` threads using
ThreadPoolExecutor.  Each worker thread opens its own SyncSessionLocal
session to avoid SQLite locking conflicts.  A threading.Lock-protected
counter drives progress reporting.
"""

import hashlib
import logging
import math
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.core.config import settings
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

def _hash_worker(
    file_ids: list[int],
    cancel_event: threading.Event,
    progress_lock: threading.Lock,
    progress_counter: list,
    total_files: int,
    progress_tracker,
) -> dict:
    """
    Worker 函式：在獨立 session 中對指定的 file_id 列表計算 SHA-256。

    Returns
    -------
    dict with keys: done, failed
    """
    from app.core.database import SyncSessionLocal

    done = 0
    failed = 0

    with SyncSessionLocal() as session:
        for file_id in file_ids:
            if cancel_event.is_set():
                break

            file_record: Optional[File] = session.get(File, file_id)
            if file_record is None:
                continue

            try:
                sha = _compute_sha256(file_record.path)
                file_record.sha256 = sha
                file_record.hash_state = "done"
                session.flush()
                session.commit()
                done += 1
                logger.debug("hash_stage: hashed %s → %s", file_record.path, sha[:12])

            except Exception as exc:
                session.rollback()
                logger.warning("hash_stage: error hashing %s — %s", file_record.path, exc)
                try:
                    file_record = session.get(File, file_id)
                    if file_record:
                        file_record.hash_state = "error"
                        _upsert_failure(session, file_record.asset_id, "hash", str(exc))
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
                            current, total_files, file_record.path if file_record else ""
                        )
                    except InterruptedError:
                        cancel_event.set()

    return {"done": done, "failed": failed}


# ─────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────

def run_hash_stage(
    session: Session,
    retry_failed: bool = False,
    progress_tracker=None,
    runners: Optional[int] = None,
) -> dict:
    """
    Execute the hash analysis stage.

    Parameters
    ----------
    session        : Active SQLAlchemy sync session (caller manages commit/rollback).
                     Used only for the batch-update steps (1-4); hashing workers
                     open their own sessions.
    retry_failed   : If True, reset files with `hash_state='error'` to 'pending'
                     so they are retried in this run.
    progress_tracker : Optional ProgressTracker-like object.
    runners        : Number of parallel worker threads.  Defaults to
                     settings.HASH_RUNNERS (config.py / env).

    Returns
    -------
    dict with keys: processed, done, not_needed, failed
    """
    if runners is None:
        runners = settings.HASH_RUNNERS
    runners = max(1, runners)

    stats = {"processed": 0, "done": 0, "not_needed": 0, "failed": 0}

    # ── Step 1: Optionally reset error files ──────────────────────────────────
    if retry_failed:
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
    session.commit()  # 提交批次 UPDATE，讓各 worker session 看到最新狀態

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
    pending_ids = [f.id for f in pending_files]

    if progress_tracker:
        progress_tracker.set_current_stage("hash", total=total_hash_files)

    logger.info(
        "hash_stage: %d file(s) to hash with %d runner(s)", total_hash_files, runners
    )

    if total_hash_files == 0:
        logger.info(
            "hash_stage complete — processed=%d done=%d not_needed=%d failed=%d",
            stats["processed"], stats["done"], stats["not_needed"], stats["failed"],
        )
        return stats

    # ── Step 6: Parallel hashing ──────────────────────────────────────────────
    cancel_event = threading.Event()
    # 若 progress_tracker 已取消，立即設旗
    if progress_tracker and progress_tracker.is_cancel_requested():
        cancel_event.set()

    progress_lock = threading.Lock()
    progress_counter = [0]  # mutable int container for cross-thread sharing

    chunks = _split_chunks(pending_ids, runners)
    actual_runners = len(chunks)
    logger.info("hash_stage: dispatching %d chunk(s) to %d thread(s)", actual_runners, actual_runners)

    with ThreadPoolExecutor(max_workers=actual_runners) as executor:
        futures = [
            executor.submit(
                _hash_worker,
                chunk,
                cancel_event,
                progress_lock,
                progress_counter,
                total_hash_files,
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
                logger.error("hash_stage: worker raised unexpected error: %s", exc, exc_info=True)

    # Worker 在獨立 session 中 commit，主 session 的 identity map 已過期。
    session.expire_all()

    logger.info(
        "hash_stage complete — processed=%d done=%d not_needed=%d failed=%d",
        stats["processed"],
        stats["done"],
        stats["not_needed"],
        stats["failed"],
    )
    return stats
