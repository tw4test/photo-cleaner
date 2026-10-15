"""
v0.6.3：審核頁 API（統計、分頁、批次狀態變更、依條件批次、復原、螢幕截圖 / 文件的保護豁免）。
資料直接寫進暫存資料庫（不需要掃描與分析）。
"""
import pytest
from datetime import datetime
from fastapi.testclient import TestClient

from app.core.database import SyncSessionLocal, init_db
from app.main import app
from app.models.schema import Asset, Candidate, Feature, File, Group, Root, VideoInfo

client = TestClient(app)


# ─────────────────────────────────────────────
# 建資料的小工具
# ─────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _db():
    init_db()
    with SyncSessionLocal() as s:
        for m in (Candidate, Group, VideoInfo, Feature, File, Asset, Root):
            s.query(m).delete()
        s.commit()
    yield


_COUNTER = [0]   # 檔名在整個測試程序內不重複（primary_path 有唯一限制）


class Lib:
    def __init__(self, session):
        self.s = session
        self.root = session.query(Root).filter(Root.path == "X:/r").first()
        if self.root is None:
            self.root = Root(path="X:/r")
            session.add(self.root)
            session.flush()

    def asset(self, name=None, size=1000, media="photo", status="candidate"):
        _COUNTER[0] += 1
        name = name or f"p{_COUNTER[0]:06d}.jpg"
        a = Asset(root_id=self.root.id, media_type=media, primary_path=f"X:/r/{name}", size_total=size, status=status)
        self.s.add(a)
        self.s.flush()
        self.s.add(File(asset_id=a.id, path=a.primary_path, size=size, mtime=1000.0, format="jpg", role="primary"))
        return a

    def cand(self, a, rule, score=0.9, group=None, keeper=False, state="candidate", reason=None):
        c = Candidate(asset_id=a.id, rule=rule, reason=reason or f"{rule} reason", score=score, group_id=group,
                      is_keeper=keeper, state=state)
        self.s.add(c)
        self.s.flush()
        return c

    def group(self, kind="near"):
        g = Group(type=kind)
        self.s.add(g)
        self.s.flush()
        return g.id


def build(fn):
    with SyncSessionLocal() as s:
        lib = Lib(s)
        out = fn(lib)
        s.commit()
        return out


def states_of(*aids):
    with SyncSessionLocal() as s:
        return {aid: {c.state for c in s.query(Candidate).filter(Candidate.asset_id == aid)} for aid in aids}


