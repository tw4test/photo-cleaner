import pytest
import tempfile
import shutil
from pathlib import Path
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.core.database import init_db, SyncSessionLocal
from app.models.schema import Root, Asset, File, Feature, ClipFeature, Candidate, Group
from app.services.scanner import scan_directory
from app.services.pairing import pairing_engine
from app.services.rules_engine import rules_engine
from app.api.scan import run_sync_scan
from app.services.hash_stage import run_hash_stage
from app.services.basic_stage import run_basic_stage

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

        # Run synchronous scan
        run_sync_scan(root_id)

        # In split architecture: run analysis stages and evaluate rules
        with SyncSessionLocal() as session:
            run_hash_stage(session)
            run_basic_stage(session)
            rules_engine.evaluate_all_rules(session)

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

        # Test Strict Multi-Batch Keeper Protection:
        # Now that the non-keeper is already queued, try to queue ONLY the keeper in a second batch
        keeper_ids = [k["id"] for k in keepers]
        batch_res2 = client.post("/api/candidates/batch-status", json={
            "candidate_ids": keeper_ids,
            "state": "queued"
        })
        assert batch_res2.status_code == 200
        batch_data2 = batch_res2.json()
        # The keeper MUST still be protected because the other group member is already in queued!
        assert batch_data2["protected_count"] == 1
        assert batch_data2["updated_count"] == 0

        # Verify candidate item response has 'rules' and 'reasons' list
        for c in candidates:
            assert isinstance(c["rules"], list)
            assert len(c["rules"]) >= 1
            assert isinstance(c["reasons"], list)

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_ai_semantic_rules_with_normal_photo_discrimination():
    """驗證 AI 語義分類在引入 normal_photo 背景類後，正常相片不會誤判，真實文件/截圖能準確命中"""
    init_db()
    with SyncSessionLocal() as session:
        # 建立 3 個測試資產
        # 資產 1: 正常生活舞台合照 (normal_photo: 0.98, document: 0.01)
        # 資產 2: 發票/收據 (document: 0.85, normal_photo: 0.02)
        # 資產 3: 手機截圖 (screenshot: 0.90, normal_photo: 0.01)
        root = Root(path="D:/test_photos_ai", priority=1)
        session.add(root)
        session.flush()

        a_normal = Asset(root_id=root.id, primary_path="D:/test_photos_ai/stage_group.jpg", media_type="photo", status="active", size_total=2000000)
        a_receipt = Asset(root_id=root.id, primary_path="D:/test_photos_ai/receipt_bill.jpg", media_type="photo", status="active", size_total=500000)
        a_screen = Asset(root_id=root.id, primary_path="D:/test_photos_ai/chat_screen.png", media_type="photo", status="active", size_total=800000)
        session.add_all([a_normal, a_receipt, a_screen])
        session.flush()

        # 加入 ClipFeature
        session.add(ClipFeature(
            asset_id=a_normal.id,
            model="ViT-B-16",
            pretrained="openai",
            dim=512,
            zs={"normal_photo": 0.98, "document": 0.01, "screenshot": 0.005, "meme": 0.003, "mistouch": 0.002}
        ))
        session.add(ClipFeature(
            asset_id=a_receipt.id,
            model="ViT-B-16",
            pretrained="openai",
            dim=512,
            zs={"normal_photo": 0.02, "document": 0.85, "screenshot": 0.10, "meme": 0.02, "mistouch": 0.01}
        ))
        session.add(ClipFeature(
            asset_id=a_screen.id,
            model="ViT-B-16",
            pretrained="openai",
            dim=512,
            zs={"normal_photo": 0.01, "document": 0.05, "screenshot": 0.90, "meme": 0.03, "mistouch": 0.01}
        ))
        session.flush()

        # 執行規則評估
        rules_engine.evaluate_all_rules(session)

        # 檢查候選結果
        cands = session.query(Candidate).all()
        cand_map = {c.asset_id: c for c in cands}

        # 1. 正常相片絕對不應該成為候選
        assert a_normal.id not in cand_map, "正常相片 (normal_photo 佔優勢) 不應被列為待刪候選"

        # 2. 發票收據應被正確識別為 document
        assert a_receipt.id in cand_map, "發票收據應被列入候選"
        assert cand_map[a_receipt.id].rule == "document"
        assert cand_map[a_receipt.id].score == 0.85

        # 3. 螢幕截圖應被正確識別為 screenshot
        assert a_screen.id in cand_map, "螢幕截圖應被列入候選"
        assert cand_map[a_screen.id].rule == "screenshot"
        assert cand_map[a_screen.id].score == 0.90
