"""
services/scan_job.py
====================
Pure DB-level scan job service.
Replaces the scanning portion of the old `run_sync_scan` in `api/scan.py`.
Does NOT read image content and does NOT call any feature extraction.
"""
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.models.schema import (
    AnalysisFailure,
    Asset,
    ClipFeature,
    Feature,
    File,
    Root,
    VideoInfo,
)
from app.services.pairing import pairing_engine
from app.services.scanner import scan_directory

logger = logging.getLogger(__name__)

# Concurrency lock to prevent simultaneous scans from colliding on unique asset paths
_SCAN_LOCK = threading.Lock()


@dataclass
class ScanSummary:
    """Counts of asset state changes produced by a single scan run."""
    added: int = 0       # net-new assets inserted into the DB
    modified: int = 0    # existing assets whose primary file changed on disk
    missing: int = 0     # assets previously on disk that are no longer found
    unchanged: int = 0   # assets confirmed still present with identical metadata


def _delete_analysis_data(session: Session, asset: Asset) -> None:
    """Delete all derived analysis records for *asset* so it can be re-analysed."""
    session.query(Feature).filter(Feature.asset_id == asset.id).delete(synchronize_session=False)
    session.query(ClipFeature).filter(ClipFeature.asset_id == asset.id).delete(synchronize_session=False)
    session.query(VideoInfo).filter(VideoInfo.asset_id == asset.id).delete(synchronize_session=False)
    session.query(AnalysisFailure).filter(AnalysisFailure.asset_id == asset.id).delete(synchronize_session=False)


def _upsert_file(
    session: Session,
    asset: Asset,
    path: str,
    size: int,
    mtime: float,
    fmt: str,
    role: str = "primary",
) -> File:
    """Return the existing File row for *path*, or create a new one."""
    file_row = session.query(File).filter(File.path == path).first()
    if file_row is None:
        file_row = File(
            asset_id=asset.id,
            path=path,
            size=size,
            mtime=mtime,
            format=fmt,
            role=role,
            hash_state="pending",
        )
        session.add(file_row)
    return file_row


