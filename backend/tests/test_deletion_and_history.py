import pytest
import tempfile
import shutil
import os
from pathlib import Path
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.core.database import init_db, SyncSessionLocal
from app.models.schema import Root, Asset, File, Candidate, ActionLog
from app.api.scan import run_sync_scan

client = TestClient(app)

def test_deletion_safety_and_history():
    init_db()
    temp_dir = tempfile.mkdtemp(prefix="delete_test_")
    try:
        # Create a Live Photo pair (HEIC/JPG + MOV)
        img_path = Path(temp_dir) / "LIVE_001.JPG"
        img = Image.new("RGB", (100, 100), color=(100, 200, 100))
        img.save(img_path)

        mov_path = Path(temp_dir) / "LIVE_001.MOV"
        with open(mov_path, "wb") as f:
            f.write(b"mock_live_video_data")

        # Create a single photo
        single_path = Path(temp_dir) / "SINGLE_002.JPG"
        img.save(single_path)

        # 1. Add root and scan
        res = client.post("/api/roots", json={"path": temp_dir})
        assert res.status_code == 200
        root_id = res.json()["id"]

        run_sync_scan(root_id)

        # 2. Get assets from DB
        with SyncSessionLocal() as session:
            live_asset = session.query(Asset).filter(Asset.primary_path == str(img_path)).first()
            assert live_asset is not None
            # Mark live asset as queued
            live_asset.status = "queued"
            session.commit()
            live_asset_id = live_asset.id

        # 3. Check Process summary
        summary_res = client.get("/api/process/summary")
        assert summary_res.status_code == 200
        summary_data = summary_res.json()
        assert summary_data["total_count"] == 1
        assert summary_data["items"][0]["asset_id"] == live_asset_id
        # Pair should be present
        assert len(summary_data["items"][0]["paired_paths"]) == 1

        # 4. Attempt deletion without confirmation (must fail)
        unconfirmed_res = client.post("/api/process/execute", json={
            "asset_ids": [live_asset_id],
            "confirmed": False
        })
        assert unconfirmed_res.status_code == 400

        # 5. Execute deletion with confirmation (send2trash)
        exec_res = client.post("/api/process/execute", json={
            "asset_ids": [live_asset_id],
            "confirmed": True,
            "mode": "trash"
        })
        assert exec_res.status_code == 200
        exec_data = exec_res.json()
        assert exec_data["success_count"] == 1
        assert exec_data["failed_count"] == 0
        batch_id = exec_data["batch_id"]

        # Check DB status
        with SyncSessionLocal() as session:
            deleted_asset = session.get(Asset, live_asset_id)
            assert deleted_asset.status == "deleted"

            # Check action log created for both JPG and MOV (鐵則 4 配對連動)
            logs = session.query(ActionLog).filter(ActionLog.batch_id == batch_id).all()
            assert len(logs) == 2  # Primary + Paired MOV

        # 6. Test History API
        history_res = client.get("/api/history/batches")
        assert history_res.status_code == 200
        batches = history_res.json()
        assert any(b["batch_id"] == batch_id for b in batches)

        details_res = client.get(f"/api/history/batches/{batch_id}")
        assert details_res.status_code == 200
        details = details_res.json()
        assert len(details) == 2

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
