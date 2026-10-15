"""
v0.5 修正的回歸測試：
- 資料安全：missing 不能當 Keeper、Keeper 保護（三種繞過方式 + 刪除前最後防線）、刪前校驗
- 不再寫入假資料：壞圖、打不開的影片、CLIP 失敗
- GPU 批次管線：OOM 自動減半、逐張隔離錯誤
- 近似重複：串連分組、群組上限拆分、方法切換
- 連拍、任務控制、Origin 防護、設定驗證、Dashboard 容量
"""
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.core.database import SyncSessionLocal
from app.core.progress import ProgressTracker, progress_tracker
from app.main import app
from app.models.schema import (Asset, AnalysisFailure, Candidate, ClipFeature, Feature, File, Group,
                               Root, Setting, VideoInfo)
from app.services.basic_stage import run_basic_stage
from app.services.clip_stage import run_clip_stage
from app.services.deletion import deletion_service
from app.services.rules_engine import rules_engine
from app.services.scan_job import run_scan_job
from app.services.stats_service import get_analysis_stage_stats, get_dashboard_stats
from app.services.video_stage import run_video_stage

client = TestClient(app)


# ─────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────
def _root(s, path="/virtual", priority=0):
    r = Root(path=path, recursive=True, available=True, priority=priority)
    s.add(r)
    s.flush()
    return r


def _asset(s, root, name, status="analysed", size=1000, sha=None, mtime=1000.0, media="photo", path=None):
    p = path or f"{root.path}/{name}"
    a = Asset(root_id=root.id, media_type=media, primary_path=p, size_total=size, status=status)
    s.add(a)
    s.flush()
    s.add(File(asset_id=a.id, path=p, size=size, mtime=mtime, format="jpg", role="primary",
               sha256=sha, hash_state="done" if sha else "pending"))
    s.flush()
    return a


def _clip(s, asset, vec, zs=None):
    v = np.asarray(vec, dtype=np.float32)
    v = v / np.linalg.norm(v)
    s.add(ClipFeature(asset_id=asset.id, model="ViT-B-16", pretrained="openai", dim=len(v),
                      embedding=v.tobytes(), zs=zs or {"normal_photo": 1.0}, analyzed_at=datetime.utcnow()))


def _feat(s, asset, blur=500.0, bright=120.0, phash=None, taken_at=None, exif=None):
    s.add(Feature(asset_id=asset.id, phash=phash, blur_score=blur, brightness=bright, width=4000, height=3000,
                  taken_at=taken_at, exif=exif or {}, decode_status="ok", analyzed_at=datetime.utcnow()))


def _set(s, **kw):
    import json
    for k, v in kw.items():
        s.merge(Setting(key=k, value=json.dumps(v)))
    s.commit()


def _vec(seed, dim=512):
    return np.random.default_rng(seed).standard_normal(dim).astype(np.float32)


