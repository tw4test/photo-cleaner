"""
Tests for the new scan_job service:
- T-1: new_only mode preserves analysis results and does not mark missing
- S-5: scan summary reports correct counts
- S-2: full mode detects modified and missing assets
"""
import os
import shutil
import tempfile
from pathlib import Path
from datetime import datetime
import pytest
from PIL import Image
from app.core.database import SyncSessionLocal, init_db
from app.models.schema import Asset, File, Feature, ClipFeature, VideoInfo, Root
from app.services.scan_job import run_scan_job


def _make_root(session, path: str) -> Root:
    """Helper: register a root path and flush."""
    root = Root(path=path, recursive=True, available=True)
    session.add(root)
    session.flush()
    return root


def _make_jpg(directory: str, name: str = "test.jpg") -> Path:
    p = Path(directory) / name
    img = Image.new("RGB", (80, 60), color=(100, 150, 200))
    img.save(p)
    return p


class TestScanNewOnly:
    """T-1: new_only mode."""

    def test_existing_asset_analysis_preserved_in_new_only(self, tmp_path):
        """
        T-1: In new_only mode, existing assets (even modified) keep their
        Feature / ClipFeature records and status unchanged.
        """
        # Create a photo file
        img_path = _make_jpg(str(tmp_path))
        with SyncSessionLocal() as session:
            root = _make_root(session, str(tmp_path))
            # First scan (full) → adds the asset
            run_scan_job(session, root_ids=[root.id], mode="full")
            session.flush()
            asset = session.query(Asset).first()
            assert asset is not None
            # Manually insert a Feature record (simulate basic analysis done)
            feat = Feature(
                asset_id=asset.id,
                phash="aabbccdd",
                blur_score=120.0,
                decode_status="ok",
                analyzed_at=datetime.utcnow(),
            )
            session.add(feat)
            # Also insert a ClipFeature
            clip = ClipFeature(
                asset_id=asset.id,
                model="ViT-B-16",
                pretrained="openai",
                dim=512,
                embedding=b"\x00" * 2048,
                zs={},
                analyzed_at=datetime.utcnow(),
            )
            session.add(clip)
            session.flush()
            # Modify the file on disk (size change)
            img_path.write_bytes(img_path.read_bytes() + b"\x00" * 100)
            # Second scan with new_only mode
            run_scan_job(session, root_ids=[root.id], mode="new_only")
            session.flush()
            # Reload feature record
            session.expire_all()
            updated_feat = session.query(Feature).filter(Feature.asset_id == asset.id).first()
            updated_clip = session.query(ClipFeature).filter(ClipFeature.asset_id == asset.id).first()
            updated_asset = session.get(Asset, asset.id)
            assert updated_asset is not None
            assert updated_feat is not None, "Feature record must be preserved in new_only mode"
            assert updated_feat.phash == "aabbccdd", "phash must not change in new_only mode"
            assert updated_clip is not None, "ClipFeature must be preserved in new_only mode"
            # NOTE: status stays 'unanalysed' until basic stage runs explicitly
            # but what matters is that analysis records are NOT cleared by new_only scan
            session.rollback()

    def test_missing_files_not_marked_in_new_only(self, tmp_path):
        """
        T-1: In new_only mode, files that disappear from disk are NOT marked missing.
        """
        img_path = _make_jpg(str(tmp_path), "photo_a.jpg")
        with SyncSessionLocal() as session:
            root = _make_root(session, str(tmp_path))
            run_scan_job(session, root_ids=[root.id], mode="full")
            session.flush()
            # Remove the file
            img_path.unlink()
            # new_only scan — must NOT mark missing
            run_scan_job(session, root_ids=[root.id], mode="new_only")
            session.flush()
            session.expire_all()
            asset = session.query(Asset).first()
            assert asset is not None
            assert asset.status != "missing", "new_only mode must not mark assets as missing"
            session.rollback()


class TestScanFull:
    """S-2: full mode detects modifications and missing assets."""

    def test_modified_file_clears_analysis(self, tmp_path):
        """
        S-2 / T-1: full scan of a modified file clears Feature/ClipFeature.
        """
        img_path = _make_jpg(str(tmp_path))
        with SyncSessionLocal() as session:
            root = _make_root(session, str(tmp_path))
            run_scan_job(session, root_ids=[root.id], mode="full")
            session.flush()
            asset = session.query(Asset).first()
            feat = Feature(
                asset_id=asset.id, phash="original",
                blur_score=80.0, decode_status="ok",
                analyzed_at=datetime.utcnow(),
            )
            session.add(feat)
            session.flush()
            # Physically change the file
            import time; time.sleep(0.05)
            img_path.write_bytes(img_path.read_bytes() + b"\x00" * 200)
            # Update the File row's mtime to match what's on disk
            # (scan_directory will pick up the new mtime; we need to make sure
            #  the existing DB row has the old mtime)
            file_row = session.query(File).filter(File.path == str(img_path)).first()
            file_row.mtime = 0.0  # force mismatch
            session.flush()
            summary = run_scan_job(session, root_ids=[root.id], mode="full")
            session.flush()
            assert summary.modified == 1
            session.expire_all()
            remaining_feat = session.query(Feature).filter(Feature.asset_id == asset.id).first()
            assert remaining_feat is None, "Feature must be cleared after modification"
            session.rollback()

    def test_missing_file_marked_missing(self, tmp_path):
        """S-2: full scan marks deleted files as missing."""
        img_path = _make_jpg(str(tmp_path))
        with SyncSessionLocal() as session:
            root = _make_root(session, str(tmp_path))
            run_scan_job(session, root_ids=[root.id], mode="full")
            session.flush()
            img_path.unlink()
            summary = run_scan_job(session, root_ids=[root.id], mode="full")
            session.flush()
            assert summary.missing == 1
            session.expire_all()
            asset = session.query(Asset).first()
            assert asset.status == "missing"
            session.rollback()


class TestScanSummary:
    """S-5: scan summary reports correct counts."""

    def test_summary_counts(self, tmp_path):
        img1 = _make_jpg(str(tmp_path), "a.jpg")
        img2 = _make_jpg(str(tmp_path), "b.jpg")
        with SyncSessionLocal() as session:
            root = _make_root(session, str(tmp_path))
            # First scan: 2 new assets
            s1 = run_scan_job(session, root_ids=[root.id], mode="full")
            session.flush()
            assert s1.added == 2
            assert s1.modified == 0
            assert s1.missing == 0
            # Delete one, keep one — second scan: 1 missing
            img1.unlink()
            s2 = run_scan_job(session, root_ids=[root.id], mode="full")
            session.flush()
            assert s2.added == 0
            assert s2.missing == 1
            assert s2.unchanged == 1
            session.rollback()


class TestScanOfflineRoot:
    """S-6: offline roots are marked unavailable but existing assets are untouched."""

    def test_offline_root_skipped(self, tmp_path):
        img = _make_jpg(str(tmp_path))
        with SyncSessionLocal() as session:
            root = _make_root(session, str(tmp_path))
            run_scan_job(session, root_ids=[root.id], mode="full")
            session.flush()
            root.path = "/nonexistent/path/xyz"
            session.flush()
            # Scan offline root
            run_scan_job(session, root_ids=[root.id], mode="full")
            session.flush()
            session.expire_all()
            root = session.query(Root).first()
            assert root.available is False
            # Asset still intact
            assert session.query(Asset).count() == 1
            session.rollback()
