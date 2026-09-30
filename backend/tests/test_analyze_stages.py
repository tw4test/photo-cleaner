"""
Tests for the analysis stages:
- T-2: hash stage does not read image content
- T-3: clip stage does not modify blur/pHash in features
- T-9: not_needed hash_state reset when same-size file added
- T-7: rebuild_thumbs only regenerates missing thumbnails
- T-8: retry_failed re-attempts failed items
"""

import hashlib
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from PIL import Image

from app.core.database import SyncSessionLocal, init_db
from app.models.schema import (
    Asset, File, Feature, ClipFeature, VideoInfo, Root, AnalysisFailure
)
from app.services.scan_job import run_scan_job
from app.services.hash_stage import run_hash_stage
from app.services.basic_stage import run_basic_stage, run_rebuild_thumbs_only
from app.services.clip_stage import run_clip_stage
from app.services.video_stage import run_video_stage


def _make_root(session, path: str) -> Root:
    root = Root(path=path, recursive=True, available=True)
    session.add(root)
    session.flush()
    return root


def _make_jpg(directory: str, name: str, size_bytes: int = 0) -> Path:
    p = Path(directory) / name
    img = Image.new("RGB", (40, 40), color=(50, 50, 50))
    img.save(p)
    if size_bytes:
        p.write_bytes(p.read_bytes() + b"\x00" * size_bytes)
    return p


def _add_asset_with_file(session, root, path: str, size: int = 1000) -> tuple:
    asset = Asset(
        root_id=root.id,
        media_type="photo",
        primary_path=path,
        size_total=size,
        status="unanalysed",
    )
    session.add(asset)
    session.flush()
    f = File(
        asset_id=asset.id,
        path=path,
        size=size,
        mtime=1000.0,
        format="jpg",
        role="primary",
        hash_state="pending",
    )
    session.add(f)
    session.flush()
    return asset, f


class TestHashStage:
    """T-2: hash stage must not read image content."""

    def test_hash_stage_does_not_open_images(self, tmp_path):
        """
        T-2: When running hash stage, PIL.Image.open must never be called.
        """
        img = _make_jpg(str(tmp_path), "img.jpg")
        # Create second file with same size to trigger hash computation
        img2 = _make_jpg(str(tmp_path), "img2.jpg")

        with SyncSessionLocal() as session:
            root = _make_root(session, str(tmp_path))
            run_scan_job(session, root_ids=[root.id], mode="full")
            session.flush()

            # Force same size on both files
            files = session.query(File).filter(File.role == "primary").all()
            for f in files:
                f.size = 5000
                f.hash_state = "pending"
            session.flush()

            # Patch PIL Image.open to detect if it's called
            with patch("PIL.Image.open") as mock_open:
                run_hash_stage(session, retry_failed=False)
                session.flush()

            mock_open.assert_not_called(), "hash stage must NOT open images"
            session.rollback()

    def test_unique_size_marked_not_needed(self, tmp_path):
        """A-6: Files with unique size should be marked not_needed."""
        img = _make_jpg(str(tmp_path), "unique.jpg")

        with SyncSessionLocal() as session:
            root = _make_root(session, str(tmp_path))
            run_scan_job(session, root_ids=[root.id], mode="full")
            session.flush()

            result = run_hash_stage(session)
            session.flush()

            f = session.query(File).filter(File.role == "primary").first()
            assert f.hash_state == "not_needed"
            assert result["not_needed"] >= 1
            session.rollback()

    def test_t9_not_needed_reset_when_same_size_added(self, tmp_path):
        """
        T-9: A file with hash_state='not_needed' is reset to 'pending' and hashed
        when another file with the same size is later added.
        """
        img1 = _make_jpg(str(tmp_path), "solo.jpg")

        with SyncSessionLocal() as session:
            root = _make_root(session, str(tmp_path))
            run_scan_job(session, root_ids=[root.id], mode="full")
            session.flush()

            # First hash run → should mark as not_needed (unique size)
            run_hash_stage(session)
            session.flush()

            f1 = session.query(File).filter(File.path == str(img1)).first()
            assert f1.hash_state == "not_needed"

            # Now add a second file with the same size
            real_size = f1.size
            img2 = _make_jpg(str(tmp_path), "dup.jpg")
            # Create second asset manually with same size
            asset2 = Asset(
                root_id=root.id,
                media_type="photo",
                primary_path=str(img2),
                size_total=real_size,
                status="unanalysed",
            )
            session.add(asset2)
            session.flush()
            f2 = File(
                asset_id=asset2.id,
                path=str(img2),
                size=real_size,
                mtime=img2.stat().st_mtime,
                format="jpg",
                role="primary",
                hash_state="pending",
            )
            session.add(f2)
            session.flush()

            # Second hash run → not_needed file should be reset and hashed
            result = run_hash_stage(session)
            session.flush()

            session.expire_all()
            f1_updated = session.query(File).filter(File.path == str(img1)).first()
            assert f1_updated.hash_state == "done", \
                "T-9: File must be re-hashed when another file with same size is added"
            assert f1_updated.sha256 is not None
            session.rollback()


