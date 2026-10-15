"""
功能情境測試（對應 photo-cleaner-functional-test-plan.md）。

透過真實的 HTTP API（FastAPI TestClient）走完整流程：
新增路徑 → 掃描 → 分析 → 評估 → 審核 → 刪除，並驗證中斷、重跑、重試、並行與所有數字恆等式。
測試名稱中的代號（S-03、I-02…）對應測試計畫。

執行模式
--------
1. 預設（不需任何設定）：自動在暫存資料夾產生合成相片庫，使用假 CLIP，約 1–2 分鐘。
       pytest tests\\test_functional.py -v
2. 你的相片（建議）：先建立測試相片庫，再指定路徑。
       python scripts\\make_test_library.py E:\\PCTest --mixed E:\\MyPhotos --max 700
       $env:PC_FUNC_LIB="E:\\PCTest"; pytest tests\\test_functional.py -v
3. 加上真實 CLIP 模型（GPU）：
       $env:PC_FUNC_REAL_CLIP="1"
   用完記得：Remove-Item Env:PC_FUNC_LIB, Env:PC_FUNC_REAL_CLIP

注意
----
- 測試使用暫存資料庫與縮圖快取，不會碰 backend\\data\\app.db。
- 測試會暫時修改 / 刪除測試相片庫中的少數檔案，結束時自動還原（保留原修改時間）。
- D-03 會把 Live Photo 配對檔送進 Windows 資源回收筒，再從備份還原；回收筒內的副本可清空。
- 標記 xfail 的項目是「已知問題」（代號 F-xx），修好後會變成 XPASS 提醒移除標記。
- 每次執行會在測試相片庫根目錄寫入 functional_report.json（基準數字），請連同結果一起貼上。
"""
import json
import os
import shutil
import sqlite3
import tempfile
import time
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import patch

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import SyncSessionLocal, init_db, sync_engine
from app.core.progress import ProgressState, progress_tracker
from app.main import app
from app.models.schema import (ActionLog, AnalysisFailure, AppState, Asset, Candidate, ClipFeature, Feature, File,
                               Group, Root, Setting, VideoInfo)
from app.services.pairing import pairing_engine
from app.services.rules_engine import rules_engine
from app.services.scanner import scan_directory
from app.services.thumbnail import thumb_key_args, thumbnail_service
from app.stubs.clip_stub import OPENAI_MEAN, OPENAI_STD, clip_extractor
from scripts import make_test_library as mtl

LIB_ENV = os.environ.get("PC_FUNC_LIB")
REAL_CLIP = os.environ.get("PC_FUNC_REAL_CLIP") == "1"
ALL_STAGES = ["hash", "basic", "clip", "video"]
INACTIVE = ("deleted", "missing")
PERMANENT = {"confirmed": True, "mode": "permanent", "confirm_text": "永久刪除"}

client = TestClient(app)


# ═════════════════════════════════════════════════════════════════════
# 假 CLIP（與 tests/conftest.py 相同邏輯；在本模組內以 module scope 使用，避免每個測試重載模型）
# ═════════════════════════════════════════════════════════════════════
class _FakeClip:
    image_size = 32
    mean = OPENAI_MEAN
    std = OPENAI_STD
    logit_scale = 100.0
    embedding_dim = 512
    precision = "fp32"
    precision_bench: Dict = {}

    def __init__(self, model_name, pretrained, device):
        pass

    def encode_images(self, batch):
        n = len(batch)
        small = batch.reshape(n, 3, 8, 4, 8, 4).mean(axis=(3, 5)).reshape(n, -1)
        v = np.zeros((n, 512), dtype=np.float32)
        v[:, :192] = small
        v[:, 192] = 3.0
        return v / np.linalg.norm(v, axis=1, keepdims=True)

    def encode_texts(self, prompts):
        out = []
        for p in prompts:
            rng = np.random.default_rng(abs(hash(p)) % (2 ** 32))
            t = rng.standard_normal(512).astype(np.float32)
            out.append(t / np.linalg.norm(t))
        return np.stack(out)


# ═════════════════════════════════════════════════════════════════════
# 相片庫
# ═════════════════════════════════════════════════════════════════════
def norm(p) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


class Library:
    def __init__(self, root: Path, exp: Dict):
        self.root = Path(root).resolve()
        self.exp = exp
        self.synthetic = exp["mode"] == "synthetic"

    def path(self, rel: str) -> Path:
        return self.root / rel

    def inventory(self) -> Dict:
        """以掃描器 + 配對引擎計算這個相片庫「應該」有的資產。"""
        assets = []
        for r in self.exp["roots"]:
            scanned = list(scan_directory(str(self.root / r["dir"]), True, r["exclude"]))
            assets += pairing_engine.pair_files(scanned)
        sizes: Dict[int, int] = {}
        for a in assets:
            sizes[a["primary_file"]["size"]] = sizes.get(a["primary_file"]["size"], 0) + 1
        return {
            "assets": len(assets),
            "files": sum(1 + len(a["paired_files"]) for a in assets),
            "photos": sum(a["media_type"] == "photo" for a in assets),
            "raws": sum(a["media_type"] == "raw" for a in assets),
            "videos": sum(a["media_type"] == "video" for a in assets),
            "hash_dup_files": sum(1 for a in assets if sizes[a["primary_file"]["size"]] > 1),
            "primary_paths": {norm(a["primary_file"]["path"]) for a in assets},
        }


@contextmanager
def preserve(*paths):
    """暫時修改檔案；結束時還原（含原修改時間）。原本不存在的檔案結束時會被刪除。"""
    bak = Path(tempfile.mkdtemp(prefix="pcbak_"))
    saved = []
    for i, p in enumerate(paths):
        p = Path(p)
        if p.exists():
            b = bak / str(i)
            shutil.copy2(p, b)
            saved.append((p, b))
        else:
            saved.append((p, None))
    try:
        yield
    finally:
        for p, b in saved:
            if b is None:
                if p.exists():
                    p.unlink()
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(b, p)
        shutil.rmtree(bak, ignore_errors=True)


# ═════════════════════════════════════════════════════════════════════
# API / DB 工具
# ═════════════════════════════════════════════════════════════════════
def get(url):
    r = client.get(url)
    assert r.status_code == 200, f"GET {url} → {r.status_code} {r.text}"
    return r.json()


def post_job(url, payload=None):
    r = client.post(url, json=payload if payload is not None else {})
    assert r.status_code == 200, f"POST {url} → {r.status_code} {r.text}"
    return get("/api/jobs/status")


def completed(st):
    assert st["stage"] == "completed", f"任務未完成：stage={st.get('stage')} label={st.get('stage_label')} " \
                                       f"error={st.get('error_message')}"
    return st


def scan(mode="full", root_id=None):
    return completed(post_job("/api/jobs/scan", {"mode": mode, "root_id": root_id}))


def analyze(stages=None, retry=False, device="auto"):
    return completed(post_job("/api/jobs/analyze",
                              {"stages": stages or ALL_STAGES, "device": device, "retry_failed": retry}))


def evaluate():
    return completed(post_job("/api/jobs/evaluate"))