# ─────────────────────────────────────────────
# C1 / C2 / C3：刪除安全
# ─────────────────────────────────────────────
class TestKeeperSafety:
    def test_missing_asset_never_keeper(self):
        with SyncSessionLocal() as s:
            r_hi = _root(s, "/hi", priority=0)
            r_lo = _root(s, "/lo", priority=5)
            gone = _asset(s, r_hi, "a.jpg", status="missing", sha="x" * 64)   # 優先度最高但已不存在
            k1 = _asset(s, r_lo, "a.jpg", sha="x" * 64)
            k2 = _asset(s, r_lo, "b.jpg", sha="x" * 64)
            s.commit()
            rules_engine.evaluate_all_rules(s)
            assert s.query(Candidate).filter(Candidate.asset_id == gone.id).count() == 0
            keeper = s.query(Group).one().keeper_asset_id
            assert keeper in (k1.id, k2.id)

    def _exact_pair(self, s):
        r = _root(s)
        k = _asset(s, r, "k.jpg", sha="y" * 64, size=2000)   # 較大 → Keeper
        n = _asset(s, r, "n.jpg", sha="y" * 64, size=1000)
        s.commit()
        rules_engine.evaluate_all_rules(s)
        return k.id, n.id

    def test_queue_keeper_first_then_rest_is_blocked(self):
        with SyncSessionLocal() as s:
            k, n = self._exact_pair(s)
        r1 = client.post("/api/candidates/batch-status", json={"asset_ids": [k], "state": "queued"}).json()
        assert r1["updated_count"] == 1          # 另一張還在，允許
        r2 = client.post("/api/candidates/batch-status", json={"asset_ids": [n], "state": "queued"}).json()
        assert r2["protected_count"] == 1 and r2["updated_count"] == 0
        with SyncSessionLocal() as s:
            assert s.get(Asset, n).status != "queued"

    def test_representative_blur_candidate_cannot_bypass(self):
        with SyncSessionLocal() as s:
            r = _root(s)
            k = _asset(s, r, "k.jpg", sha="z" * 64, size=2000)
            n = _asset(s, r, "n.jpg", sha="z" * 64, size=1000)
            _feat(s, k, blur=1.0)       # Keeper 同時也是模糊 → 代表候選可能是 blur 那條
            s.commit()
            rules_engine.evaluate_all_rules(s)
            blur_cid = s.query(Candidate.id).filter(Candidate.asset_id == k.id, Candidate.rule == "blur").scalar()
            n_cid = s.query(Candidate.id).filter(Candidate.asset_id == n.id).first()[0]
        res = client.post("/api/candidates/batch-status",
                          json={"candidate_ids": [blur_cid, n_cid], "state": "queued"}).json()
        assert res["protected_count"] == 1
        with SyncSessionLocal() as s:
            statuses = {s.get(Asset, k.id).status, s.get(Asset, n.id).status}
            assert statuses != {"queued"}

    def test_reevaluate_restores_keeper_when_group_fully_queued(self):
        with SyncSessionLocal() as s:
            k, n = self._exact_pair(s)
            # 繞過 API 直接把整組設成待刪（模擬舊版資料或其他路徑）
            s.query(Candidate).update({"state": "queued"})
            s.query(Asset).update({"status": "queued"})
            s.commit()
            rules_engine.evaluate_all_rules(s)
            states = {a.id: a.status for a in s.query(Asset)}
            assert "queued" in states.values() and set(states.values()) != {"queued"}

    def test_delete_time_last_line_of_defence(self, tmp_path):
        p1, p2 = tmp_path / "a.jpg", tmp_path / "b.jpg"
        Image.new("RGB", (20, 20), (1, 2, 3)).save(p1)
        p2.write_bytes(p1.read_bytes())
        with SyncSessionLocal() as s:
            r = _root(s, str(tmp_path))
            a1 = _asset(s, r, "a.jpg", path=str(p1), sha="q" * 64, size=p1.stat().st_size, mtime=p1.stat().st_mtime)
            a2 = _asset(s, r, "b.jpg", path=str(p2), sha="q" * 64, size=p2.stat().st_size, mtime=p2.stat().st_mtime)
            s.commit()
            rules_engine.evaluate_all_rules(s)
            s.query(Asset).update({"status": "queued"})
            s.commit()
            res = deletion_service.execute_deletion(s, mode="permanent")
        assert res["protected_count"] == 1 and res["success_count"] == 1
        assert p1.exists() != p2.exists()


class TestPreDeleteCheck:
    @pytest.mark.parametrize("change", ["size_only", "mtime_only"])
    def test_modified_file_refused(self, tmp_path, change):
        p = tmp_path / "x.jpg"
        Image.new("RGB", (20, 20), (9, 9, 9)).save(p)
        st = p.stat()
        with SyncSessionLocal() as s:
            r = _root(s, str(tmp_path))
            size = st.st_size + 1 if change == "size_only" else st.st_size
            mtime = st.st_mtime if change == "size_only" else st.st_mtime - 3600
            _asset(s, r, "x.jpg", path=str(p), status="queued", size=size, mtime=mtime)
            s.commit()
            res = deletion_service.execute_deletion(s, mode="permanent")
        assert res["success_count"] == 0 and res["failed_count"] == 1
        assert p.exists()

    def test_permanent_requires_confirm_text(self):
        r = client.post("/api/process/execute", json={"confirmed": True, "mode": "permanent"})
        assert r.status_code == 400


