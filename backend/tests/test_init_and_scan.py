import pytest
import os
import shutil
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy import text
from PIL import Image

from app.main import app
from app.core.database import init_db, SyncSessionLocal
from app.models.schema import Root, Asset, File, Feature
from app.services.scanner import scan_directory
from app.services.pairing import pairing_engine
from app.services.feature_pipeline import feature_pipeline

client = TestClient(app)

def test_health_check():
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["host"] == "127.0.0.1"
    assert data["gpu_mode"] == "mock_stub"

def test_database_init_and_tables():
    init_db()
    with SyncSessionLocal() as session:
        # Verify WAL mode
        result = session.execute(text("PRAGMA journal_mode;")).fetchone()
        assert result[0].lower() == "wal"

def test_scan_and_pairing_mock():
    # Create temporary directory with mock files
    temp_dir = tempfile.mkdtemp(prefix="photo_test_")
    try:
        # Create a sample JPG
        img_path = Path(temp_dir) / "IMG_0001.JPG"
        img = Image.new("RGB", (100, 100), color=(200, 100, 50))
        img.save(img_path)

        # Create a mock Live Photo video companion
        mov_path = Path(temp_dir) / "IMG_0001.MOV"
        with open(mov_path, "wb") as f:
            f.write(b"mock_video_bytes")

        # Create a mock RAW + JPG pair
        raw_path = Path(temp_dir) / "IMG_0002.CR3"
        with open(raw_path, "wb") as f:
            f.write(b"mock_cr3_bytes")
        jpg_pair_path = Path(temp_dir) / "IMG_0002.JPG"
        img.save(jpg_pair_path)

        # Run scanner
        scanned = list(scan_directory(temp_dir, recursive=True))
        assert len(scanned) == 4

        # Run pairing
        paired = pairing_engine.pair_files(scanned)
        assert len(paired) == 2  # IMG_0001 (Live Photo) and IMG_0002 (RAW+JPG)

        # Test feature extraction on primary photo
        feats = feature_pipeline.extract_features(str(img_path))
        assert feats["sha256"] is not None
        assert feats["blur_score"] is not None  # Mock fake blur
        assert feats["clip_embedding"] is not None  # Mock 512-dim embedding
        assert len(feats["clip_embedding"]) == 512 * 4  # 512 float32 bytes

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
