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
from app.models.schema import Root, Asset, File, Feature, Candidate
from app.services.scanner import scan_directory
from app.services.pairing import pairing_engine
from app.services.feature_pipeline import feature_pipeline
from app.api.scan import run_sync_scan   # backward-compat re-export

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

        # Test feature extraction on primary photo (via backward-compat extract_features)
        feats = feature_pipeline.extract_features(str(img_path))
        assert feats["sha256"] is not None
        assert feats["blur_score"] is not None  # Mock fake blur
        # clip_embedding is still returned by extract_features() for backward-compat
        assert feats["clip_embedding"] is not None  # Mock 512-dim embedding
        assert len(feats["clip_embedding"]) == 512 * 4  # 512 float32 bytes

        # Verify new split APIs work independently
        basic = feature_pipeline.extract_basic(str(img_path))
        assert basic["phash"] is not None
        assert "clip_embedding" not in basic, "extract_basic must not return CLIP embedding"

        clip = feature_pipeline.extract_clip(str(img_path))
        assert clip["embedding"] is not None
        assert "phash" not in clip, "extract_clip must not return phash"

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

def test_duplicate_and_nested_path_handling():
    temp_parent = tempfile.mkdtemp(prefix="photo_root_parent_")
    temp_child = os.path.join(temp_parent, "child_folder")
    os.makedirs(temp_child, exist_ok=True)

    try:
        # 1. 加入合法目錄
        res1 = client.post("/api/roots", json={"path": temp_parent, "recursive": True})
        assert res1.status_code == 200
        root_id = res1.json()["id"]

        # 2. 嘗試重複加入相同目錄 (應被拒絕)
        res_dup = client.post("/api/roots", json={"path": temp_parent})
        assert res_dup.status_code == 400
        assert "無法重複加入" in res_dup.json()["detail"] or "已在掃描清單" in res_dup.json()["detail"]

        # 3. 嘗試加入已被父目錄遞迴涵蓋之子目錄 (應被拒絕)
        res_child = client.post("/api/roots", json={"path": temp_child})
        assert res_child.status_code == 400
        assert "涵蓋" in res_child.json()["detail"]

        # 清理
        client.delete(f"/api/roots/{root_id}")
    finally:
        shutil.rmtree(temp_parent, ignore_errors=True)

def test_repeated_scan_idempotency_and_candidate_count():
    temp_dir = tempfile.mkdtemp(prefix="photo_repeat_scan_")
    try:
        # 建立 3 張假相片
        for i in range(3):
            img_path = Path(temp_dir) / f"photo_{i}.jpg"
            img = Image.new("RGB", (60, 60), color=(i * 40, i * 40, i * 40))
            img.save(img_path)

        # 註冊目錄
        res = client.post("/api/roots", json={"path": temp_dir})
        assert res.status_code == 200
        root_id = res.json()["id"]

        # 第一次掃描
        run_sync_scan(root_id)
        status1 = client.get("/api/scan/status").json()
        assert status1["stage"] == "completed"
        assert status1["new_assets_count"] == 3
        # 掃描階段不執行評估，候選數為 0（評估已拆分為獨立的 /api/jobs/evaluate）
        assert status1["new_candidates_count"] == 0

        with SyncSessionLocal() as session:
            asset_count_1 = session.query(Asset).count()
            assert asset_count_1 == 3

        # 第二次重複掃描 (應為差異跳過，不重複建立資產)
        run_sync_scan(root_id)
        status2 = client.get("/api/scan/status").json()
        assert status2["stage"] == "completed"
        assert status2["new_assets_count"] == 0  # 重複掃描時，新資產為 0
        assert status2["new_candidates_count"] == 0

        with SyncSessionLocal() as session:
            asset_count_2 = session.query(Asset).count()
            assert asset_count_2 == 3  # 資產總數依然維持 3，無重複資產

        client.delete(f"/api/roots/{root_id}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

def test_scan_progress_and_cancel_endpoints():
    # Test getting scan status
    res = client.get("/api/scan/status")
    assert res.status_code == 200
    data = res.json()
    assert "stage" in data
    assert "percentage" in data
    assert "processed_files" in data

    # Test cancel endpoint
    res_cancel = client.post("/api/scan/cancel")
    assert res_cancel.status_code == 200
    assert res_cancel.json()["status"] == "ok"