# ─────────────────────────────────────────────
# H2 / M11：不再寫入假資料
# ─────────────────────────────────────────────
class TestNoFakeData:
    def test_corrupt_jpeg_recorded_as_failure(self, tmp_path):
        (tmp_path / "bad.jpg").write_bytes(b"not an image at all")
        with SyncSessionLocal() as s:
            r = _root(s, str(tmp_path))
            run_scan_job(s, root_ids=[r.id])
            res = run_basic_stage(s)
            assert res["failed"] == 1
            a = s.query(Asset).one()
            f = s.query(Feature).filter_by(asset_id=a.id).one()
            assert f.decode_status == "error" and f.phash is None
            assert s.query(AnalysisFailure).filter_by(asset_id=a.id, stage="basic").count() == 1
            stats = get_analysis_stage_stats(s)["basic"]
            assert stats["failed"] == 1 and stats["pending"] == 0
            rules_engine.evaluate_all_rules(s)
            assert s.query(Candidate).count() == 0

    def test_unopenable_video_recorded_as_failure(self, tmp_path):
        (tmp_path / "clip.mp4").write_bytes(b"garbage")
        with SyncSessionLocal() as s:
            r = _root(s, str(tmp_path))
            run_scan_job(s, root_ids=[r.id])
            res = run_video_stage(s)
            assert res["failed"] == 1
            assert s.query(VideoInfo).count() == 0
            assert s.query(AnalysisFailure).filter_by(stage="video").count() == 1
            # 不重試時不會再處理
            assert run_video_stage(s)["failed"] == 0

    def test_basic_reads_exif_capture_time(self, tmp_path):
        p = tmp_path / "e.jpg"
        img = Image.new("RGB", (64, 48), (200, 10, 10))
        exif = Image.Exif()
        exif.get_ifd(0x8769)[36867] = "2024:05:06 07:08:09"
        exif.get_ifd(0x8769)[37521] = "25"
        img.save(p, exif=exif)
        with SyncSessionLocal() as s:
            r = _root(s, str(tmp_path))
            run_scan_job(s, root_ids=[r.id])
            run_basic_stage(s)
            f = s.query(Feature).one()
            assert f.taken_at == datetime(2024, 5, 6, 7, 8, 9, 250000)


# ─────────────────────────────────────────────
# GPU 批次管線
# ─────────────────────────────────────────────
def _make_photos(tmp_path, n):
    for i in range(n):
        Image.new("RGB", (48, 32), ((i * 37) % 255, (i * 91) % 255, (i * 13) % 255)).save(tmp_path / f"p{i:03d}.jpg")


