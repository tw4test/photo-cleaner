"""
Comprehensive tests for Data Lifecycle, Invariants, and Concurrency safety:
1. test_path_readd_lifecycle: Add root -> Scan -> Delete root -> Re-add -> Re-scan.
   Verifies stats remain clean and consistent, and no duplicate / collision errors occur.
2. test_stats_consistency_invariants: Verifies single-source-of-truth invariants
   (Total = Photo + Raw + Video; Stage Total = Pending + Done + Not Needed + Failed).
3. test_rules_re_evaluation_preserves_queued: Verifies user-queued items are not wiped
   on rules re-evaluation.
4. test_concurrent_same_path_scan: Verifies concurrent scans of the same root
   do not throw sqlite3 IntegrityError or corrupt asset counts.
5. test_semantic_b_root_deletion_preserves_disk: Verifies that removing a root path
   only affects the DB and never removes physical files on disk.
"""

import os
import shutil
import tempfile
import threading
from pathlib import Path
from datetime import datetime

import pytest
from PIL import Image

from app.core.database import SyncSessionLocal, init_db
from app.models.schema import Asset, File, Feature, ClipFeature, VideoInfo, Root, Candidate
from app.services.scan_job import run_scan_job
from app.services.stats_service import get_dashboard_stats, get_analysis_stage_stats
from app.services.rules_engine import rules_engine


def _make_jpg(directory: str, name: str = "test.jpg") -> Path:
    p = Path(directory) / name
    img = Image.new("RGB", (64, 64), color=(120, 150, 180))
    img.save(p)
    return p


def test_path_readd_lifecycle(tmp_path):
    """
    Test adding a root, scanning it, deleting the root from DB,
    re-adding it, and re-scanning.
    """
    p1 = _make_jpg(str(tmp_path), "photo1.jpg")
    p2 = _make_jpg(str(tmp_path), "photo2.jpg")

    with SyncSessionLocal() as session:
        # 1. Add root
        root = Root(path=str(tmp_path), recursive=True, available=True)
        session.add(root)
        session.commit()
        root_id = root.id

        # 2. Scan
        summary = run_scan_job(session, root_ids=[root_id], mode="full")
        session.commit()
        assert summary.added == 2

        stats1 = get_dashboard_stats(session)
        assert stats1["assets"]["total"] == 2
        assert stats1["assets"]["photo_count"] == 2

        # 3. Simulate root deletion (Semantic B: DB delete only)
        root_to_del = session.query(Root).filter(Root.id == root_id).first()
        session.delete(root_to_del)
        session.commit()

        # Files on disk must remain intact!
        assert p1.exists()
        assert p2.exists()

        # Assets should be cascade-deleted or removed with root
        assets_count = session.query(Asset).count()
        assert assets_count == 0

        # Stats after root deletion must be 0
        stats2 = get_dashboard_stats(session)
        assert stats2["assets"]["total"] == 0

        # 4. Re-add the same root path
        new_root = Root(path=str(tmp_path), recursive=True, available=True)
        session.add(new_root)
        session.commit()
        new_root_id = new_root.id

        # 5. Re-scan the root
        summary2 = run_scan_job(session, root_ids=[new_root_id], mode="full")
        session.commit()
        assert summary2.added == 2

        # 6. Verify stats are consistent and single source of truth holds
        stats3 = get_dashboard_stats(session)
        assert stats3["assets"]["total"] == 2
        assert stats3["assets"]["photo_count"] == 2


def test_stats_consistency_invariants(tmp_path):
    """
    Test Single Source of Truth invariants:
    - total_assets == photos + raw_photos + videos
    - stage total == pending + done + not_needed + failed
    """
    _make_jpg(str(tmp_path), "img1.jpg")
    _make_jpg(str(tmp_path), "img2.jpg")

    with SyncSessionLocal() as session:
        root = Root(path=str(tmp_path), recursive=True, available=True)
        session.add(root)
        session.commit()

        run_scan_job(session, root_ids=[root.id], mode="full")
        session.commit()

        # Check dashboard invariant
        d_stats = get_dashboard_stats(session)
        assert d_stats["assets"]["total"] == d_stats["assets"]["photo_count"] + d_stats["assets"]["raw_count"] + d_stats["assets"]["video_count"]

        # Check analysis stage stats invariant
        a_stats = get_analysis_stage_stats(session)
        for stage_name, stage_data in a_stats.items():
            total = stage_data["total"]
            parts = (
                stage_data["pending"]
                + stage_data["done"]
                + stage_data.get("not_needed", 0)
                + stage_data.get("stale", 0)
                + stage_data["failed"]
            )
            assert total == parts, f"Stage {stage_name} invariant failed: total={total} vs sum={parts}"


def test_rules_re_evaluation_preserves_queued(tmp_path):
    """
    Test that re-evaluating rules does NOT wipe user queued or kept candidate decisions.
    """
    _make_jpg(str(tmp_path), "blurry1.jpg")

    with SyncSessionLocal() as session:
        root = Root(path=str(tmp_path), recursive=True, available=True)
        session.add(root)
        session.commit()

        run_scan_job(session, root_ids=[root.id], mode="full")
        session.commit()

        asset = session.query(Asset).first()
        assert asset is not None

        # Add a low blur score feature to trigger candidate rule
        feat = Feature(
            asset_id=asset.id,
            blur_score=10.0,  # Below default blur threshold
            brightness=50.0,
            decode_status="ok",
        )
        session.add(feat)
        session.commit()

        # Run first evaluation
        rules_engine.evaluate_all_rules(session)

        cand = session.query(Candidate).filter(Candidate.asset_id == asset.id).first()
        assert cand is not None

        # User queues this candidate for deletion
        cand.state = "queued"
        asset.status = "queued"
        session.commit()

        # Re-evaluate rules (e.g. user triggers rules scan again)
        rules_engine.evaluate_all_rules(session)

        # Asset and Candidate MUST still be queued!
        refreshed_cand = session.query(Candidate).filter(Candidate.asset_id == asset.id).first()
        assert refreshed_cand is not None
        assert refreshed_cand.state == "queued"
        assert asset.status == "queued"


def test_concurrent_same_path_scan(tmp_path):
    """
    Test that two threads scanning the exact same root directory concurrently
    do not trigger UNIQUE constraint violations (assets.primary_path or files.path).
    """
    for i in range(5):
        _make_jpg(str(tmp_path), f"photo_{i}.jpg")

    with SyncSessionLocal() as session:
        root = Root(path=str(tmp_path), recursive=True, available=True)
        session.add(root)
        session.commit()
        root_id = root.id

    errors = []

    def scan_worker():
        try:
            with SyncSessionLocal() as s:
                run_scan_job(s, root_ids=[root_id], mode="full")
                s.commit()
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=scan_worker)
    t2 = threading.Thread(target=scan_worker)

    t1.start()
    t2.start()

    t1.join()
    t2.join()

    assert len(errors) == 0, f"Concurrent scan errors: {errors}"

    with SyncSessionLocal() as session:
        assets = session.query(Asset).filter(Asset.root_id == root_id).all()
        assert len(assets) == 5
