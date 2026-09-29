import pytest
import tempfile
import shutil
from pathlib import Path
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.core.database import init_db, SyncSessionLocal
from app.models.schema import Root, Asset, File, Feature, Candidate, Group
from app.services.scanner import scan_directory
from app.services.pairing import pairing_engine
from app.services.rules_engine import rules_engine
from app.api.scan import run_sync_scan

client = TestClient(app)

def test_rules_engine_and_candidates():
    init_db()
    temp_dir = tempfile.mkdtemp(prefix="rules_test_")
    try:
        # Create identical images for Exact Duplicate test
        img1_path = Path(temp_dir) / "photo_a.jpg"
        img2_path = Path(temp_dir) / "photo_a_copy.jpg"
        img = Image.new("RGB", (200, 200), color=(100, 150, 200))
        img.save(img1_path)
        shutil.copy(img1_path, img2_path)

        # Create a blurred image for Blur rule test
        blur_path = Path(temp_dir) / "blur_photo.jpg"
        blur_img = Image.new("RGB", (200, 200), color=(50, 50, 50))
        blur_img.save(blur_path)

        # Add root via API
        res = client.post("/api/roots", json={"path": temp_dir, "priority": 1})
        assert res.status_code == 200
        root_id = res.json()["id"]

        # Run synchronous scan and evaluation
        run_sync_scan(root_id)

        # Test Dashboard stats API
        dash_res = client.get("/api/dashboard/stats")
        assert dash_res.status_code == 200
        dash_data = dash_res.json()
        assert dash_data["assets"]["total"] == 3
        assert dash_data["candidates"]["total"] >= 2

        # Test Candidates API
        cand_res = client.get("/api/candidates")
        assert cand_res.status_code == 200
        candidates = cand_res.json()
        assert len(candidates) >= 2

        # Check Exact Duplicate has 1 keeper and 1 candidate
        exact_cands = [c for c in candidates if c["rule"] == "exact_dup"]
        assert len(exact_cands) == 2
        keepers = [c for c in exact_cands if c["is_keeper"]]
        non_keepers = [c for c in exact_cands if not c["is_keeper"]]
        assert len(keepers) == 1
        assert len(non_keepers) == 1

        # Test Keeper Protection: attempt to queue both keeper and non-keeper
        all_exact_ids = [c["id"] for c in exact_cands]
        batch_res = client.post("/api/candidates/batch-status", json={
            "candidate_ids": all_exact_ids,
            "state": "queued"
        })
        assert batch_res.status_code == 200
        batch_data = batch_res.json()
        # Keeper should be protected!
        assert batch_data["protected_count"] == 1
        assert batch_data["updated_count"] == 1

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