class TestClipPipeline:
    def test_batches_and_oom_backoff(self, tmp_path, fake_clip):
        _make_photos(tmp_path, 20)
        fake_clip.oom_until_batch = 3          # 批次 > 3 張就「顯存不足」
        with SyncSessionLocal() as s:
            r = _root(s, str(tmp_path))
            run_scan_job(s, root_ids=[r.id])
            run_basic_stage(s)
            res = run_clip_stage(s, device="cpu", batch_size=16, runners=3)
            assert res["processed"] == 20 and res["failed"] == 0
            assert res["batch_size"] <= 3
            assert s.query(ClipFeature).count() == 20
            zs = s.query(ClipFeature).first().zs
            assert set(zs) == {"screenshot", "document", "meme", "mistouch", "normal_photo"}
            assert abs(sum(zs.values()) - 1.0) < 0.01

    def test_per_image_error_isolated(self, tmp_path, fake_clip, monkeypatch):
        _make_photos(tmp_path, 6)
        orig = fake_clip.encode_images

        def flaky(self, batch):
            # 批次中只要含有「壞圖」就整批失敗 → 管線應逐張重跑，只讓那一張失敗
            if np.any(np.isclose(batch[:, 0, 16, 16], flaky.bad)):
                raise ValueError("boom")
            return orig(self, batch)

        with SyncSessionLocal() as s:
            r = _root(s, str(tmp_path))
            run_scan_job(s, root_ids=[r.id])
            run_basic_stage(s)
            # 以第一張圖的像素值當作「壞圖」標記
            from app.stubs.clip_stub import clip_extractor
            a0 = s.query(Asset).order_by(Asset.primary_path).first()
            img = clip_extractor.load_image(a0.primary_path)
            flaky.bad = clip_extractor.preprocess(img, 32)[0, 16, 16]
            monkeypatch.setattr(fake_clip, "encode_images", flaky)
            res = run_clip_stage(s, device="cpu", batch_size=4)
            assert res["failed"] >= 1 and res["processed"] + res["failed"] == 6
            assert s.query(ClipFeature).filter_by(asset_id=a0.id).count() == 0
            assert s.query(AnalysisFailure).filter_by(asset_id=a0.id, stage="clip").count() == 1

    def test_unreadable_file_is_failure_not_fake_vector(self, tmp_path):
        with SyncSessionLocal() as s:
            r = _root(s, str(tmp_path))
            a = _asset(s, r, "ghost.jpg", path=str(tmp_path / "ghost.jpg"))
            s.commit()
            res = run_clip_stage(s, device="cpu")
            assert res["failed"] == 1 and res["processed"] == 0
            assert s.query(ClipFeature).count() == 0
            # 未要求重試時不會重跑
            assert run_clip_stage(s, device="cpu")["failed"] == 0
            assert run_clip_stage(s, device="cpu", retry_failed=True)["failed"] == 1

    def test_cuda_requested_but_unavailable_fails_fast(self):
        from app.stubs.clip_stub import ClipDeviceError
        with SyncSessionLocal() as s:
            with pytest.raises(ClipDeviceError):
                run_clip_stage(s, device="cuda")