def put_settings(values: Dict, re_evaluate=True):
    r = client.put("/api/settings", json={"settings": values, "re_evaluate": re_evaluate})
    assert r.status_code == 200, r.text
    return r.json()


def stage_stats():
    return get("/api/analysis/status")["stages"]


def dashboard():
    return get("/api/dashboard/stats")


def asset_by_path(path) -> Optional[Asset]:
    target = norm(path)
    with SyncSessionLocal() as s:
        for a in s.query(Asset).all():
            if norm(a.primary_path) == target:
                s.expunge(a)
                return a
    return None


def asset_id(path) -> int:
    a = asset_by_path(path)
    assert a is not None, f"資料庫找不到資產：{path}"
    return a.id


def groups() -> Dict[int, Dict]:
    """group_id → {type, keeper, members: {asset_id: primary_path}}"""
    out: Dict[int, Dict] = {}
    with SyncSessionLocal() as s:
        for g in s.query(Group).all():
            out[g.id] = {"type": g.type, "keeper": g.keeper_asset_id, "members": {}}
        rows = (s.query(Candidate.group_id, Asset.id, Asset.primary_path)
                .join(Asset, Candidate.asset_id == Asset.id)
                .filter(Candidate.group_id.isnot(None)).all())
        for gid, aid, path in rows:
            if gid in out:
                out[gid]["members"][aid] = path
    return out


def group_of(aid: int, gtype: Optional[str] = None) -> Optional[Dict]:
    for gid, g in groups().items():
        if aid in g["members"] and (gtype is None or g["type"] == gtype):
            return {"id": gid, **g}
    return None


def group_signature():
    sig = set()
    for g in groups().values():
        keeper = norm(g["members"].get(g["keeper"], "")) if g["keeper"] else ""
        sig.add((g["type"], frozenset(norm(p) for p in g["members"].values()), keeper))
    return sig


def rules_of(aid: int) -> List[str]:
    with SyncSessionLocal() as s:
        return [r for (r,) in s.query(Candidate.rule).filter(Candidate.asset_id == aid)]


def failures(stage: str) -> set:
    return {norm(f["primary_path"]) for f in get(f"/api/analysis/failures?stage={stage}")}


def check_invariants():
    """測試計畫第 2 節 N-01 ~ N-09。每個情境結束都會呼叫。"""
    d = dashboard()
    st = stage_stats()
    A = d["assets"]
    assert A["total"] == A["photo_count"] + A["raw_count"] + A["video_count"], f"N-01 {A}"
    for name, s in st.items():
        parts = s["pending"] + s["done"] + s["failed"] + s.get("not_needed", 0) + s.get("stale", 0)
        assert s["total"] == parts, f"N-02 {name}: {s}"
    assert st["basic"]["total"] == st["clip"]["total"] == A["photo_count"] + A["raw_count"], f"N-03 {st}"
    assert st["video"]["total"] == A["video_count"], f"N-03 video {st['video']}"
    assert st["hash"]["total"] == A["total"], f"N-03 hash {st['hash']} vs {A['total']}"

    with SyncSessionLocal() as s:
        active = s.query(Asset).filter(Asset.status.notin_(INACTIVE)).count()
        assert active == A["total"], f"N-01 DB active {active} vs dashboard {A['total']}"
        rows = (s.query(Candidate.asset_id, Candidate.is_keeper, Candidate.state, Candidate.group_id,
                        Asset.status, Asset.size_total)
                .join(Asset, Candidate.asset_id == Asset.id).all())
        open_nk = {r.asset_id for r in rows if not r.is_keeper and r.state == "candidate" and r.status not in INACTIVE}
        assert len(open_nk) == d["candidates"]["total"], \
            f"N-04 dashboard {d['candidates']['total']} vs DB {len(open_nk)}"
        rel = {}
        for r in rows:
            if not r.is_keeper and r.state in ("candidate", "queued") and r.status not in INACTIVE:
                rel[r.asset_id] = r.size_total
        assert sum(rel.values()) == d["candidates"]["releasable_size_bytes"], "N-06 releasable size"
        queued = s.query(Asset).filter(Asset.status == "queued").count()
        member_groups: Dict[int, set] = {}
        by_group: Dict[int, List] = {}
        for r in rows:
            if r.group_id is not None:
                member_groups.setdefault(r.asset_id, set()).add(r.group_id)
                by_group.setdefault(r.group_id, []).append(r)
    assert all(len(g) == 1 for g in member_groups.values()), "一個資產只能屬於一個群組"
    for gid, members in by_group.items():
        alive = [m for m in members if m.status not in INACTIVE]
        if alive:
            assert any(m.status != "queued" for m in alive), f"N-09 群組 {gid} 全部待刪"
    assert get("/api/process/summary")["total_count"] == queued, "N-08 process summary vs queued"
    for key, rule in [("exact_dup", "exact_dup"), ("near_dup", "near_dup"), ("burst", "burst"),
                      ("blur", "blur"), ("exposure", "exposure"), ("screenshot", "screenshot"),
                      ("document", "document"), ("meme", "meme"), ("mistouch", "mistouch")]:
        items = get(f"/api/candidates?rule={rule}&state=candidate")
        n = len([i for i in items if not i["is_keeper"]])
        assert n == d["candidates"][key], f"N-07 {rule}: review {n} vs dashboard {d['candidates'][key]}"


def _reset_tracker():
    progress_tracker.state = ProgressState()
    progress_tracker._cancel_requested = False


def _db_path() -> Path:
    return Path(settings.sqlite_db_path)


def _backup(src: Path, dst: Path):
    sync_engine.dispose()
    with closing(sqlite3.connect(str(src))) as a, closing(sqlite3.connect(str(dst))) as b:
        a.backup(b)


def _wipe():
    init_db()
    with SyncSessionLocal() as s:
        for m in (ActionLog, Candidate, Group, AnalysisFailure, ClipFeature, Feature, VideoInfo, File, Asset,
                  Root, Setting, AppState):
            s.query(m).delete()
        s.commit()


# ═════════════════════════════════════════════════════════════════════
# Fixtures
# ═════════════════════════════════════════════════════════════════════
@pytest.fixture(autouse=True)
def fake_clip():
    """覆蓋 tests/conftest.py 的同名 fixture：CLIP 後端由 _clip_backend（module scope）負責。"""
    yield None


@pytest.fixture(autouse=True)
def clean_db():
    """覆蓋 tests/conftest.py 的同名 fixture：本模組以資料庫快照切換狀態，不在每個測試清空。"""
    _reset_tracker()
    yield
    _reset_tracker()


@pytest.fixture(scope="module")
def _clip_backend():
    if REAL_CLIP:
        clip_extractor.set_backend_factory(None, cuda_available=None)
    else:
        clip_extractor.set_backend_factory(_FakeClip, cuda_available=False)
    yield
    clip_extractor.set_backend_factory(None, cuda_available=None)


