import pytest
from pathlib import Path
from PIL import Image
from fastapi.testclient import TestClient

from app.main import app
from app.core.database import init_db, SyncSessionLocal
from app.models.schema import Asset, File
from app.services.scan_job import run_scan_job
from app.services.basic_stage import run_basic_stage
from app.services.rules_engine import rules_engine

client = TestClient(app)


def test_e2e_thumbnail_generation_and_api(tmp_path):
    # 1. Create two test images (one standard, one blurred)
    img1_path = tmp_path / "photo_a.jpg"
    img2_path = tmp_path / "photo_b.jpg"
    Image.new("RGB", (600, 400), color=(100, 150, 200)).save(img1_path)
    Image.new("RGB", (600, 400), color=(50, 50, 50)).save(img2_path)

    # 2. Add root via API
    res = client.post("/api/roots", json={"path": str(tmp_path), "priority": 1})
    assert res.status_code == 200
    root_id = res.json()["id"]

    # 3. Scan & analyze
    with SyncSessionLocal() as session:
        run_scan_job(session, root_ids=[root_id], mode="full")
        session.commit()
        run_basic_stage(session)
        session.commit()
        rules_engine.evaluate_all_rules(session)

    # 4. Get candidates list
    cand_res = client.get("/api/candidates")
    assert cand_res.status_code == 200
    items = cand_res.json()
    assert len(items) >= 1

    first_item = items[0]
    assert "thumbnail_url" in first_item
    assert "/api/thumbnails/" in first_item["thumbnail_url"]
    assert "v=" in first_item["thumbnail_url"]

    # 5. Fetch thumbnail image via API with version query
    thumb_res = client.get(first_item["thumbnail_url"])
    assert thumb_res.status_code == 200
    assert thumb_res.headers["content-type"] == "image/jpeg"
    assert "immutable" in thumb_res.headers.get("cache-control", "")

    # 6. Test cleanup endpoint
    cleanup_res = client.post("/api/thumbnails/cleanup")
    assert cleanup_res.status_code == 200
    assert cleanup_res.json()["status"] == "ok"