# ─────────────────────────────────────────────
# 近似重複 / 連拍
# ─────────────────────────────────────────────
class TestNearDuplicates:
    def test_chain_merges_into_one_group(self):
        base = _vec(1)
        with SyncSessionLocal() as s:
            r = _root(s)
            # 在同一平面上每張轉 15°：相鄰 cos15°≈0.966 ≥ 0.95，隔一張 cos30°≈0.866 < 0.95
            # A~B、B~C、C~D 相似，A 與 C/D 不直接相似，仍要串成同一群
            e0, e1 = np.eye(512, dtype=np.float32)[0], np.eye(512, dtype=np.float32)[1]
            vecs = [np.cos(np.radians(d)) * e0 + np.sin(np.radians(d)) * e1 for d in (0, 15, 30, 45)]
            assets = []
            for i, v in enumerate(vecs):
                a = _asset(s, r, f"c{i}.jpg")
                _clip(s, a, v)
                _feat(s, a, blur=100 + i)
                assets.append(a)
            far = _asset(s, r, "far.jpg")
            _clip(s, far, _vec(99))
            _feat(s, far)
            s.commit()
            _set(s, near_dup_method="clip", clip_dup_threshold=0.95, rule_blur_enabled=False)
            rules_engine.evaluate_all_rules(s)
            groups = s.query(Group).filter_by(type="near").all()
            assert len(groups) == 1
            members = {c.asset_id for c in s.query(Candidate).filter_by(group_id=groups[0].id)}
            assert members == {a.id for a in assets}
            # 每個資產只屬於一個近似群組
            assert s.query(Candidate).filter_by(rule="near_dup").count() == 4
            # Keeper = 最清晰
            assert groups[0].keeper_asset_id == assets[3].id

    def test_large_group_split_by_cap(self):
        base = _vec(5)
        with SyncSessionLocal() as s:
            r = _root(s)
            for cluster in range(2):
                center = base + 0.30 * _vec(100 + cluster)
                for i in range(6):
                    a = _asset(s, r, f"g{cluster}_{i}.jpg")
                    _clip(s, a, center + 0.05 * _vec(1000 + cluster * 10 + i))
                    _feat(s, a)
            s.commit()
            _set(s, near_dup_method="clip", clip_dup_threshold=0.90, near_dup_max_group=6,
                 rule_blur_enabled=False)
            rules_engine.evaluate_all_rules(s)
            sizes = sorted(len(g.candidates) for g in s.query(Group).filter_by(type="near"))
            assert sizes and max(sizes) <= 6

    def test_phash_used_when_no_clip_vector(self):
        with SyncSessionLocal() as s:
            r = _root(s)
            a = _asset(s, r, "a.jpg"); _feat(s, a, phash="ffff0000ffff0000")
            b = _asset(s, r, "b.jpg"); _feat(s, b, phash="ffff0000ffff0003")   # 距離 2
            c = _asset(s, r, "c.jpg"); _feat(s, c, phash="0000ffff0000ffff")   # 很遠
            s.commit()
            _set(s, rule_blur_enabled=False)
            rules_engine.evaluate_all_rules(s)     # 預設 either
            g = s.query(Group).filter_by(type="near").one()
            assert {x.asset_id for x in g.candidates} == {a.id, b.id}
            _set(s, near_dup_method="clip")
            rules_engine.evaluate_all_rules(s)
            assert s.query(Group).filter_by(type="near").count() == 0

    def test_both_requires_clip_and_phash(self):
        v = _vec(7)
        with SyncSessionLocal() as s:
            r = _root(s)
            a = _asset(s, r, "a.jpg"); _clip(s, a, v); _feat(s, a, phash="ffff0000ffff0000")
            b = _asset(s, r, "b.jpg"); _clip(s, b, v); _feat(s, b, phash="0000ffff0000ffff")
            s.commit()
            _set(s, near_dup_method="both", rule_blur_enabled=False)
            rules_engine.evaluate_all_rules(s)
            assert s.query(Group).filter_by(type="near").count() == 0
            _set(s, near_dup_method="clip")
            rules_engine.evaluate_all_rules(s)
            assert s.query(Group).filter_by(type="near").count() == 1

    def test_burst_keeps_sharpest(self):
        base = _vec(9)
        t0 = datetime(2025, 1, 1, 12, 0, 0)
        with SyncSessionLocal() as s:
            r = _root(s)
            ids = []
            for i, blur in enumerate([300.0, 900.0, 200.0]):
                a = _asset(s, r, f"b{i}.jpg")
                _clip(s, a, base + 0.25 * _vec(200 + i))     # 相似度約 0.94，低於近似門檻 0.95
                _feat(s, a, blur=blur, taken_at=t0 + timedelta(milliseconds=400 * i))
                ids.append(a.id)
            late = _asset(s, r, "late.jpg")
            _clip(s, late, base + 0.25 * _vec(300))
            _feat(s, late, taken_at=t0 + timedelta(seconds=30))
            s.commit()
            _set(s, near_dup_method="clip", clip_dup_threshold=0.99, rule_blur_enabled=False)
            rules_engine.evaluate_all_rules(s)
            g = s.query(Group).filter_by(type="burst").one()
            assert {c.asset_id for c in g.candidates} == set(ids)
            assert g.keeper_asset_id == ids[1]

    def test_near_dup_speed_5k(self):
        rng = np.random.default_rng(0)
        with SyncSessionLocal() as s:
            r = _root(s)
            centers = rng.standard_normal((2500, 512)).astype(np.float32)
            for i in range(5000):
                a = Asset(root_id=r.id, media_type="photo", primary_path=f"/v/{i}.jpg", size_total=1, status="analysed")
                s.add(a)
            s.flush()
            for i, a in enumerate(s.query(Asset).all()):
                v = centers[i // 2] + 0.05 * rng.standard_normal(512).astype(np.float32)
                v /= np.linalg.norm(v)
                s.add(ClipFeature(asset_id=a.id, model="ViT-B-16", pretrained="openai", dim=512,
                                  embedding=v.tobytes(), zs={"normal_photo": 1.0}))
            s.commit()
            _set(s, near_dup_method="clip")
            t = time.time()
            counts = rules_engine.evaluate_all_rules(s)
            assert time.time() - t < 60
            assert counts.get("near_dup_groups") == 2500


# ─────────────────────────────────────────────
# 任務控制 / 安全 / 設定 / 統計
# ─────────────────────────────────────────────
class TestJobControl:
    def test_try_start_is_exclusive(self):
        t = ProgressTracker()
        assert t.try_start_analyze(["clip"]) is True
        assert t.try_start_scan() is False

    def test_cancel_keeps_running_until_finished(self):
        t = ProgressTracker()
        assert t.try_start_scan()
        t.request_cancel()
        assert t.state.is_running is True          # 舊工作還沒停
        assert t.try_start_scan() is False         # 不能啟動新工作
        t.ensure_finished()
        assert t.state.is_running is False and t.state.stage_label == "已取消"
        assert t.try_start_scan() is True
        assert t.is_cancel_requested() is False

    def test_double_click_analyze_starts_once(self):
        progress_tracker.try_start_analyze(["hash"])
        try:
            r = client.post("/api/jobs/analyze", json={"stages": ["hash"]})
            assert r.status_code == 400
        finally:
            progress_tracker.ensure_finished()


class TestRequestGuards:
    def test_cross_site_post_rejected(self):
        r = client.post("/api/settings/reset", headers={"Origin": "https://evil.example"})
        assert r.status_code == 403
        r2 = client.post("/api/jobs/cancel", headers={"Sec-Fetch-Site": "cross-site"})
        assert r2.status_code == 403

    def test_foreign_host_rejected(self):
        r = client.get("/api/health", headers={"Host": "evil.example"})
        assert r.status_code == 400

    def test_install_cuda_requires_body(self):
        r = client.post("/api/system/install-cuda")
        assert r.status_code == 422


class TestSettingsValidation:
    @pytest.mark.parametrize("payload", [
        {"no_such_key": 1},
        {"phash_threshold": 99},
        {"clip_dup_threshold": "abc"},
        {"near_dup_method": "magic"},
    ])
    def test_invalid_rejected(self, payload):
        r = client.put("/api/settings", json={"settings": payload, "re_evaluate": False})
        assert r.status_code == 422
        with SyncSessionLocal() as s:
            assert s.query(Setting).count() == 0


class TestDashboard:
    def test_releasable_counts_each_duplicate(self):
        with SyncSessionLocal() as s:
            r = _root(s)
            for n in ("a", "b", "c"):
                _asset(s, r, f"{n}.jpg", sha="d" * 64, size=5000)
            s.commit()
            rules_engine.evaluate_all_rules(s)
            stats = get_dashboard_stats(s)
            assert stats["candidates"]["releasable_size_bytes"] == 10000


class TestEndToEndJobs:
    def test_scan_analyze_evaluate_via_api(self, tmp_path):
        base = Image.new("RGB", (300, 200), (30, 120, 200))
        base.save(tmp_path / "a.jpg")
        base.save(tmp_path / "a_copy.jpg")                       # 完全重複
        base.resize((150, 100)).save(tmp_path / "a_small.jpg")   # 近似
        Image.new("RGB", (300, 200), (220, 40, 40)).save(tmp_path / "other.jpg")
        (tmp_path / "broken.jpg").write_bytes(b"xx")
        rid = client.post("/api/roots", json={"path": str(tmp_path)}).json()["id"]
        assert client.post("/api/jobs/scan", json={"mode": "full", "root_id": rid}).status_code == 200
        assert client.get("/api/jobs/status").json()["stage"] == "completed"
        r = client.post("/api/jobs/analyze", json={"stages": ["hash", "basic", "clip", "video"], "device": "auto"})
        assert r.status_code == 200
        st = client.get("/api/jobs/status").json()
        assert st["stage"] == "completed", st
        assert st["stage_stats"]["basic"]["failed"] == 1
        assert st["stage_stats"]["clip"]["failed"] == 1     # 壞圖沒有縮圖、原檔也讀不到
        status = client.get("/api/analysis/status").json()["stages"]
        assert status["clip"]["done"] == 4 and status["clip"]["failed"] == 1
        assert client.post("/api/jobs/evaluate").status_code == 200
        cands = client.get("/api/candidates").json()
        rules = {c["rule"] for c in cands}
        assert "exact_dup" in rules
        fails = client.get("/api/analysis/failures").json()
        assert {f["stage"] for f in fails} == {"basic", "clip"}