@pytest.fixture(scope="module")
def lib(tmp_path_factory) -> Library:
    if LIB_ENV:
        root = Path(LIB_ENV)
        exp_file = root / "expected.json"
        assert exp_file.exists(), f"{root} 不是測試相片庫（找不到 expected.json），請先執行 scripts/make_test_library.py"
        return Library(root, json.loads(exp_file.read_text(encoding="utf-8")))
    root = tmp_path_factory.mktemp("pclib") / "lib"
    return Library(root, mtl.build(root))


class States:
    """各階段的資料庫快照：empty（只加了路徑）、scanned、analysed、evaluated。"""

    def __init__(self, lib: Library, snapdir: Path):
        self.lib = lib
        self.dir = snapdir
        self.files: Dict[str, Path] = {}
        self.stats: Dict[str, Dict] = {}
        self.root_ids: Dict[str, int] = {}
        self.inventory: Dict = {}
        self.analyse_job: Dict = {}

    def save(self, name):
        f = self.dir / f"{name}.db"
        _backup(_db_path(), f)
        self.files[name] = f
        self.stats[name] = {"dashboard": dashboard(), "analysis": stage_stats(), "groups": group_signature()}

    def use(self, name):
        _backup(self.files[name], _db_path())
        _reset_tracker()


@pytest.fixture(scope="module")
def states(lib, _clip_backend, tmp_path_factory) -> States:
    _wipe()
    _reset_tracker()
    S = States(lib, tmp_path_factory.mktemp("pcsnap"))
    S.inventory = lib.inventory()
    for r in lib.exp["roots"]:
        res = client.post("/api/roots", json={"path": str(lib.root / r["dir"]), "recursive": True,
                                              "exclude_patterns": r["exclude"], "priority": r["priority"]})
        assert res.status_code == 200, res.text
        S.root_ids[r["name"]] = res.json()["id"]
    S.save("empty")
    t = time.time()
    scan()
    t_scan = time.time() - t
    S.save("scanned")
    t = time.time()
    S.analyse_job = analyze()
    t_analyse = time.time() - t
    S.save("analysed")
    t = time.time()
    evaluate()
    t_eval = time.time() - t
    S.save("evaluated")
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": lib.exp["mode"], "real_clip": REAL_CLIP, "library": str(lib.root),
        "inventory": {k: v for k, v in S.inventory.items() if k != "primary_paths"},
        "seconds": {"scan": round(t_scan, 1), "analyse": round(t_analyse, 1), "evaluate": round(t_eval, 1)},
        "analyse_job": S.analyse_job.get("stage_stats"),
        "analysis": S.stats["analysed"]["analysis"],
        "dashboard": S.stats["evaluated"]["dashboard"],
        "groups": {t_: sum(1 for g in S.stats["evaluated"]["groups"] if g[0] == t_)
                   for t_ in ("exact", "near", "burst", "video_size")},
    }
    try:
        (lib.root / "functional_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                                          encoding="utf-8")
    except OSError:
        pass
    print("\n[functional] baseline:", json.dumps(report, ensure_ascii=False))
    return S


# ═════════════════════════════════════════════════════════════════════
# 基準（A-01 / E-01 / P-05）
# ═════════════════════════════════════════════════════════════════════
class TestBaseline:
    def test_A01_scan_and_analysis_numbers(self, states, lib):
        states.use("analysed")
        inv = states.inventory
        d = dashboard()["assets"]
        st = stage_stats()
        assert d["total"] == inv["assets"]
        assert (d["photo_count"], d["raw_count"], d["video_count"]) == (inv["photos"], inv["raws"], inv["videos"])
        with SyncSessionLocal() as s:
            assert s.query(File).count() == inv["files"]
            db_paths = {norm(a.primary_path) for a in s.query(Asset).all()}
        assert db_paths == inv["primary_paths"]
        # 排除資料夾
        for rel in lib.exp["excluded"]:
            assert asset_by_path(lib.path(rel)) is None, f"排除的檔案被掃描進來：{rel}"
        # Live Photo：一個資產、兩個檔案、MOV 不是獨立資產
        lp = lib.exp["live_pair"]
        a = asset_by_path(lib.path(lp["primary"]))
        assert a is not None and a.media_type == "photo"
        assert [norm(p) for p in a.paired_paths] == [norm(lib.path(lp["pair"]))]
        assert asset_by_path(lib.path(lp["pair"])) is None
        # 雜湊：大小重複的主檔都算完
        assert st["hash"]["done"] == inv["hash_dup_files"], st["hash"]
        assert st["hash"]["pending"] == 0
        # 所有階段都沒有待處理
        for name in ALL_STAGES:
            assert st[name]["pending"] == 0, f"{name} 仍有待處理：{st[name]}"
        # 壞圖 / 壞影片一定在失敗清單
        bad = {norm(lib.path(r)) for r in lib.exp["bad_images"]}
        assert bad <= failures("basic"), "壞圖應記錄為 basic 失敗"
        assert bad <= failures("clip"), "壞圖應記錄為 clip 失敗"
        assert {norm(lib.path(r)) for r in lib.exp["broken_videos"]} <= failures("video")
        for rel in lib.exp["ok_videos"]:
            with SyncSessionLocal() as s:
                assert s.query(VideoInfo).filter(VideoInfo.asset_id == asset_id(lib.path(rel))).count() == 1
        # 中文 / emoji 路徑：完成 basic 且有縮圖
        for rel in lib.exp["unicode"]:
            with SyncSessionLocal() as s:
                a = s.query(Asset).filter(Asset.id == asset_id(lib.path(rel))).one()
                assert a.features is not None and a.features.decode_status == "ok"
                assert thumbnail_service.has_thumbnail(**thumb_key_args(a))
        if lib.synthetic:
            c = lib.exp["counts"]
            assert d["total"] == c["assets"] and d["video_count"] == c["videos"]
            assert st["basic"]["failed"] == c["basic_failed"]
            assert st["clip"]["failed"] == c["clip_failed"]
            assert st["video"]["failed"] == c["video_failed"] and st["video"]["done"] == c["video_done"]
            assert len(get("/api/analysis/failures")) == c["basic_failed"] + c["clip_failed"] + c["video_failed"]
        check_invariants()

    def test_E01_candidates_match_known_answers(self, states, lib):
        states.use("evaluated")
        # 完全重複：注入的每一組都在同一個 exact 群組，Keeper 不在優先度較低的 B
        for paths in lib.exp["exact_groups"]:
            ids = [asset_id(lib.path(p)) for p in paths]
            g = group_of(ids[0], "exact")
            assert g is not None, f"完全重複未成組：{paths}"
            assert set(ids) <= set(g["members"]), f"完全重複成員不齊：{paths}"
            keeper_path = g["members"][g["keeper"]]
            assert norm(keeper_path).startswith(norm(lib.root / "A")), f"Keeper 應在優先度較高的 A：{keeper_path}"
        # 近似：兩個變體同一群組；原圖在同一群或在完全重複群組
        for ng in lib.exp["near_groups"]:
            v = [asset_id(lib.path(p)) for p in ng["variants"]]
            g = group_of(v[0], "near")
            assert g is not None and set(v) <= set(g["members"]), f"近似變體未同組：{ng}"
            orig = asset_id(lib.path(ng["original"]))
            assert orig in g["members"] or group_of(orig, "exact") is not None, f"原圖未入組：{ng}"
        # 畫質
        for rel in lib.exp["blurry"]:
            assert "blur" in rules_of(asset_id(lib.path(rel)))
        for rel in lib.exp["dark"]:
            assert "under_exposure" in rules_of(asset_id(lib.path(rel)))
        for rel in lib.exp["bright"]:
            assert "over_exposure" in rules_of(asset_id(lib.path(rel)))
        # 壞圖不會成為任何候選
        for rel in lib.exp["bad_images"]:
            assert rules_of(asset_id(lib.path(rel))) == [], f"壞圖不應有候選：{rel}"
        if lib.synthetic:
            c = lib.exp["counts"]
            dc = dashboard()["candidates"]
            assert dc["exact_dup"] == c["exact_dup_non_keepers"]
            assert dc["near_dup"] == c["near_dup_non_keepers"]
            assert dc["blur"] == c["blur"]
            assert dc["exposure"] == c["under_exposure"] + c["over_exposure"]
            assert dc["burst"] == 0
        check_invariants()

    def test_P05_evaluate_is_idempotent(self, states):
        states.use("evaluated")
        before = (group_signature(), dashboard()["candidates"])
        evaluate()
        assert (group_signature(), dashboard()["candidates"]) == before
        check_invariants()


# ═════════════════════════════════════════════════════════════════════
# R – 路徑管理
# ═════════════════════════════════════════════════════════════════════
class TestRoots:
    def test_R02_duplicate_path_rejected(self, states, lib):
        states.use("empty")
        for p in (str(lib.root / "A") + os.sep, str(lib.root / "A").upper() if os.name == "nt" else str(lib.root / "A")):
            r = client.post("/api/roots", json={"path": p})
            assert r.status_code == 400 and "已在掃描清單" in r.json()["detail"], r.text

    def test_R03_child_of_recursive_root_rejected(self, states, lib):
        states.use("empty")
        r = client.post("/api/roots", json={"path": str(lib.root / "A" / "dup")})
        assert r.status_code == 400 and "涵蓋" in r.json()["detail"]

    def test_R04_parent_of_existing_root_rejected(self, states, lib):
        states.use("empty")
        r = client.post("/api/roots", json={"path": str(lib.root), "recursive": True})
        assert r.status_code == 400 and "子目錄" in r.json()["detail"]

    def test_R05_invalid_paths_rejected(self, states, lib, tmp_path):
        states.use("empty")
        assert client.post("/api/roots", json={"path": str(tmp_path / "no_such_dir")}).status_code == 400
        f = tmp_path / "file.txt"
        f.write_text("x")
        assert client.post("/api/roots", json={"path": str(f)}).status_code == 400
        assert client.post("/api/roots", json={"path": "   "}).status_code == 400

    def test_R06_R07_remove_root_updates_groups(self, states, lib):
        states.use("evaluated")
        b_id = states.root_ids["B"]
        b_files = [lib.path(p) for g in lib.exp["exact_groups"] for p in g if p.startswith("B/")]
        r = client.delete(f"/api/roots/{b_id}")
        assert r.status_code == 200
        assert all(p.exists() for p in b_files), "移除路徑不可刪除實體檔案"
        with SyncSessionLocal() as s:
            assert s.query(Asset).filter(Asset.root_id == b_id).count() == 0
        evaluate()
        g0, g1 = lib.exp["exact_groups"]
        grp = group_of(asset_id(lib.path(g0[0])), "exact")
        assert grp is not None and len(grp["members"]) == len([p for p in g0 if not p.startswith("B/")])
        assert "exact_dup" not in rules_of(asset_id(lib.path(g1[0]))), "只剩一張時不應再是完全重複"
        assert dashboard()["assets"]["total"] == states.inventory["assets"] - len(b_files)
        check_invariants()


# ═════════════════════════════════════════════════════════════════════
# S – 掃描
# ═════════════════════════════════════════════════════════════════════
class TestScan:
    def test_S02_rescan_unchanged_keeps_analysis(self, states):
        states.use("analysed")
        st = scan()
        assert st["scan_summary"] == {"added": 0, "modified": 0, "missing": 0,
                                      "unchanged": states.inventory["assets"]}
        assert stage_stats() == states.stats["analysed"]["analysis"]
        check_invariants()

    def test_S03_new_files_only_new_items_pending(self, states, lib):
        states.use("analysed")
        n1, n2 = lib.path("A/new/new_1.jpg"), lib.path("A/new/new_2.jpg")
        with preserve(n1, n2):
            mtl.save_jpg(mtl.texture(424242), n1)
            mtl.save_jpg(mtl.texture(434343), n2)
            assert scan()["scan_summary"]["added"] == 2
            st = stage_stats()
            assert st["basic"]["pending"] == 2 and st["clip"]["pending"] == 2 and st["hash"]["pending"] == 2
            assert st["video"]["pending"] == 0
            res = analyze()["stage_stats"]
            assert res["basic"]["processed"] == 2 and res["clip"]["processed"] == 2
            st = stage_stats()
            assert all(st[k]["pending"] == 0 for k in ALL_STAGES)
            base = states.stats["analysed"]["analysis"]
            assert st["basic"]["done"] == base["basic"]["done"] + 2
            check_invariants()

    def _edit_target(self, lib) -> Path:
        return lib.path(lib.exp["unicode"][0])

    def test_S04_modified_file_full_scan_resets_analysis(self, states, lib):
        states.use("evaluated")
        p = self._edit_target(lib)
        aid = asset_id(p)
        with preserve(p):
            mtl.save_jpg(mtl.texture(515151, 640, 480), p)
            st = scan()
            assert st["scan_summary"]["modified"] == 1
            with SyncSessionLocal() as s:
                assert s.query(Feature).filter(Feature.asset_id == aid).count() == 0
                assert s.query(ClipFeature).filter(ClipFeature.asset_id == aid).count() == 0
            ss = stage_stats()
            assert ss["basic"]["pending"] == 1 and ss["clip"]["pending"] == 1
            analyze()
            assert stage_stats()["basic"]["pending"] == 0
            check_invariants()

    def test_S05_modified_file_new_only_keeps_analysis(self, states, lib):
        states.use("analysed")
        p = self._edit_target(lib)
        with preserve(p):
            mtl.save_jpg(mtl.texture(525252, 640, 480), p)
            st = scan("new_only")
            assert st["scan_summary"]["modified"] == 0 and st["scan_summary"]["added"] == 0
            assert stage_stats() == states.stats["analysed"]["analysis"]
            check_invariants()

    def test_S06_S07_deleted_outside_then_restored(self, states, lib):
        states.use("evaluated")
        g0 = lib.exp["exact_groups"][0]
        grp = group_of(asset_id(lib.path(g0[0])), "exact")
        keeper_path = Path(grp["members"][grp["keeper"]])
        with preserve(keeper_path):
            keeper_path.unlink()
            st = scan()
            assert st["scan_summary"]["missing"] == 1
            assert dashboard()["assets"]["total"] == states.inventory["assets"] - 1
            evaluate()
            kid = asset_by_path(keeper_path).id
            assert rules_of(kid) == [], "遺失的資產不應有候選"
            new_grp = group_of(asset_id(lib.path(g0[1] if norm(lib.path(g0[0])) == norm(keeper_path) else g0[0])),
                               "exact")
            assert new_grp is not None and new_grp["keeper"] != kid, "遺失的檔案不可成為 Keeper"
            check_invariants()
        # 還原後重新掃描：復活為新資產
        st = scan()
        assert st["scan_summary"]["added"] == 1
        a = asset_by_path(keeper_path)
        assert a.status == "unanalysed"
        analyze()
        evaluate()
        check_invariants()

    def test_S10_scan_single_root(self, states, lib):
        states.use("analysed")
        with SyncSessionLocal() as s:
            before = {r.id: r.last_scan_at for r in s.query(Root).all()}
        time.sleep(0.01)
        scan(root_id=states.root_ids["B"])
        with SyncSessionLocal() as s:
            after = {r.id: r.last_scan_at for r in s.query(Root).all()}
        assert after[states.root_ids["A"]] == before[states.root_ids["A"]]
        assert after[states.root_ids["B"]] != before[states.root_ids["B"]]
        check_invariants()

    def test_S11_offline_root_untouched(self, states, lib):
        states.use("analysed")
        b, off = lib.root / "B", lib.root / "B_offline"
        b_id = states.root_ids["B"]
        with SyncSessionLocal() as s:
            n_b = s.query(Asset).filter(Asset.root_id == b_id).count()
        os.rename(b, off)
        try:
            scan()
            with SyncSessionLocal() as s:
                assert s.get(Root, b_id).available is False
                statuses = [a.status for a in s.query(Asset).filter(Asset.root_id == b_id)]
            assert len(statuses) == n_b and "missing" not in statuses
        finally:
            os.rename(off, b)
        scan()
        with SyncSessionLocal() as s:
            assert s.get(Root, b_id).available is True
        check_invariants()


# ═════════════════════════════════════════════════════════════════════
# A – 分析
# ═════════════════════════════════════════════════════════════════════
class TestAnalysis:
    def test_A02_stage_by_stage_equals_all_at_once(self, states):
        states.use("scanned")
        for stage in ALL_STAGES:
            analyze([stage])
        assert stage_stats() == states.stats["analysed"]["analysis"]
        check_invariants()

    def test_A03_clip_before_basic(self, states):
        states.use("scanned")
        analyze(["clip"])
        with SyncSessionLocal() as s:
            clip_before = {c.asset_id: c.analyzed_at for c in s.query(ClipFeature)}
        analyze(["hash", "basic", "video"])
        with SyncSessionLocal() as s:
            clip_after = {c.asset_id: c.analyzed_at for c in s.query(ClipFeature)}
        assert clip_after == clip_before, "basic 不可改動 CLIP 結果"
        assert stage_stats() == states.stats["analysed"]["analysis"]
        check_invariants()

    def test_A04_hash_does_not_decode_images(self, states):
        states.use("scanned")
        with patch("PIL.Image.open") as mock_open:
            analyze(["hash"])
        mock_open.assert_not_called()
        assert stage_stats()["hash"]["pending"] == 0

    def test_A05_rerun_with_nothing_pending(self, states):
        states.use("analysed")
        res = analyze()["stage_stats"]
        for k in ALL_STAGES:
            assert res[k]["processed"] == 0, f"{k} 不應重新處理：{res[k]}"
        assert stage_stats() == states.stats["analysed"]["analysis"]

    def test_A06_gpu_only_without_cuda_fails_cleanly(self, states):
        if clip_extractor.cuda_available:
            pytest.skip("這台電腦有 CUDA")
        states.use("scanned")
        st = post_job("/api/jobs/analyze", {"stages": ALL_STAGES, "device": "cuda"})
        assert st["stage"] == "error" and st["error_message"], st
        assert stage_stats() == states.stats["scanned"]["analysis"], "失敗的任務不可改動資料"

    def test_A08_model_change_marks_clip_stale(self, states):
        states.use("analysed")
        done = states.stats["analysed"]["analysis"]["clip"]["done"]
        put_settings({"clip_model": "ViT-B-32"}, re_evaluate=False)
        c = stage_stats()["clip"]
        assert c["stale"] == done and c["done"] == 0
        check_invariants()

    def test_A09_runner_setting_persists(self, states):
        states.use("analysed")
        put_settings({"basic_runners": 3}, re_evaluate=False)
        assert get("/api/analysis/status")["runners"]["basic"] == 3

    def test_A10_rebuild_missing_thumbnails_only(self, states):
        states.use("analysed")
        with SyncSessionLocal() as s:
            assets = (s.query(Asset).join(Feature, Feature.asset_id == Asset.id)
                      .filter(Feature.decode_status == "ok").limit(3).all())
            targets = [(a.id, thumbnail_service.get_thumbnail_path(**thumb_key_args(a))) for a in assets]
            feats = {f.asset_id: f.analyzed_at for f in s.query(Feature)}
        for _, t in targets:
            t.unlink(missing_ok=True)
        completed(post_job("/api/jobs/rebuild_thumbs"))
        assert all(t.exists() for _, t in targets)
        with SyncSessionLocal() as s:
            assert {f.asset_id: f.analyzed_at for f in s.query(Feature)} == feats


# ═════════════════════════════════════════════════════════════════════
# P – 重試 / 重新處理
# ═════════════════════════════════════════════════════════════════════
class TestRetry:
    def test_P01_retry_still_broken_stays_failed(self, states):
        states.use("analysed")
        base = states.stats["analysed"]["analysis"]
        analyze(retry=True)
        st = stage_stats()
        for k in ALL_STAGES:
            assert st[k]["failed"] == base[k]["failed"], f"{k}: {st[k]} vs {base[k]}"
        check_invariants()

    def test_P02_fixed_file_recovers(self, states, lib):
        states.use("analysed")
        p = lib.path("A/bad/corrupt.jpg")
        base = states.stats["analysed"]["analysis"]
        with preserve(p):
            mtl.save_jpg(mtl.texture(616161), p)
            assert scan()["scan_summary"]["modified"] == 1
            analyze()
            st = stage_stats()
            assert st["basic"]["failed"] == base["basic"]["failed"] - 1
            assert st["basic"]["done"] == base["basic"]["done"] + 1
            assert st["clip"]["failed"] == base["clip"]["failed"] - 1
            assert norm(p) not in failures("basic")
            check_invariants()

    def test_P03_normal_run_skips_failed(self, states):
        states.use("analysed")
        res = analyze()["stage_stats"]
        assert res["clip"]["skipped_failed"] == states.stats["analysed"]["analysis"]["clip"]["failed"]
        assert res["basic"]["processed"] == 0

    def test_P04_fixed_video_recovers(self, states, lib):
        if not lib.exp["ok_videos"]:
            pytest.skip("此電腦無法產生測試影片")
        states.use("analysed")
        broken = lib.path(lib.exp["broken_videos"][0])
        with preserve(broken):
            shutil.copyfile(lib.path(lib.exp["ok_videos"][0]), broken)
            scan()
            analyze(["video"])
            assert norm(broken) not in failures("video")
            check_invariants()


# ═════════════════════════════════════════════════════════════════════
# I – 中斷與續跑
# ═════════════════════════════════════════════════════════════════════
def _cancel_when(monkeypatch, method: str, threshold: int):
    orig = getattr(progress_tracker, method)

    def hook(processed, *args, **kwargs):
        if processed >= threshold:
            progress_tracker.request_cancel()
        return orig(processed, *args, **kwargs)

    monkeypatch.setattr(progress_tracker, method, hook)


def _assert_cancelled():
    st = get("/api/jobs/status")
    assert st["is_running"] is False and st["stage"] == "idle" and st["stage_label"] == "已取消", st


class TestInterrupt:
    def test_I01_cancel_scan_then_resume(self, states, monkeypatch):
        states.use("empty")
        _cancel_when(monkeypatch, "update_progress", 10)
        post_job("/api/jobs/scan", {"mode": "full"})
        _assert_cancelled()
        assert dashboard()["assets"]["total"] < states.inventory["assets"]
        monkeypatch.undo()
        scan()
        assert dashboard()["assets"]["total"] == states.inventory["assets"]
        check_invariants()

    def test_I02_cancel_basic_then_resume(self, states, monkeypatch):
        states.use("scanned")
        total = states.stats["scanned"]["analysis"]["basic"]["total"]
        _cancel_when(monkeypatch, "update_analyze_progress", max(1, total // 3))
        post_job("/api/jobs/analyze", {"stages": ["basic"]})
        _assert_cancelled()
        b = stage_stats()["basic"]
        assert 0 < b["done"] + b["failed"] < total, f"應只完成部分：{b}"
        monkeypatch.undo()
        analyze(["basic"])
        assert stage_stats()["basic"] == states.stats["analysed"]["analysis"]["basic"]
        check_invariants()

    def test_I03_cancel_clip_then_resume(self, states, monkeypatch):
        states.use("scanned")
        analyze(["basic"])
        total = stage_stats()["clip"]["total"]
        _cancel_when(monkeypatch, "update_analyze_progress", max(1, total // 3))
        post_job("/api/jobs/analyze", {"stages": ["clip"]})
        _assert_cancelled()
        c = stage_stats()["clip"]
        if total >= 300:   # 數量太少時一批就做完，無法驗證「部分完成」
            assert c["done"] + c["failed"] < total, f"應只完成部分：{c}"
        monkeypatch.undo()
        analyze(["clip"])
        assert stage_stats()["clip"] == states.stats["analysed"]["analysis"]["clip"]
        check_invariants()

    def test_I04_new_job_refused_until_cancel_finishes(self, states):
        states.use("analysed")
        assert progress_tracker.try_start_analyze(["basic"])
        progress_tracker.request_cancel()
        assert client.post("/api/jobs/scan", json={"mode": "full"}).status_code == 400
        progress_tracker.ensure_finished()
        scan()

    def test_I09_cancel_during_evaluate_reports_truthfully(self, states, monkeypatch):
        states.use("analysed")
        orig = rules_engine._evaluate_quality_rules

        def hook(*a, **k):
            progress_tracker.request_cancel()
            return orig(*a, **k)

        monkeypatch.setattr(rules_engine, "_evaluate_quality_rules", hook)
        st = post_job("/api/jobs/evaluate")
        assert dashboard()["candidates"]["total"] > 0
        assert st["stage"] == "completed", st


# ═════════════════════════════════════════════════════════════════════
# C – 並行
# ═════════════════════════════════════════════════════════════════════
class TestConcurrency:
    def test_C01_C02_C04_everything_refused_while_busy(self, states):
        states.use("evaluated")
        assert progress_tracker.try_start_analyze(["basic"])
        try:
            assert client.post("/api/jobs/scan", json={"mode": "full"}).status_code == 400
            assert client.post("/api/jobs/analyze", json={"stages": ["basic"]}).status_code == 400
            assert client.post("/api/jobs/evaluate").status_code == 400
            assert client.post("/api/jobs/rebuild_thumbs").status_code == 400
            assert client.post("/api/scan/all").status_code == 400
            assert client.post("/api/candidates/evaluate").status_code == 409
            assert client.post("/api/process/execute", json=PERMANENT).status_code == 409
        finally:
            progress_tracker.ensure_finished()

    def test_C03_settings_not_half_applied_while_busy(self, states):
        states.use("evaluated")
        before = get("/api/settings")
        assert progress_tracker.try_start_analyze(["basic"])
        try:
            r = client.put("/api/settings", json={"settings": {"blur_threshold": 555.0}, "re_evaluate": True})
            assert r.status_code == 409
        finally:
            progress_tracker.ensure_finished()
        assert get("/api/settings") == before

    def test_C03b_reset_not_applied_while_busy(self, states):
        states.use("evaluated")
        put_settings({"blur_threshold": 444.0}, re_evaluate=False)
        assert progress_tracker.try_start_analyze(["basic"])
        try:
            assert client.post("/api/settings/reset").status_code == 409
        finally:
            progress_tracker.ensure_finished()
        assert get("/api/settings")["blur_threshold"] == 444.0


# ═════════════════════════════════════════════════════════════════════
# E – 規則評估
# ═════════════════════════════════════════════════════════════════════
class TestEvaluate:
    def test_E02_evaluate_before_analysis(self, states):
        states.use("scanned")
        evaluate()
        assert dashboard()["candidates"]["total"] == 0 and groups() == {}
        check_invariants()

    def test_E03_user_decisions_survive_reevaluate(self, states, lib):
        states.use("evaluated")
        q = asset_id(lib.path(lib.exp["blurry"][0]))
        k = asset_id(lib.path(lib.exp["dark"][0]))
        assert client.post("/api/candidates/batch-status", json={"asset_ids": [q], "state": "queued"}).json()["updated_count"] == 1
        assert client.post("/api/candidates/batch-status", json={"asset_ids": [k], "state": "kept"}).json()["updated_count"] == 1
        evaluate()
        with SyncSessionLocal() as s:
            assert {c.state for c in s.query(Candidate).filter(Candidate.asset_id == q)} == {"queued"}
            assert {c.state for c in s.query(Candidate).filter(Candidate.asset_id == k)} == {"kept"}
            assert s.get(Asset, q).status == "queued"
        check_invariants()

    def test_E04_new_copy_joins_existing_group(self, states, lib):
        states.use("evaluated")
        g1 = lib.exp["exact_groups"][1]
        b_copy = asset_id(lib.path(g1[1]))
        client.post("/api/candidates/batch-status", json={"asset_ids": [b_copy], "state": "queued"})
        src = lib.path(g1[0])
        new = lib.path(f"A/new/{src.stem}_copy3{src.suffix}")
        with preserve(new):
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, new)
            scan("new_only")
            analyze()
            evaluate()
            g = group_of(asset_id(new), "exact")
            assert g is not None and {asset_id(src), b_copy, asset_id(new)} <= set(g["members"])
            with SyncSessionLocal() as s:
                assert s.get(Asset, b_copy).status == "queued"
            check_invariants()

    def test_E05_settings_change_reevaluates_without_reanalysis(self, states):
        states.use("evaluated")
        base_blur = dashboard()["candidates"]["blur"]
        put_settings({"blur_threshold": 100000.0}, re_evaluate=True)
        assert dashboard()["candidates"]["blur"] > base_blur
        assert stage_stats() == states.stats["evaluated"]["analysis"]
        check_invariants()

    @pytest.mark.parametrize("method", ["clip", "phash", "both", "either"])
    def test_E06_near_dup_methods(self, states, lib, method):
        states.use("evaluated")
        put_settings({"near_dup_method": method}, re_evaluate=True)
        if method == "either":
            for ng in lib.exp["near_groups"]:
                v = [asset_id(lib.path(p)) for p in ng["variants"]]
                g = group_of(v[0], "near")
                assert g is not None and set(v) <= set(g["members"])
        check_invariants()

    def test_E07_all_rules_off_then_reset(self, states):
        states.use("evaluated")
        keys = [k for k in get("/api/settings") if k.startswith("rule_") and k.endswith("_enabled")]
        put_settings({k: False for k in keys}, re_evaluate=True)
        assert dashboard()["candidates"]["total"] == 0 and groups() == {}
        assert client.post("/api/settings/reset").status_code == 200
        assert dashboard()["candidates"] == states.stats["evaluated"]["dashboard"]["candidates"]
        check_invariants()


# ═════════════════════════════════════════════════════════════════════
# V – 審核（Keeper 保護）
# ═════════════════════════════════════════════════════════════════════
class TestReview:
    def _exact_ids(self, lib, idx=0):
        return [asset_id(lib.path(p)) for p in lib.exp["exact_groups"][idx]]

    def test_V02_queue_whole_group_keeps_one(self, states, lib):
        states.use("evaluated")
        ids = self._exact_ids(lib)
        items = [i for i in get("/api/candidates?rule=exact_dup") if i["asset_id"] in ids]
        r = client.post("/api/candidates/batch-status",
                        json={"candidate_ids": [i["id"] for i in items], "state": "queued"}).json()
        assert r["protected_count"] == 1 and r["updated_count"] == len(ids) - 1
        check_invariants()

    def test_V03_keeper_first_then_rest(self, states, lib):
        states.use("evaluated")
        g = group_of(self._exact_ids(lib)[0], "exact")
        keeper = g["keeper"]
        others = [a for a in g["members"] if a != keeper]
        r1 = client.post("/api/candidates/batch-status", json={"asset_ids": [keeper], "state": "queued"}).json()
        assert r1["updated_count"] == 1
        r2 = client.post("/api/candidates/batch-status", json={"asset_ids": others, "state": "queued"}).json()
        assert r2["protected_count"] == 1 and r2["updated_count"] == len(others) - 1
        check_invariants()

    def test_V06_tabs_move_items(self, states, lib):
        states.use("evaluated")
        aid = asset_id(lib.path(lib.exp["blurry"][0]))
        client.post("/api/candidates/batch-status", json={"asset_ids": [aid], "state": "queued"})
        assert aid in {i["asset_id"] for i in get("/api/candidates?state=queued")}
        assert aid not in {i["asset_id"] for i in get("/api/candidates?state=candidate")}
        client.post("/api/candidates/batch-status", json={"asset_ids": [aid], "state": "kept"})
        assert aid in {i["asset_id"] for i in get("/api/candidates?state=kept")}
        client.post("/api/candidates/batch-status", json={"asset_ids": [aid], "state": "candidate"})
        assert aid in {i["asset_id"] for i in get("/api/candidates?state=candidate")}
        check_invariants()


# ═════════════════════════════════════════════════════════════════════
# D / H – 刪除與歷史
# ═════════════════════════════════════════════════════════════════════
def _queue(*ids):
    r = client.post("/api/candidates/batch-status", json={"asset_ids": list(ids), "state": "queued"}).json()
    assert r["updated_count"] == len(ids), r


class TestProcess:
    def test_D01_summary(self, states, lib):
        states.use("evaluated")
        lp = asset_by_path(lib.path(lib.exp["live_pair"]["primary"]))
        bl = asset_by_path(lib.path(lib.exp["blurry"][0]))
        _queue(lp.id, bl.id)
        s = get("/api/process/summary")
        assert s["total_count"] == 2 and s["total_size_bytes"] == lp.size_total + bl.size_total
        item = next(i for i in s["items"] if i["asset_id"] == lp.id)
        assert [norm(p) for p in item["paired_paths"]] == [norm(lib.path(lib.exp["live_pair"]["pair"]))]
        check_invariants()

    def test_D02_confirmation_required(self, states):
        states.use("evaluated")
        assert client.post("/api/process/execute", json={"confirmed": False}).status_code == 400
        assert client.post("/api/process/execute", json={"confirmed": True, "mode": "permanent"}).status_code == 400

    def test_D03_H01_H02_trash_pair_and_history(self, states, lib):
        states.use("evaluated")
        prim, pair = lib.path(lib.exp["live_pair"]["primary"]), lib.path(lib.exp["live_pair"]["pair"])
        a = asset_by_path(prim)
        before = dashboard()
        with preserve(prim, pair):
            _queue(a.id)
            r = client.post("/api/process/execute", json={"confirmed": True, "mode": "trash"})
            assert r.status_code == 200, r.text
            res = r.json()
            assert res["success_count"] == 1 and res["failed_count"] == 0, res
            assert not prim.exists() and not pair.exists(), "配對的兩個檔案都要移到回收筒"
            batch = next(b for b in get("/api/history/batches") if b["batch_id"] == res["batch_id"])
            assert batch["total_files"] == 2 and batch["mode"] == "trash"
            details = get(f"/api/history/batches/{res['batch_id']}")
            assert {norm(d["original_path"]) for d in details} == {norm(prim), norm(pair)}
            after = dashboard()
            assert after["assets"]["total"] == before["assets"]["total"] - 1
            assert after["history"]["deleted_size_bytes"] == before["history"]["deleted_size_bytes"] + a.size_total
            assert after["history"]["deleted_batches"] == before["history"]["deleted_batches"] + 1
            check_invariants()

    def test_D04_modified_file_refused(self, states, lib):
        states.use("evaluated")
        p = lib.path(lib.exp["blurry"][0])
        with preserve(p):
            _queue(asset_id(p))
            with open(p, "ab") as f:
                f.write(b"\x00" * 64)
            res = client.post("/api/process/execute", json=PERMANENT).json()
            assert res["success_count"] == 0 and res["failed_count"] == 1
            assert "修改" in res["failed_items"][0]["reason"] and p.exists()

    def test_D05_missing_file_refused(self, states, lib):
        states.use("evaluated")
        p = lib.path(lib.exp["dark"][0])
        with preserve(p):
            _queue(asset_id(p))
            p.unlink()
            res = client.post("/api/process/execute", json=PERMANENT).json()
            assert res["failed_count"] == 1 and "不存在" in res["failed_items"][0]["reason"]

    def test_D06_whole_group_forced_queued_keeps_one(self, states, lib):
        states.use("evaluated")
        paths = [lib.path(p) for p in lib.exp["exact_groups"][1]]
        ids = [asset_id(p) for p in paths]
        with preserve(*paths):
            with SyncSessionLocal() as s:   # 繞過 API，模擬舊資料或其他路徑造成整組待刪
                s.query(Asset).filter(Asset.id.in_(ids)).update({"status": "queued"}, synchronize_session=False)
                s.query(Candidate).filter(Candidate.asset_id.in_(ids)).update({"state": "queued"},
                                                                              synchronize_session=False)
                s.commit()
            res = client.post("/api/process/execute", json=PERMANENT).json()
            assert res["protected_count"] == 1 and res["success_count"] == len(ids) - 1, res
            assert sum(p.exists() for p in paths) == 1
            check_invariants()

    def test_D07_unqueue(self, states, lib):
        states.use("evaluated")
        aid = asset_id(lib.path(lib.exp["blurry"][0]))
        _queue(aid)
        r = client.post("/api/process/unqueue?target_state=candidate", json=[aid])
        assert r.status_code == 200
        with SyncSessionLocal() as s:
            assert s.get(Asset, aid).status == "candidate"
        assert get("/api/process/summary")["total_count"] == 0
        check_invariants()


# ═════════════════════════════════════════════════════════════════════
# T – 設定
# ═════════════════════════════════════════════════════════════════════
class TestSettings:
    def test_T01_T02_valid_saved_invalid_rejected(self, states):
        states.use("evaluated")
        put_settings({"blur_threshold": 123.0}, re_evaluate=False)
        before = get("/api/settings")
        assert before["blur_threshold"] == 123.0
        r = client.put("/api/settings", json={"settings": {"blur_threshold": 50.0, "phash_threshold": 99},
                                              "re_evaluate": False})
        assert r.status_code == 422
        assert get("/api/settings") == before, "驗證失敗時不可寫入任何設定"


# ═════════════════════════════════════════════════════════════════════
# W – v0.6：評估狀態追蹤、一鍵流水線、流程狀態 / 主要分類
# ═════════════════════════════════════════════════════════════════════
def workflow():
    return get("/api/workflow/status")


class TestWorkflow:
    def test_W01_breakdown_sums_to_candidate_total(self, states):
        states.use("evaluated")
        w = workflow()
        assert w["breakdown_total"] == dashboard()["candidates"]["total"] == w["review"]["candidate"]["count"]
        assert sum(b["count"] for b in w["breakdown"]) == w["breakdown_total"]
        open_size = sum(b["size_bytes"] for b in w["breakdown"])
        assert open_size == w["review"]["candidate"]["size_bytes"]
        assert w["review"]["candidate"]["size_bytes"] + w["review"]["queued"]["size_bytes"] == \
            dashboard()["candidates"]["releasable_size_bytes"]
        check_invariants()

    def test_W02_breakdown_uses_priority(self, states, lib):
        states.use("evaluated")
        g0 = lib.exp["exact_groups"][0]
        w = {b["key"]: b["count"] for b in workflow()["breakdown"]}
        assert w["exact_dup"] == dashboard()["candidates"]["exact_dup"]
        # 完全重複的副本即使也命中其他規則，也只算在「完全重複」
        assert w["exact_dup"] >= len(g0) - 1

    def test_W03_stale_tracking(self, states, lib):
        states.use("analysed")
        e = workflow()["evaluation"]
        assert e["last_evaluated_at"] is None and e["is_stale"] is True and e["analysed_since"] > 0
        evaluate()
        e = workflow()["evaluation"]
        assert e["is_stale"] is False and e["analysed_since"] == 0
        assert e["last_result"]["added"] == e["last_result"]["total"] > 0
        # 審核動作不會讓評估變成過期
        aid = asset_id(lib.path(lib.exp["blurry"][0]))
        client.post("/api/candidates/batch-status", json={"asset_ids": [aid], "state": "queued"})
        assert workflow()["evaluation"]["is_stale"] is False
        # 改設定 → 過期
        put_settings({"blur_threshold": 5000.0}, re_evaluate=False)
        assert workflow()["evaluation"]["is_stale"] is True

    def test_W04_evaluate_skips_when_nothing_changed(self, states):
        states.use("evaluated")
        evaluate()                              # 狀態快照前已評估過 → 這次應略過
        st = get("/api/jobs/status")
        assert st["evaluate_result"]["skipped"] is True
        assert "略過" in st["stage_label"]
        completed(post_job("/api/jobs/evaluate", {"force": True}))
        st = get("/api/jobs/status")
        assert st["evaluate_result"]["skipped"] is False
        assert st["evaluate_result"]["added"] == 0 and st["evaluate_result"]["removed"] == 0

    def test_W05_added_removed_counts(self, states, lib):
        states.use("evaluated")
        n = lib.path("A/new/w05.jpg")
        src = lib.path(lib.exp["exact_groups"][1][0])
        with preserve(n):
            n.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, n)
            scan("new_only")
            analyze()
            e = workflow()["evaluation"]
            assert e["is_stale"] is True and e["analysed_since"] == 1
            evaluate()
            r = get("/api/jobs/status")["evaluate_result"]
            assert r["skipped"] is False and r["added"] >= 1
        check_invariants()

    def test_W06_pipeline_runs_all_steps(self, states):
        states.use("empty")
        st = completed(post_job("/api/jobs/pipeline", {"mode": "full", "device": "auto"}))
        assert st["job_type"] == "pipeline" and st["pipeline_step"] == "done"
        assert st["scan_summary"]["added"] == states.inventory["assets"]
        assert st["evaluate_result"]["total"] == dashboard()["candidates"]["total"]
        assert stage_stats() == states.stats["analysed"]["analysis"]
        assert dashboard()["candidates"] == states.stats["evaluated"]["dashboard"]["candidates"]
        assert workflow()["evaluation"]["is_stale"] is False
        check_invariants()

    def test_W07_pipeline_cancel_during_analysis(self, states, monkeypatch):
        states.use("empty")
        _cancel_when(monkeypatch, "update_analyze_progress", 3)
        post_job("/api/jobs/pipeline", {"mode": "full"})
        _assert_cancelled()
        assert dashboard()["assets"]["total"] == states.inventory["assets"]      # 掃描已完成並保留
        assert groups() == {}                                                     # 沒有執行評估
        monkeypatch.undo()
        completed(post_job("/api/jobs/pipeline", {"mode": "full"}))
        assert stage_stats() == states.stats["analysed"]["analysis"]
        check_invariants()

    def test_W08_cancel_endpoint_ignored_during_evaluate(self, states):
        states.use("analysed")
        assert progress_tracker.try_start_evaluate()
        try:
            r = client.post("/api/jobs/cancel").json()
            assert r["status"] == "ignored"
            assert progress_tracker.is_cancel_requested() is False
        finally:
            progress_tracker.ensure_finished()

    def test_W09_pipeline_refused_while_busy(self, states):
        states.use("analysed")
        assert progress_tracker.try_start_analyze(["basic"])
        try:
            assert client.post("/api/jobs/pipeline", json={}).status_code == 400
        finally:
            progress_tracker.ensure_finished()