class TestBasicStage:
    """T-3: clip stage does not modify basic features."""

    def test_clip_stage_does_not_modify_basic_features(self, tmp_path):
        """
        T-3: Running clip stage alone must not change blur_score, brightness, phash.
        """
        img = _make_jpg(str(tmp_path), "photo.jpg")

        with SyncSessionLocal() as session:
            root = _make_root(session, str(tmp_path))
            run_scan_job(session, root_ids=[root.id], mode="full")
            session.flush()

            # Run basic stage first
            run_basic_stage(session)
            session.flush()

            asset = session.query(Asset).first()
            feat_before = session.query(Feature).filter(Feature.asset_id == asset.id).first()
            blur_before = feat_before.blur_score
            phash_before = feat_before.phash

            # Now run clip stage only
            run_clip_stage(session, device="cpu")
            session.flush()

            session.expire_all()
            feat_after = session.query(Feature).filter(Feature.asset_id == asset.id).first()

            assert feat_after is not None
            assert feat_after.blur_score == blur_before, "T-3: blur_score must not change after clip stage"
            assert feat_after.phash == phash_before, "T-3: phash must not change after clip stage"
            session.rollback()

    def test_retry_failed_re_runs_basic(self, tmp_path):
        """
        T-8: retry_failed=True causes failed assets to be re-processed.
        """
        img = _make_jpg(str(tmp_path), "photo.jpg")

        with SyncSessionLocal() as session:
            root = _make_root(session, str(tmp_path))
            run_scan_job(session, root_ids=[root.id], mode="full")
            session.flush()

            asset = session.query(Asset).first()

            # Create a fake thumbnail so _needs_processing returns False (thumbnail present)
            import hashlib
            from app.core.config import settings as cfg
            sha = hashlib.sha256(asset.primary_path.encode()).hexdigest()
            thumb_dir = cfg.THUMBS_CACHE_DIR / sha[:2]
            thumb_dir.mkdir(parents=True, exist_ok=True)
            (thumb_dir / f"{sha}.jpg").write_bytes(b"fake_thumb")

            # Manually set a failed Feature record
            feat = Feature(
                asset_id=asset.id,
                decode_status="error",
                analyzed_at=datetime.utcnow(),
            )
            session.add(feat)
            fail = AnalysisFailure(
                asset_id=asset.id,
                stage="basic",
                error_msg="decode failed",
                occurred_at=datetime.utcnow(),
            )
            session.add(fail)
            session.flush()

            # retry_failed=False → should skip (thumbnail exists, decode_status=error but not retrying)
            result_no_retry = run_basic_stage(session, retry_failed=False)
            assert result_no_retry["processed"] == 0

            # retry_failed=True → should re-process
            result_retry = run_basic_stage(session, retry_failed=True)
            assert result_retry["processed"] >= 1

            # Cleanup fake thumb
            (thumb_dir / f"{sha}.jpg").unlink(missing_ok=True)
            session.rollback()


class TestRebuildThumbs:
    """T-7: rebuild_thumbs only regenerates missing thumbnails."""

    def test_rebuild_thumbs_only_missing(self, tmp_path):
        """
        T-7: After deleting thumbs, rebuild_thumbs regenerates them
        without changing blur_score or phash.
        """
        img = _make_jpg(str(tmp_path), "photo.jpg")

        with SyncSessionLocal() as session:
            root = _make_root(session, str(tmp_path))
            run_scan_job(session, root_ids=[root.id], mode="full")
            session.flush()

            run_basic_stage(session)
            session.flush()

            asset = session.query(Asset).first()
            feat = session.query(Feature).filter(Feature.asset_id == asset.id).first()
            blur_original = feat.blur_score
            phash_original = feat.phash

            # Delete the thumbnail
            from app.core.config import settings as cfg
            sha = hashlib.sha256(asset.primary_path.encode()).hexdigest()
            thumb_path = cfg.THUMBS_CACHE_DIR / sha[:2] / f"{sha}.jpg"
            if thumb_path.exists():
                thumb_path.unlink()

            # Run rebuild_thumbs_only
            result = run_rebuild_thumbs_only(session)
            assert result["done"] >= 1

            # Feature must be unchanged
            session.expire_all()
            feat_after = session.query(Feature).filter(Feature.asset_id == asset.id).first()
            assert feat_after.blur_score == blur_original, "T-7: blur_score must not change"
            assert feat_after.phash == phash_original, "T-7: phash must not change"
            session.rollback()


class TestClipModelTracking:
    """T-5: CLIP model change marks old vectors stale."""

    def test_stale_clip_detected_after_model_change(self, tmp_path):
        """
        T-5: After running clip with model A, switching to model B
        should make the status API report stale records.
        """
        img = _make_jpg(str(tmp_path), "photo.jpg")

        with SyncSessionLocal() as session:
            root = _make_root(session, str(tmp_path))
            run_scan_job(session, root_ids=[root.id], mode="full")
            session.flush()

            asset = session.query(Asset).first()

            # Manually insert a ClipFeature with old model
            old_clip = ClipFeature(
                asset_id=asset.id,
                model="ViT-B-32",       # old model
                pretrained="openai",
                dim=512,
                embedding=b"\x00" * 2048,
                zs={},
                analyzed_at=datetime.utcnow(),
            )
            session.add(old_clip)
            session.flush()

            # Now run clip_stage with new model (ViT-B-16 from DEFAULT_SETTINGS)
            result = run_clip_stage(session, device="cpu")
            session.flush()

            assert result["stale_found"] >= 1, "Stale CLIP vector must be detected"

            session.expire_all()
            updated_clip = session.query(ClipFeature).filter(
                ClipFeature.asset_id == asset.id
            ).first()
            assert updated_clip.model == "ViT-B-16", "Model must be updated to current setting"
            session.rollback()