def page(**params):
    r = client.get("/api/candidates/page", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def status(ids, state, **extra):
    return client.post("/api/candidates/batch-status", json={"asset_ids": list(ids), "state": state, **extra})


def bulk(filter_, target, **extra):
    return client.post("/api/candidates/bulk-status", json={"filter": filter_, "target_state": target, **extra})


# ─────────────────────────────────────────────
# 統計
# ─────────────────────────────────────────────
class TestSummary:
    def test_counts_are_distinct_assets_without_keepers(self):
        def mk(lib):
            g = lib.group()
            a = lib.asset(size=100)                       # screenshot + near_dup（非 Keeper）
            lib.cand(a, "screenshot", 0.9)
            lib.cand(a, "near_dup", 0.95, group=g)
            k = lib.asset(size=200)                       # 近似重複的 Keeper
            lib.cand(k, "near_dup", 1.0, group=g, keeper=True)
            c = lib.asset(size=300)
            lib.cand(c, "blur", 0.5)
            d, e = lib.asset(size=400), lib.asset(size=500)
            lib.cand(d, "over_exposure", 0.5)
            lib.cand(e, "under_exposure", 0.5)
            q = lib.asset(size=600, status="queued")
            lib.cand(q, "screenshot", 0.9, state="queued")
        build(mk)
        s = client.get("/api/candidates/summary", params={"state": "candidate"}).json()
        assert s["states"]["candidate"] == {"count": 4, "size_bytes": 100 + 300 + 400 + 500}
        assert s["states"]["queued"] == {"count": 1, "size_bytes": 600}
        cats = {c["key"]: c for c in s["categories"]}
        assert cats["all"]["count"] == 4
        assert cats["screenshot"]["count"] == 1 and cats["near_dup"]["count"] == 1
        assert cats["near_dup"]["keepers"] == 1 and cats["all"]["keepers"] == 1
        assert cats["exposure"]["count"] == 2 and cats["exposure"]["size_bytes"] == 900
        assert cats["blur"]["count"] == 1 and cats["video"]["count"] == 0
        sq = client.get("/api/candidates/summary", params={"state": "queued"}).json()
        assert {c["key"]: c["count"] for c in sq["categories"]}["screenshot"] == 1

    def test_invalid_state_is_422(self):
        assert client.get("/api/candidates/summary", params={"state": "bogus"}).status_code == 422


# ─────────────────────────────────────────────
# 分頁
# ─────────────────────────────────────────────
class TestPage:
    def test_pagination_is_complete_stable_and_has_no_duplicates(self):
        def mk(lib):
            for i in range(7):
                lib.cand(lib.asset(), "screenshot", 0.90 + i * 0.01)
            # 同分的兩張：靠資產 id 穩定排序
            lib.cand(lib.asset(), "screenshot", 0.90)
        build(mk)
        seen, offset, flags = [], 0, []
        while True:
            p = page(category="screenshot", sort="score", offset=offset, limit=3)
            assert p["total"] == 8
            seen += [i["asset_id"] for i in p["items"]]
            flags.append(p["has_more"])
            if not p["has_more"]:
                break
            offset += 3
        assert len(seen) == 8 and len(set(seen)) == 8
        assert flags == [True, True, False]

    def test_score_sort_descending(self):
        build(lambda lib: [lib.cand(lib.asset(), "screenshot", sc) for sc in (0.61, 0.99, 0.80)])
        scores = [i["score"] for i in page(category="screenshot", sort="score")["items"]]
        assert scores == sorted(scores, reverse=True) == [0.99, 0.80, 0.61]

    def test_size_and_path_sort(self):
        def mk(lib):
            lib.cand(lib.asset("b.jpg", size=10), "blur", 0.1)
            lib.cand(lib.asset("a.jpg", size=30), "blur", 0.1)
            lib.cand(lib.asset("c.jpg", size=20), "blur", 0.1)
        build(mk)
        assert [i["size_total"] for i in page(category="blur", sort="size")["items"]] == [30, 20, 10]
        assert [i["filename"] for i in page(category="blur", sort="path")["items"]] == ["a.jpg", "b.jpg", "c.jpg"]

    def test_min_score_filters_and_counts(self):
        build(lambda lib: [lib.cand(lib.asset(), "meme", sc) for sc in (0.6, 0.8, 0.95)])
        p = page(category="meme", min_score=0.8)
        assert p["total"] == 2 and all(i["score"] >= 0.8 for i in p["items"])

    def test_keeper_modes(self):
        def mk(lib):
            g = lib.group()
            lib.cand(lib.asset("keep.jpg"), "near_dup", 1.0, group=g, keeper=True)
            for n in ("x.jpg", "y.jpg"):
                lib.cand(lib.asset(n), "near_dup", 0.96, group=g)
        build(mk)
        assert page(category="near_dup", keepers="show")["total"] == 3
        hide = page(category="near_dup", keepers="hide")
        assert hide["total"] == 2 and not any(i["is_keeper"] for i in hide["items"])
        only = page(category="near_dup", keepers="only")
        assert only["total"] == 1 and only["items"][0]["is_keeper"]

    def test_group_sort_keeps_members_adjacent_and_reports_group_size(self):
        def mk(lib):
            g1, g2 = lib.group(), lib.group()
            for i in range(3):
                lib.cand(lib.asset(f"g1_{i}.jpg"), "near_dup", 0.95 + i * 0.001, group=g1)
            for i in range(2):
                lib.cand(lib.asset(f"g2_{i}.jpg"), "near_dup", 0.95, group=g2)
            lib.cand(lib.asset("solo.jpg"), "blur", 0.5)
        build(mk)
        items = page(category="all", sort="group")["items"]
        gids = [i["group_id"] for i in items]
        # 群組 id 大的先；同群組連續；沒有群組的排最後
        assert gids == sorted([g for g in gids if g], reverse=True) + [None]
        first = items[0]
        assert first["group_size"] == 2 and items[2]["group_size"] == 3

    def test_item_carries_all_rules_and_metadata(self):
        def mk(lib):
            g = lib.group()
            a = lib.asset("shot.jpg", size=4096)
            lib.cand(a, "screenshot", 0.97, reason="AI 識別為螢幕截圖")
            lib.cand(a, "near_dup", 0.96, group=g, reason="近似")
            s = lib.s
            s.add(Feature(asset_id=a.id, width=1080, height=2340, taken_at=datetime(2026, 9, 1, 10, 30), exif={}))
            v = lib.asset("clip.mp4", media="video")
            lib.cand(v, "video_tiny", 0.1)
            s.add(VideoInfo(asset_id=v.id, width=640, height=480, duration=3.0))
        build(mk)
        it = [i for i in page(category="screenshot")["items"]][0]
        assert it["filename"] == "shot.jpg" and it["rule"] == "screenshot" and it["score"] == 0.97
        assert set(it["rules"]) == {"screenshot", "near_dup"} and len(it["reasons"]) == 2
        assert (it["width"], it["height"]) == (1080, 2340) and it["taken_at"].startswith("2026-09-01")
        assert it["thumbnail_url"].startswith("/api/thumbnails/")
        vid = page(category="video")["items"][0]
        assert (vid["width"], vid["height"]) == (640, 480)

    def test_state_isolation(self):
        def mk(lib):
            lib.cand(lib.asset(), "screenshot", 0.9)
            lib.cand(lib.asset(status="queued"), "screenshot", 0.9, state="queued")
        build(mk)
        assert page(category="screenshot", state="candidate")["total"] == 1
        assert page(category="screenshot", state="queued")["total"] == 1

    def test_bad_params(self):
        assert client.get("/api/candidates/page", params={"category": "nope"}).status_code == 422
        assert client.get("/api/candidates/page", params={"limit": 5000}).status_code == 422
        assert client.get("/api/candidates/page", params={"sort": "zzz"}).status_code == 422


# ─────────────────────────────────────────────
# 以資產 id 變更狀態、復原
# ─────────────────────────────────────────────
class TestBatchStatus:
    def test_queue_returns_ids_and_undo_restores(self):
        ids = build(lambda lib: [lib.asset().id for _ in range(3)])
        build(lambda lib: [lib.cand(lib.s.get(Asset, i), "blur", 0.5) for i in ids])
        r = status(ids, "queued").json()
        assert r["updated_count"] == 3 and sorted(r["updated_asset_ids"]) == sorted(ids) and r["protected_count"] == 0
        assert all(v == {"queued"} for v in states_of(*ids).values())
        assert client.post("/api/candidates/undo", json={"token": r["undo_token"]}).json()["restored_count"] == 3
        assert all(v == {"candidate"} for v in states_of(*ids).values())
        # 同一個 token 不能復原兩次
        assert client.post("/api/candidates/undo", json={"token": r["undo_token"]}).status_code == 404

    def test_undo_does_not_overwrite_later_decisions(self):
        def mk(lib):
            a, b = lib.asset(), lib.asset()
            lib.cand(a, "blur"), lib.cand(b, "blur")
            return a.id, b.id
        a, b = build(mk)
        r = status([a, b], "queued").json()
        status([a], "kept")                      # 之後使用者又把 a 改成保留
        assert client.post("/api/candidates/undo", json={"token": r["undo_token"]}).json()["restored_count"] == 1
        st = states_of(a, b)
        assert st[a] == {"kept"} and st[b] == {"candidate"}

    def test_undo_remembers_each_previous_state(self):
        def mk(lib):
            a, b = lib.asset(), lib.asset(status="kept")
            lib.cand(a, "blur"), lib.cand(b, "blur", state="kept")
            return a.id, b.id
        a, b = build(mk)
        r = status([a, b], "queued").json()
        client.post("/api/candidates/undo", json={"token": r["undo_token"]})
        st = states_of(a, b)
        assert st[a] == {"candidate"} and st[b] == {"kept"}

    def test_candidate_ids_still_supported(self):
        def mk(lib):
            a = lib.asset()
            return lib.cand(a, "blur").id, a.id
        cid, aid = build(mk)
        r = client.post("/api/candidates/batch-status", json={"candidate_ids": [cid], "state": "kept"}).json()
        assert r["updated_count"] == 1 and states_of(aid)[aid] == {"kept"}

    def test_bad_state_and_bad_waive_value(self):
        assert status([1], "deleted").status_code == 400
        assert status([1], "queued", waive_protection_for="meme").status_code == 400

    def test_more_than_one_chunk_in_a_single_call(self):
        n = 1200

        def mk(lib):
            ids = []
            for _ in range(n):
                a = lib.asset()
                lib.cand(a, "blur")
                ids.append(a.id)
            return ids
        ids = build(mk)
        r = status(ids, "queued").json()
        assert r["updated_count"] == n and r["protected_count"] == 0
        assert page(state="queued", category="blur", limit=1)["total"] == n


class TestProtectionWaiver:
    """螢幕截圖 / 文件：使用者決定不受「群組至少留一張」保護；其他維持原樣。"""

    def _group_of_three(self, rule=None):
        def mk(lib):
            g = lib.group()
            ids = []
            for i in range(3):
                a = lib.asset()
                lib.cand(a, "near_dup", 1.0 if i == 0 else 0.96, group=g, keeper=(i == 0))
                if rule:
                    lib.cand(a, rule, 0.95)
                ids.append(a.id)
            return ids
        return build(mk)

    def test_without_waiver_the_last_member_is_protected(self):
        ids = self._group_of_three("screenshot")
        r = status(ids, "queued").json()
        assert r["protected_count"] >= 1 and r["updated_count"] + r["protected_count"] == 3

    def test_with_waiver_all_screenshot_members_can_be_queued(self):
        ids = self._group_of_three("screenshot")
        r = status(ids, "queued", waive_protection_for="screenshot").json()
        assert r["updated_count"] == 3 and r["protected_count"] == 0
        assert all(v == {"queued"} for v in states_of(*ids).values())

    def test_waiver_needs_the_matching_rule(self):
        ids = self._group_of_three("screenshot")
        r = status(ids, "queued", waive_protection_for="document").json()   # 這些資產沒有 document 記錄
        assert r["protected_count"] >= 1

    def test_non_waived_member_is_still_protected_after_waived_ones_are_queued(self):
        def mk(lib):
            g = lib.group()
            shot = lib.asset()
            lib.cand(shot, "near_dup", 0.96, group=g)
            lib.cand(shot, "screenshot", 0.95)
            normal = lib.asset()                       # 不是截圖的 Keeper
            lib.cand(normal, "near_dup", 1.0, group=g, keeper=True)
            return shot.id, normal.id
        shot, normal = build(mk)
        r = status([shot, normal], "queued", waive_protection_for="screenshot").json()
        assert r["updated_asset_ids"] == [shot] and r["protected_asset_ids"] == [normal]
        assert states_of(normal)[normal] == {"candidate"}


# ─────────────────────────────────────────────
# 依條件批次
# ─────────────────────────────────────────────
class TestBulk:
    def _shots(self, n=5):
        def mk(lib):
            return [lib.cand(lib.asset(size=100 * (i + 1)), "screenshot", 0.80 + i * 0.03).asset_id for i in range(n)]
        return build(mk)

    def test_dry_run_reports_count_size_and_a_sample(self):
        ids = self._shots(20)
        r = bulk({"category": "screenshot"}, "queued").json()
        assert r["status"] == "preview" and r["count"] == 20 and r["waived"] is True
        assert r["size_bytes"] == sum(100 * (i + 1) for i in range(20))
        assert 0 < len(r["sample"]) <= 12 and {s["asset_id"] for s in r["sample"]} <= set(ids)
        assert all(v == {"candidate"} for v in states_of(*ids).values())      # 預覽不寫入

    def test_apply_requires_expected_count_and_refuses_when_stale(self):
        ids = self._shots(5)
        f = {"category": "screenshot"}
        assert bulk(f, "queued", dry_run=False).status_code == 400
        r = bulk(f, "queued", dry_run=False, expected_count=4)
        assert r.status_code == 409
        assert all(v == {"candidate"} for v in states_of(*ids).values())      # 被拒絕時一張都沒動

    def test_apply_respects_exclusions_and_min_score_and_can_be_undone(self):
        ids = self._shots(5)                     # 分數 0.80, 0.83, 0.86, 0.89, 0.92
        f = {"category": "screenshot", "min_score": 0.85}
        pv = bulk(f, "queued", exclude_asset_ids=[ids[-1]]).json()
        assert pv["count"] == 2                  # 0.86、0.89（0.92 被排除）
        r = bulk(f, "queued", dry_run=False, expected_count=2, exclude_asset_ids=[ids[-1]]).json()
        assert r["updated_count"] == 2 and r["protected_count"] == 0 and r["size_bytes"] == 300 + 400
        st = states_of(*ids)
        assert [st[i] == {"queued"} for i in ids] == [False, False, True, True, False]
        client.post("/api/candidates/undo", json={"token": r["undo_token"]})
        assert all(v == {"candidate"} for v in states_of(*ids).values())

    def test_bulk_never_touches_keepers(self):
        def mk(lib):
            g = lib.group()
            k = lib.asset()
            lib.cand(k, "near_dup", 1.0, group=g, keeper=True)
            others = []
            for _ in range(3):
                a = lib.asset()
                lib.cand(a, "near_dup", 0.96, group=g)
                others.append(a.id)
            return k.id, others
        k, others = build(mk)
        pv = bulk({"category": "near_dup"}, "queued").json()
        assert pv["count"] == 3 and pv["waived"] is False
        bulk({"category": "near_dup"}, "queued", dry_run=False, expected_count=3)
        assert states_of(k)[k] == {"candidate"}
        assert all(v == {"queued"} for v in states_of(*others).values())

    def test_screenshot_bulk_waives_group_protection(self):
        def mk(lib):
            g = lib.group()
            ids = []
            for i in range(3):
                a = lib.asset()
                lib.cand(a, "near_dup", 1.0 if i == 0 else 0.96, group=g, keeper=(i == 0))
                lib.cand(a, "screenshot", 0.95)
                ids.append(a.id)
            return ids
        ids = build(mk)
        r = bulk({"category": "screenshot"}, "queued", dry_run=False, expected_count=3).json()
        assert r["updated_count"] == 3 and r["protected_count"] == 0
        assert all(v == {"queued"} for v in states_of(*ids).values())

    def test_other_bad_requests(self):
        self._shots(1)
        assert bulk({"category": "nope"}, "queued").status_code == 422
        assert bulk({"category": "screenshot", "state": "candidate"}, "candidate").status_code == 400

    def test_large_bulk_spans_several_chunks(self):
        n = 1300

        def mk(lib):
            for _ in range(n):
                lib.cand(lib.asset(), "screenshot", 0.9)
        build(mk)
        r = bulk({"category": "screenshot"}, "queued", dry_run=False, expected_count=n).json()
        assert r["updated_count"] == n
        client.post("/api/candidates/undo", json={"token": r["undo_token"]})
        assert page(category="screenshot", limit=1)["total"] == n