def run_scan_job(
    session: Session,
    root_ids: Optional[list] = None,
    mode: str = "full",
    progress_tracker=None,
) -> ScanSummary:
    """
    Walk each requested root directory and synchronise its contents with the DB.
    v0.5：每個根目錄完成後即 commit。

    mode:
        'full'     – add new assets **and** detect modified/missing ones.
        'new_only' – add new assets only; skip existing paths entirely.
    """
    if mode not in ("full", "new_only"):
        raise ValueError(f"Unknown scan mode: {mode!r}. Expected 'full' or 'new_only'.")
    with _SCAN_LOCK:
        summary = ScanSummary()
        query = session.query(Root)
        if root_ids:
            query = query.filter(Root.id.in_(root_ids))
        roots: list[Root] = query.all()
        if not roots:
            logger.warning("run_scan_job: no roots found for root_ids=%s", root_ids)
            return summary
        for root in roots:
            logger.info("Scanning root id=%d path=%r mode=%s", root.id, root.path, mode)
            root_path = Path(root.path)
            if not root_path.exists() or not root_path.is_dir():
                logger.warning("Root id=%d path=%r is offline – marking unavailable.", root.id, root.path)
                root.available = False
                session.commit()
                continue
            root.available = True
            scanned_raw: list[dict] = list(
                scan_directory(
                    root_path=root.path,
                    recursive=root.recursive,
                    exclude_patterns=root.exclude_patterns or "",
                )
            )
            logger.debug("Root id=%d: %d files discovered on disk.", root.id, len(scanned_raw))
            paired_assets: list[dict] = pairing_engine.pair_files(scanned_raw)
            total_assets = len(paired_assets)
            if progress_tracker is not None:
                progress_tracker.set_discovered_total(total_assets)
            scanned_primary_paths: set[str] = set()
            existing_assets: dict[str, Asset] = {
                a.primary_path: a
                for a in session.query(Asset).filter(Asset.root_id == root.id).all()
            }
            existing_files: dict[str, File] = {
                f.path: f
                for f in session.query(File)
                .join(Asset)
                .filter(Asset.root_id == root.id)
                .all()
            }
            processed_count = 0
            for paired in paired_assets:
                primary_info: dict = paired["primary_file"]
                paired_infos: list[dict] = paired["paired_files"]
                media_type: str = paired["media_type"]
                size_total: int = paired["size_total"]
                primary_path: str = primary_info["path"]
                scanned_primary_paths.add(primary_path)
                if progress_tracker is not None and progress_tracker.is_cancel_requested():
                    logger.info("Scan cancelled by user at root id=%d.", root.id)
                    return summary
                existing_asset: Optional[Asset] = existing_assets.get(primary_path)
                if existing_asset is None:
                    existing_asset = session.query(Asset).filter(Asset.primary_path == primary_path).first()
                    if existing_asset is not None:
                        existing_assets[primary_path] = existing_asset
                        existing_asset.root_id = root.id
                if existing_asset is not None:
                    if existing_asset.status in ("deleted", "missing"):
                        existing_asset.status = "unanalysed"
                        _delete_analysis_data(session, existing_asset)
                        summary.added += 1
                    elif mode == "new_only":
                        summary.unchanged += 1
                    else:
                        primary_file_row: Optional[File] = existing_files.get(primary_path)
                        if primary_file_row is None:
                            primary_file_row = session.query(File).filter(File.path == primary_path).first()
                            if primary_file_row is not None:
                                existing_files[primary_path] = primary_file_row
                        if primary_file_row is not None and (
                            primary_file_row.size != primary_info["size"]
                            or primary_file_row.mtime != primary_info["mtime"]
                        ):
                            logger.debug(
                                "Asset id=%d primary_path=%r modified (size/mtime changed).",
                                existing_asset.id, primary_path,
                            )
                            _delete_analysis_data(session, existing_asset)
                            primary_file_row.size = primary_info["size"]
                            primary_file_row.mtime = primary_info["mtime"]
                            primary_file_row.hash_state = "pending"
                            primary_file_row.sha256 = None
                            existing_asset.size_total = size_total
                            existing_asset.status = "unanalysed"
                            summary.modified += 1
                        else:
                            summary.unchanged += 1
                else:
                    paired_paths = [pf["path"] for pf in paired_infos]
                    new_asset = Asset(
                        root_id=root.id,
                        media_type=media_type,
                        primary_path=primary_path,
                        size_total=size_total,
                        paired_paths=paired_paths,
                        status="unanalysed",
                    )
                    session.add(new_asset)
                    session.flush()
                    existing_assets[primary_path] = new_asset
                    _upsert_file(
                        session=session, asset=new_asset, path=primary_path,
                        size=primary_info["size"], mtime=primary_info["mtime"],
                        fmt=primary_info["ext"].lstrip(".").lower(), role="primary",
                    )
                    for pf_info in paired_infos:
                        _upsert_file(
                            session=session, asset=new_asset, path=pf_info["path"],
                            size=pf_info["size"], mtime=pf_info["mtime"],
                            fmt=pf_info["ext"].lstrip(".").lower(), role="pair",
                        )
                    summary.added += 1
                processed_count += 1
                if progress_tracker is not None:
                    progress_tracker.update_progress(
                        processed=processed_count,
                        current_file=primary_path,
                        new_assets=summary.added,
                    )
            if mode == "full":
                for asset_path, db_asset in existing_assets.items():
                    if asset_path not in scanned_primary_paths and db_asset.status not in ("deleted", "missing"):
                        logger.debug("Asset id=%d primary_path=%r no longer on disk – marking missing.",
                                     db_asset.id, asset_path)
                        db_asset.status = "missing"
                        summary.missing += 1
            root.last_scan_at = datetime.utcnow()
            # 每個根目錄掃完即 commit（在 _SCAN_LOCK 內）：
            # 1. 長時間掃描中途取消或當機時，已完成的根目錄不會整批回滾
            # 2. 兩個掃描同時進行時，第二個一定看得到第一個寫入的資產，不會撞 UNIQUE
            session.commit()
            logger.info(
                "Root id=%d scan complete: added=%d modified=%d missing=%d unchanged=%d",
                root.id, summary.added, summary.modified, summary.missing, summary.unchanged,
            )
    return summary
