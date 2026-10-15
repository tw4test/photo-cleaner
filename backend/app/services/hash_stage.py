"""
hash_stage.py
─────────────
Analysis stage: SHA-256 hashing for duplicate-size detection.

1. Build a set of file sizes that appear more than once across *all* active
   PRIMARY-role files (i.e. assets not deleted/missing).
2. Files whose size is UNIQUE get `hash_state='not_needed'`.
3. Files whose size is DUPLICATED get SHA-256 computed; 'done' or 'error'.
4. Previously 'not_needed' files are reset to 'pending' if a same-size file appeared.
5. When `retry_failed=True`, 'error' files are also reset to 'pending'.
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


def _compute_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(DEFAULT_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _upsert_failure(session: Session, asset_id: int, stage: str, msg: str) -> None:
    session.query(AnalysisFailure).filter(
        AnalysisFailure.asset_id == asset_id,
        AnalysisFailure.stage == stage,
    ).delete()
    session.add(AnalysisFailure(asset_id=asset_id, stage=stage, error_msg=msg, occurred_at=datetime.utcnow()))


def _duplicate_sizes_subquery(session: Session):
    return (
        session.query(File.size)
        .join(Asset, Asset.id == File.asset_id)
        .filter(File.role == "primary", Asset.status.notin_(["deleted", "missing"]))
        .group_by(File.size)
        .having(func.count(File.id) > 1)
        .subquery()
    )


def _split_chunks(items: list, n: int) -> list[list]:
    if not items:
        return []
    n = min(n, len(items))
    size = math.ceil(len(items) / n)
    return [items[i: i + size] for i in range(0, len(items), size)]


def _hash_worker(file_ids, cancel_event, progress_lock, progress_counter, total_files, progress_tracker) -> dict:
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
            path = file_record.path
            try:
                sha = _compute_sha256(path)
                file_record.sha256 = sha
                file_record.hash_state = "done"
                session.query(AnalysisFailure).filter(
                    AnalysisFailure.asset_id == file_record.asset_id, AnalysisFailure.stage == "hash"
                ).delete()
                session.commit()
                done += 1
            except Exception as exc:
                session.rollback()
                logger.warning("hash_stage: error hashing %s — %s", path, exc)
                try:
                    file_record = session.get(File, file_id)
                    if file_record:
                        file_record.hash_state = "error"
                        _upsert_failure(session, file_record.asset_id, "hash", str(exc))
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
                        progress_tracker.update_analyze_progress(current, total_files, path)
                    except InterruptedError:
                        cancel_event.set()
    return {"done": done, "failed": failed}


def run_hash_stage(
    session: Session,
    retry_failed: bool = False,
    progress_tracker=None,
    runners: Optional[int] = None,
) -> dict:
    """Execute the hash analysis stage. Returns {processed, done, not_needed, failed}."""
    if runners is None:
        runners = settings.HASH_RUNNERS
    runners = max(1, runners)
    stats = {"processed": 0, "done": 0, "not_needed": 0, "failed": 0}
    if retry_failed:
        error_file_ids = [
            f.id for f in session.query(File.id)
            .join(Asset, Asset.id == File.asset_id)
            .filter(File.role == "primary", File.hash_state == "error",
                    Asset.status.notin_(["deleted", "missing"]))
            .all()
        ]
        if error_file_ids:
            session.query(File).filter(File.id.in_(error_file_ids)).update(
                {"hash_state": "pending"}, synchronize_session="fetch"
            )
    dup_sizes_sub = _duplicate_sizes_subquery(session)
    not_needed_to_reset = (
        session.query(File)
        .join(Asset, Asset.id == File.asset_id)
        .filter(File.role == "primary", File.hash_state == "not_needed",
                Asset.status.notin_(["deleted", "missing"]),
                File.size.in_(select(dup_sizes_sub)))
        .all()
    )
    if not_needed_to_reset:
        for f in not_needed_to_reset:
            f.hash_state = "pending"
        session.flush()
    unique_file_ids = [
        f.id for f in session.query(File.id)
        .join(Asset, Asset.id == File.asset_id)
        .filter(File.role == "primary", File.hash_state == "pending",
                Asset.status.notin_(["deleted", "missing"]),
                File.size.notin_(select(dup_sizes_sub)))
        .all()
    ]
    if unique_file_ids:
        session.query(File).filter(File.id.in_(unique_file_ids)).update(
            {"hash_state": "not_needed"}, synchronize_session="fetch"
        )
    stats["not_needed"] += len(unique_file_ids)
    stats["processed"] += len(unique_file_ids)
    session.flush()
    session.commit()
    pending_files = (
        session.query(File)
        .join(Asset, Asset.id == File.asset_id)
        .filter(File.role == "primary", File.hash_state == "pending",
                Asset.status.notin_(["deleted", "missing"]),
                File.size.in_(select(dup_sizes_sub)))
        .all()
    )
    pending_ids = [f.id for f in pending_files]
    total_hash_files = len(pending_ids)
    if progress_tracker:
        progress_tracker.set_current_stage("hash", total=total_hash_files, runners=runners)
    logger.info("hash_stage: %d file(s) to hash with %d runner(s)", total_hash_files, runners)
    if total_hash_files == 0:
        return stats
    cancel_event = threading.Event()
    if progress_tracker and progress_tracker.is_cancel_requested():
        cancel_event.set()
    progress_lock = threading.Lock()
    progress_counter = [0]
    chunks = _split_chunks(pending_ids, runners)
    with ThreadPoolExecutor(max_workers=len(chunks)) as executor:
        futures = [
            executor.submit(_hash_worker, chunk, cancel_event, progress_lock, progress_counter,
                            total_hash_files, progress_tracker)
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
    session.expire_all()
    if cancel_event.is_set() and progress_tracker:
        raise InterruptedError("使用者已取消分析任務")
    logger.info("hash_stage complete — %s", stats)
    return stats
