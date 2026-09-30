"""
services/scan_job.py
====================
Pure DB-level scan job service.

Replaces the scanning portion of the old `run_sync_scan` in `api/scan.py`.
Does NOT read image content and does NOT call any feature extraction.
"""

import logging
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


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------

@dataclass
class ScanSummary:
    """Counts of asset state changes produced by a single scan run."""
    added: int = 0       # net-new assets inserted into the DB
    modified: int = 0    # existing assets whose primary file changed on disk
    missing: int = 0     # assets previously on disk that are no longer found
    unchanged: int = 0   # assets confirmed still present with identical metadata


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_scan_job(
    session: Session,
    root_ids: Optional[list] = None,
    mode: str = "full",
    progress_tracker=None,
) -> ScanSummary:
    """
    Walk each requested root directory and synchronise its contents with the DB.

    Parameters
    ----------
    session:
        Active SQLAlchemy session.  The caller is responsible for committing or
        rolling back after this function returns.
    root_ids:
        IDs of ``Root`` rows to scan.  Pass ``None`` (or an empty list) to scan
        all roots.
    mode:
        ``'full'``     – add new assets **and** detect modified/missing ones.
        ``'new_only'`` – add new assets only; skip existing paths entirely.
    progress_tracker:
        Optional :class:`~app.core.progress.ProgressTracker` instance.
        ``update_progress`` is called after every asset is processed.

    Returns
    -------
    ScanSummary
        Aggregate counts of what changed during this run.
    """
    if mode not in ("full", "new_only"):
        raise ValueError(f"Unknown scan mode: {mode!r}. Expected 'full' or 'new_only'.")

    summary = ScanSummary()

    # ------------------------------------------------------------------
    # 1. Resolve which roots to scan
    # ------------------------------------------------------------------
    query = session.query(Root)
    if root_ids:
        query = query.filter(Root.id.in_(root_ids))
    roots: list[Root] = query.all()

    if not roots:
        logger.warning("run_scan_job: no roots found for root_ids=%s", root_ids)
        return summary

    # ------------------------------------------------------------------
    # 2. Announce discovery phase to the progress tracker (if present)
    # ------------------------------------------------------------------
    # (progress_tracker.start_scan() is called by the background task in scan.py / jobs.py)
    for root in roots:
        logger.info("Scanning root id=%d path=%r mode=%s", root.id, root.path, mode)

        # ------------------------------------------------------------------
        # 2a. Offline-root check: mark unavailable and skip
        # ------------------------------------------------------------------
        root_path = Path(root.path)
        if not root_path.exists() or not root_path.is_dir():
            logger.warning("Root id=%d path=%r is offline – marking unavailable.", root.id, root.path)
            root.available = False
            # Persist the unavailability flag but leave existing assets untouched.
            session.flush()
            continue

        # Root is reachable – ensure the available flag is set to True.
        root.available = True

        # ------------------------------------------------------------------
        # 3. Discover files on disk
        # ------------------------------------------------------------------
        scanned_raw: list[dict] = list(
            scan_directory(
                root_path=root.path,
                recursive=root.recursive,
                exclude_patterns=root.exclude_patterns or "",
            )
        )
        logger.debug("Root id=%d: %d files discovered on disk.", root.id, len(scanned_raw))

        # ------------------------------------------------------------------
        # 4. Pair files (RAW+JPG, Live Photo, sidecar)
        # ------------------------------------------------------------------
        paired_assets: list[dict] = pairing_engine.pair_files(scanned_raw)
        total_assets = len(paired_assets)

        if progress_tracker is not None:
            progress_tracker.set_discovered_total(total_assets)

        # Collect the primary paths of every asset seen on disk (used later
        # for missing-asset detection in full mode).
        scanned_primary_paths: set[str] = set()

        # ------------------------------------------------------------------
        # 5. Build an in-memory lookup of existing DB assets for this root
        #    keyed by primary_path to avoid repeated per-asset DB queries.
        # ------------------------------------------------------------------
        existing_assets: dict[str, Asset] = {
            a.primary_path: a
            for a in session.query(Asset).filter(Asset.root_id == root.id).all()
        }

        # Track existing File rows by path for quick lookup
        existing_files: dict[str, File] = {
            f.path: f
            for f in session.query(File)
            .join(Asset)
            .filter(Asset.root_id == root.id)
            .all()
        }

        # ------------------------------------------------------------------
        # 6. Process each paired asset
        # ------------------------------------------------------------------
        processed_count = 0

        for paired in paired_assets:
            primary_info: dict = paired["primary_file"]
            paired_infos: list[dict] = paired["paired_files"]
            media_type: str = paired["media_type"]
            size_total: int = paired["size_total"]

            primary_path: str = primary_info["path"]
            scanned_primary_paths.add(primary_path)

            # Check for cancellation if the tracker supports it
            if progress_tracker is not None and progress_tracker.is_cancel_requested():
                logger.info("Scan cancelled by user at root id=%d.", root.id)
                return summary

            existing_asset: Optional[Asset] = existing_assets.get(primary_path)

            if existing_asset is not None:
                # --------------------------------------------------------
                # Path already known – decide what to do
                # --------------------------------------------------------
                if mode == "new_only":
                    # Skip entirely in new_only mode.
                    summary.unchanged += 1
                else:
                    # full mode: check whether the primary file has changed.
                    primary_file_row: Optional[File] = existing_files.get(primary_path)

                    if primary_file_row is not None and (
                        primary_file_row.size != primary_info["size"]
                        or primary_file_row.mtime != primary_info["mtime"]
                    ):
                        # File has been modified on disk – invalidate analysis data.
                        logger.debug(
                            "Asset id=%d primary_path=%r modified (size/mtime changed).",
                            existing_asset.id,
                            primary_path,
                        )
                        _delete_analysis_data(session, existing_asset)

                        # Update primary file metadata
                        primary_file_row.size = primary_info["size"]
                        primary_file_row.mtime = primary_info["mtime"]
                        primary_file_row.hash_state = "pending"
                        primary_file_row.sha256 = None  # hash is now stale

                        # Update asset totals & status
                        existing_asset.size_total = size_total
                        existing_asset.status = "unanalysed"

                        summary.modified += 1
                    else:
                        summary.unchanged += 1
            else:
                # --------------------------------------------------------
                # Brand-new asset – insert into DB
                # --------------------------------------------------------
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
                session.flush()  # populate new_asset.id

                # Insert File rows for primary and any paired files
                _upsert_file(
                    session=session,
                    asset=new_asset,
                    path=primary_path,
                    size=primary_info["size"],
                    mtime=primary_info["mtime"],
                    fmt=primary_info["ext"].lstrip(".").lower(),
                    role="primary",
                )
                for pf_info in paired_infos:
                    _upsert_file(
                        session=session,
                        asset=new_asset,
                        path=pf_info["path"],
                        size=pf_info["size"],
                        mtime=pf_info["mtime"],
                        fmt=pf_info["ext"].lstrip(".").lower(),
                        role="pair",
                    )

                summary.added += 1

            # ----------------------------------------------------------------
            # Progress reporting
            # ----------------------------------------------------------------
            processed_count += 1
            if progress_tracker is not None:
                progress_tracker.update_progress(
                    processed=processed_count,
                    current_file=primary_path,
                    new_assets=summary.added,
                )

        # ------------------------------------------------------------------
        # 7. Missing-asset detection (full mode only)
        # ------------------------------------------------------------------
        if mode == "full":
            # Find DB assets for this root whose primary_path is no longer on disk
            # and that haven't already been marked deleted or missing.
            for asset_path, db_asset in existing_assets.items():
                if asset_path not in scanned_primary_paths and db_asset.status not in (
                    "deleted",
                    "missing",
                ):
                    logger.debug(
                        "Asset id=%d primary_path=%r no longer on disk – marking missing.",
                        db_asset.id,
                        asset_path,
                    )
                    db_asset.status = "missing"
                    summary.missing += 1

        # ------------------------------------------------------------------
        # 8. Update root metadata
        # ------------------------------------------------------------------
        root.last_scan_at = datetime.utcnow()
        session.flush()

        logger.info(
            "Root id=%d scan complete: added=%d modified=%d missing=%d unchanged=%d",
            root.id,
            summary.added,
            summary.modified,
            summary.missing,
            summary.unchanged,
        )

    # ------------------------------------------------------------------
    # 9. Final progress notification
    # ------------------------------------------------------------------
    if progress_tracker is not None:
        progress_tracker.complete_scan(
            summary={
                "added": summary.added,
                "modified": summary.modified,
                "missing": summary.missing,
                "unchanged": summary.unchanged,
            }
        )

    return summary
