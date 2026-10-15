"""
v0.6.5：GET /api/candidates/preview/{asset_id} 與 GET /api/candidates/group/{group_id}（需要資料庫）。
圖片來源本身的行為（縮圖、快取、RAW 伴生檔…）在 test_preview_service.py。
"""
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.core.database import SyncSessionLocal, init_db
from app.main import app
from app.models.schema import Asset, Candidate, Feature, File, Group, Root
from app.services import preview_service

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean():
    init_db()
    with SyncSessionLocal() as s:
        for m in (Candidate, Group, Feature, File, Asset, Root):
            s.query(m).delete()
        s.commit()
    preview_service.clear_cache()
    yield
    preview_service.clear_cache()


def _img(path: Path, size=(3000, 2000), fmt="JPEG"):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (200, 80, 40)).save(path, format=fmt)
    return path


def _asset(path, media="photo", status="candidate", paired=None, size=None):
    with SyncSessionLocal() as s:
        root = s.query(Root).first()
        if root is None:
            root = Root(path="X:/root")
            s.add(root)
            s.flush()
        p = str(path)
        sz = size if size is not None else (Path(p).stat().st_size if Path(p).exists() else 1000)
        a = Asset(root_id=root.id, media_type=media, primary_path=p, size_total=sz, paired_paths=paired or [], status=status)
        s.add(a)
        s.flush()
        s.add(File(asset_id=a.id, path=p, size=sz, mtime=1000.0, format=Path(p).suffix.lstrip("."), role="primary"))
        s.commit()
        return a.id


def _get(aid, **params):
    return client.get(f"/api/candidates/preview/{aid}", params=params)


def test_routes_are_registered_and_existing_ones_remain():
    # 用 OpenAPI 的路徑清單檢查：新版 FastAPI 的 app.routes 會把 include_router 包成沒有 .path 的物件
    paths = set(app.openapi()["paths"])
    assert {"/api/candidates/preview/{asset_id}", "/api/candidates/group/{group_id}", "/api/candidates/page"} <= paths


def test_native_file_is_served_as_is_with_long_cache_only_when_versioned(tmp_path):
    f = _img(tmp_path / "a.jpg")
    aid = _asset(f)
    r = _get(aid, v="123")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/jpeg") and r.content == f.read_bytes()
    assert "immutable" in r.headers["cache-control"]
    assert "immutable" not in _get(aid).headers["cache-control"]


def test_raw_with_companion_serves_the_companion(tmp_path):
    raw = tmp_path / "IMG.cr2"
    raw.write_bytes(b"\x00raw")
    jpg = _img(tmp_path / "IMG.jpg", size=(400, 300))
    aid = _asset(raw, media="raw", paired=[str(jpg)])
    assert _get(aid).content == jpg.read_bytes()


def test_non_native_is_decoded_resized_and_cached(tmp_path, monkeypatch):
    tif = _img(tmp_path / "big.tiff", size=(6000, 4000), fmt="TIFF")
    aid = _asset(tif)
    calls = []

    def fake(src, is_raw, is_video):
        calls.append(src)
        return Image.open(src)
    monkeypatch.setattr(preview_service, "_decode", fake)
    r = _get(aid)
    out = Image.open(io.BytesIO(r.content))
    assert r.headers["content-type"] == "image/jpeg" and out.size == (2560, 1707)
    _get(aid)
    assert len(calls) == 1


def test_decode_failure_is_422_missing_file_unknown_and_deleted_are_404(tmp_path, monkeypatch):
    tif = _img(tmp_path / "bad.tif", size=(10, 10), fmt="TIFF")

    def boom(*a):
        raise ValueError("壞掉的檔案")
    monkeypatch.setattr(preview_service, "_decode", boom)
    r = _get(_asset(tif))
    assert r.status_code == 422 and "壞掉的檔案" in r.json()["detail"]
    ghost = _get(_asset(tmp_path / "ghost.jpg", size=1))
    assert ghost.status_code == 404 and "檔案不存在" in ghost.json()["detail"]
    assert _get(999999).status_code == 404
    assert _get(_asset(_img(tmp_path / "gone.jpg", size=(8, 8)), status="deleted")).status_code == 404


# ─────────────────────────────────────────────
# 群組成員
# ─────────────────────────────────────────────
def _member(d, name, group, *, rule="near_dup", score=0.96, keeper=False, state="candidate", sharp=None, size=1000,
            w=1000, h=800, status="candidate"):
    aid = _asset(d / name, size=size, status=status)
    with SyncSessionLocal() as s:
        s.add(Candidate(asset_id=aid, rule=rule, reason=f"{rule} 原因", score=score, group_id=group, is_keeper=keeper, state=state))
        s.add(Feature(asset_id=aid, width=w, height=h, blur_score=sharp, exif={}))
        s.commit()
    return aid


def _group(kind="near"):
    with SyncSessionLocal() as s:
        g = Group(type=kind)
        s.add(g)
        s.commit()
        return g.id


def test_group_members_order_fields_and_states(tmp_path):
    g = _group()
    keeper = _member(tmp_path, "keep.jpg", g, keeper=True, score=1.0, sharp=500.0, size=300)
    sharp = _member(tmp_path, "sharp.jpg", g, sharp=420.0, size=100, state="queued", status="queued")
    blurry = _member(tmp_path, "blurry.jpg", g, sharp=90.5, size=900, w=1920, h=1080)
    big = _member(tmp_path, "nosharp_big.jpg", g, sharp=None, size=800)
    small = _member(tmp_path, "nosharp_small.jpg", g, sharp=None, size=200)
    _member(tmp_path, "deleted.jpg", g, status="deleted")
    _member(tmp_path, "missing.jpg", g, status="missing")
    body = client.get(f"/api/candidates/group/{g}").json()
    assert body["group_id"] == g and body["type"] == "near"
    assert [m["asset_id"] for m in body["members"]] == [keeper, sharp, blurry, big, small]
    assert body["members"][0]["is_keeper"] is True and body["members"][1]["state"] == "queued"
    m = body["members"][2]
    assert set(m) == {"asset_id", "filename", "primary_path", "media_type", "size_total", "width", "height",
                      "sharpness", "is_keeper", "state", "score", "rules", "thumbnail_url"}
    assert (m["filename"], m["width"], m["height"], m["sharpness"]) == ("blurry.jpg", 1920, 1080, 90.5)


def test_group_rules_are_merged_and_other_groups_are_not_mixed_in(tmp_path):
    g1, g2 = _group(), _group("burst")
    a = _member(tmp_path, "a.jpg", g1, score=0.96)
    with SyncSessionLocal() as s:
        s.add(Candidate(asset_id=a, rule="screenshot", reason="截圖", score=0.99, group_id=g1, is_keeper=False, state="candidate"))
        s.commit()
    _member(tmp_path, "b.jpg", g2)
    members = client.get(f"/api/candidates/group/{g1}").json()["members"]
    assert [x["asset_id"] for x in members] == [a] and members[0]["rules"] == ["screenshot", "near_dup"] and members[0]["score"] == 0.99
    assert client.get(f"/api/candidates/group/{g2}").json()["type"] == "burst"
    assert client.get("/api/candidates/group/424242").status_code == 404
