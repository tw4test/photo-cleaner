"""
v0.7.0：「所有影片」清單 API（VD-01 至 VD-05）、影片第一幀的 ffmpeg 備援（VD-20）、
影片候選分數是規則固定值（VD-30）。
"""
import io
import os
import shutil
import subprocess
import warnings
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.core.database import SyncSessionLocal, init_db
from app.main import app
from app.models.schema import Asset, Candidate, File, Group, Root, VideoInfo
from app.services import ffmpeg_service, preview_service
from app.services.rules_engine import VIDEO_FIXED_SCORES, rules_engine
from app.stubs.video_probe_stub import video_probe

client = TestClient(app)
HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="沒有安裝 ffmpeg / ffprobe")


@pytest.fixture(autouse=True)
def _clean():
    init_db()
    with SyncSessionLocal() as s:
        for m in (Candidate, Group, VideoInfo, File, Asset, Root):
            s.query(m).delete()
        s.commit()
    preview_service.clear_cache()
    yield
    preview_service.clear_cache()


def _asset(path, *, media="video", status="analysed", size=1000, mtime=1000.0, dur=None, w=None, h=None,
           codec=None, sha=None):
    with SyncSessionLocal() as s:
        root = s.query(Root).first()
        if root is None:
            root = Root(path="X:/root")
            s.add(root)
            s.flush()
        p = str(path)
        a = Asset(root_id=root.id, media_type=media, primary_path=p, size_total=size, paired_paths=[], status=status)
        s.add(a)
        s.flush()
        s.add(File(asset_id=a.id, path=p, size=size, mtime=mtime, format=p.rsplit(".", 1)[-1], role="primary", sha256=sha))
        if dur is not None or w is not None or codec:
            s.add(VideoInfo(asset_id=a.id, duration=dur, width=w, height=h, codec=codec))
        s.commit()
        return a.id


def _list(**params):
    r = client.get("/api/videos", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _names(j):
    return [i["filename"] for i in j["items"]]


@pytest.fixture
def library():
    """五支影片：名稱、日期、大小、時長、解析度的排序互不相同；另有一個沒有 video_info 的。"""
    ids = {}
    ids["b"] = _asset("D:/Videos/Trip/b_beach.mp4", size=300, mtime=3000.0, dur=30.0, w=1920, h=1080, codec="avc1")
    ids["a"] = _asset("D:\\Videos\\Family\\A_birthday.MOV", size=500, mtime=1000.0, dur=10.0, w=3840, h=2160)
    ids["c"] = _asset("E:/Backup/c_old.avi", size=100, mtime=2000.0, dur=60.0, w=640, h=480)
    ids["d"] = _asset("E:/Backup/Trip2/d_clip.mkv", size=400, mtime=5000.0, dur=5.0, w=1280, h=720)
    ids["n"] = _asset("F:/NoInfo/zz_noinfo.wmv", size=200, mtime=4000.0)          # 沒有 video_info
    return ids


# ── 範圍：全部影片，不論有沒有命中規則 ───────────────────────────
class TestScope:
    def test_lists_every_video_regardless_of_rules_and_excludes_non_video_and_gone(self, library, tmp_path):
        _asset("D:/p/photo.jpg", media="photo")
        _asset("D:/p/raw.cr2", media="raw")
        _asset("D:/v/deleted.mp4", status="deleted")
        _asset("D:/v/missing.mp4", status="missing")
        j = _list()
        assert j["total"] == 5 and len(j["items"]) == 5
        assert all(not i["rules"] for i in j["items"])            # 沒有任何候選也要列出
        assert j["total_size"] == 300 + 500 + 100 + 400 + 200

    def test_item_fields(self, library):
        it = next(i for i in _list()["items"] if i["filename"] == "b_beach.mp4")
        assert it["asset_id"] == library["b"] and it["primary_path"] == "D:/Videos/Trip/b_beach.mp4"
        assert (it["size_total"], it["duration"], it["width"], it["height"], it["codec"]) == (300, 30.0, 1920, 1080, "avc1")
        assert it["thumbnail_url"] == f"/api/thumbnails/{library['b']}?v=3000"
        assert it["modified_at"].startswith("1970-01-01")
        assert it["status"] == "analysed" and it["is_keeper"] is False

    def test_backslash_paths_give_correct_filename(self, library):
        assert "A_birthday.MOV" in _names(_list())

    def test_item_without_video_info_has_nulls(self, library):
        it = next(i for i in _list()["items"] if i["filename"] == "zz_noinfo.wmv")
        assert it["duration"] is None and it["width"] is None and it["codec"] is None

    def test_rules_and_state_shown_when_candidate(self, library):
        with SyncSessionLocal() as s:
            s.add(Candidate(asset_id=library["c"], rule="video_tiny", reason="x", score=0.7, state="queued"))
            s.query(Asset).filter(Asset.id == library["c"]).update({"status": "queued"})
            s.commit()
        it = next(i for i in _list()["items"] if i["asset_id"] == library["c"])
        assert it["rules"] == ["video_tiny"] and it["status"] == "queued"

    def test_summary(self, library):
        assert client.get("/api/videos/summary").json() == {"count": 5, "total_size": 1500}

    def test_empty_library(self):
        j = _list()
        assert j["items"] == [] and j["total"] == 0 and j["has_more"] is False
        assert client.get("/api/videos/summary").json() == {"count": 0, "total_size": 0}


# ── 排序 ────────────────────────────────────────────────────────
class TestSort:
    def test_name_asc_is_case_insensitive_by_filename_not_directory(self, library):
        # 目錄是 Videos\Family、Videos/Trip、Backup…；若錯用完整路徑排序，順序會不同
        assert _names(_list(sort="name")) == ["A_birthday.MOV", "b_beach.mp4", "c_old.avi", "d_clip.mkv", "zz_noinfo.wmv"]

    def test_name_desc(self, library):
        assert _names(_list(sort="name", order="desc")) == ["zz_noinfo.wmv", "d_clip.mkv", "c_old.avi", "b_beach.mp4", "A_birthday.MOV"]

    def test_default_orders(self, library):
        assert _list(sort="name")["order"] == "asc"
        for k in ("date", "size", "duration", "resolution"):
            assert _list(sort=k)["order"] == "desc"

    def test_date_size(self, library):
        assert _names(_list(sort="date")) == ["d_clip.mkv", "zz_noinfo.wmv", "b_beach.mp4", "c_old.avi", "A_birthday.MOV"]
        assert _names(_list(sort="date", order="asc"))[0] == "A_birthday.MOV"
        assert _names(_list(sort="size")) == ["A_birthday.MOV", "d_clip.mkv", "b_beach.mp4", "zz_noinfo.wmv", "c_old.avi"]

    def test_duration_nulls_last_in_both_directions(self, library):
        desc = _names(_list(sort="duration", order="desc"))
        asc = _names(_list(sort="duration", order="asc"))
        assert desc == ["c_old.avi", "b_beach.mp4", "A_birthday.MOV", "d_clip.mkv", "zz_noinfo.wmv"]
        assert asc == ["d_clip.mkv", "A_birthday.MOV", "b_beach.mp4", "c_old.avi", "zz_noinfo.wmv"]

    def test_resolution_is_pixel_count_nulls_last(self, library):
        assert _names(_list(sort="resolution")) == ["A_birthday.MOV", "b_beach.mp4", "d_clip.mkv", "c_old.avi", "zz_noinfo.wmv"]
        assert _names(_list(sort="resolution", order="asc"))[-1] == "zz_noinfo.wmv"

    def test_ties_break_by_asset_id(self):
        ids = [_asset(f"D:/v/x{i}.mp4", size=100, mtime=1.0) for i in range(5)]
        j = _list(sort="size")
        assert [i["asset_id"] for i in j["items"]] == sorted(ids)

    def test_bad_sort_or_order_is_422(self, library):
        assert client.get("/api/videos", params={"sort": "score"}).status_code == 422
        assert client.get("/api/videos", params={"order": "up"}).status_code == 422


# ── 搜尋 ────────────────────────────────────────────────────────
class TestSearch:
    def test_case_insensitive_and_matches_directory(self, library):
        assert _names(_list(q="BIRTHDAY")) == ["A_birthday.MOV"]
        assert set(_names(_list(q="trip"))) == {"b_beach.mp4", "d_clip.mkv"}      # Trip 與 Trip2 資料夾

    def test_slash_and_backslash_are_equivalent(self, library):
        assert _names(_list(q="Videos\\Family")) == ["A_birthday.MOV"]
        assert _names(_list(q="Videos/Family")) == ["A_birthday.MOV"]

    def test_multiple_terms_are_anded(self, library):
        assert _names(_list(q="trip clip")) == ["d_clip.mkv"]
        assert _list(q="trip birthday")["total"] == 0

    def test_percent_and_underscore_are_literal(self, library):
        _asset("D:/v/100%_done.mp4")
        assert _names(_list(q="100%")) == ["100%_done.mp4"]
        assert _names(_list(q="_done")) == ["100%_done.mp4"]
        assert _list(q="%")["total"] == 1                         # 不是萬用字元
        _asset("D:/v/xyz.mp4")
        assert _list(q="x_z")["total"] == 0                       # "_" 不是單一字元萬用字元（否則會命中 xyz）

    def test_chinese_names(self):
        _asset("D:/家庭/旅行/海邊.mp4")
        _asset("D:/家庭/生日.mp4")
        assert _names(_list(q="旅行")) == ["海邊.mp4"]
        assert set(_names(_list(q="家庭"))) == {"海邊.mp4", "生日.mp4"}

    def test_search_total_and_size_follow_the_filter(self, library):
        j = _list(q="trip")
        assert j["total"] == 2 and j["total_size"] == 700
        assert _list(q="nomatch")["items"] == []

    def test_blank_query_means_all(self, library):
        assert _list(q="   ")["total"] == 5


# ── 分頁 ────────────────────────────────────────────────────────
class TestPaging:
    def test_infinite_scroll_pages_have_no_gaps_or_duplicates(self):
        for i in range(57):
            _asset(f"D:/v/clip{i:03d}.mp4", size=i + 1, mtime=float(i))
        seen, offset = [], 0
        while True:
            j = _list(sort="size", limit=20, offset=offset)
            seen += [i["asset_id"] for i in j["items"]]
            assert j["total"] == 57
            if not j["has_more"]:
                break
            offset += 20
        assert len(seen) == 57 and len(set(seen)) == 57
        assert _list(sort="size", limit=57)["items"][0]["size_total"] == 57

    def test_has_more_flag_and_limit_bounds(self, library):
        assert _list(limit=2)["has_more"] is True
        assert _list(limit=5)["has_more"] is False
        assert _list(offset=10)["items"] == []
        assert client.get("/api/videos", params={"limit": 501}).status_code == 422
        assert client.get("/api/videos", params={"limit": 0}).status_code == 422
        assert client.get("/api/videos", params={"offset": -1}).status_code == 422

    def test_name_sort_pages_are_stable(self):
        for n in ("b", "a", "d", "c", "e"):
            _asset(f"D:/v/dir_{n}/{n}.mp4")
        first = _names(_list(sort="name", limit=2))
        second = _names(_list(sort="name", limit=2, offset=2))
        third = _names(_list(sort="name", limit=2, offset=4))
        assert first + second + third == ["a.mp4", "b.mp4", "c.mp4", "d.mp4", "e.mp4"]


# ── 只讀 ────────────────────────────────────────────────────────
class TestReadOnly:
    def test_listing_changes_nothing_in_the_database(self, library):
        def snapshot():
            with SyncSessionLocal() as s:
                return (sorted(s.query(Asset.id, Asset.status, Asset.size_total).all()),
                        s.query(Candidate).count())
        before = snapshot()
        _list(sort="duration", q="trip")
        client.get("/api/videos/summary")
        assert snapshot() == before

    def test_no_write_methods_on_the_list_endpoints(self):
        assert client.post("/api/videos").status_code == 405
        assert client.put("/api/videos").status_code == 405
        assert client.delete("/api/videos").status_code == 405
        assert client.post("/api/videos/summary").status_code == 405

    def test_openapi_has_no_duplicate_operation_ids(self):
        app.openapi_schema = None
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            paths = app.openapi()["paths"]
        assert not [x for x in w if "Duplicate Operation ID" in str(x.message)]
        assert "/api/videos" in paths and "/api/videos/summary" in paths
        assert set(paths["/api/videos"]) == {"get"}


# ── ffmpeg 備援：OpenCV 讀不到第一幀（VD-20）───────────────────────
def _make_video(path: Path, size="320x240", codec=("-c:v", "libx264", "-pix_fmt", "yuv420p"), dur=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc=size={size}:rate=10:duration={dur}", *codec, str(path)], check=True, capture_output=True)
    return path


class TestFrameFallback:
    @needs_ffmpeg
    def test_extract_frame_returns_rgb_image(self, tmp_path):
        v = _make_video(tmp_path / "a.mp4")
        img = ffmpeg_service.extract_frame(str(v))
        assert img is not None and img.mode == "RGB" and img.size == (320, 240)

    @needs_ffmpeg
    def test_extract_frame_limits_longest_side_without_upscaling(self, tmp_path):
        big = _make_video(tmp_path / "big.mp4", size="3000x1500")
        assert ffmpeg_service.extract_frame(str(big), max_side=1000).size == (1000, 500)
        port = _make_video(tmp_path / "port.mp4", size="1500x3000")
        assert ffmpeg_service.extract_frame(str(port), max_side=1000).size == (500, 1000)
        assert ffmpeg_service.extract_frame(str(big), max_side=5000).size == (3000, 1500)   # 不放大

    @needs_ffmpeg
    def test_extract_frame_bad_or_missing_file_is_none(self, tmp_path):
        bad = tmp_path / "bad.mp4"
        bad.write_bytes(b"not a video" * 50)
        assert ffmpeg_service.extract_frame(str(bad)) is None
        assert ffmpeg_service.extract_frame(str(tmp_path / "nope.mp4")) is None

    def test_extract_frame_without_ffmpeg_is_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ffmpeg_service, "find_ffmpeg", lambda: None)
        f = tmp_path / "x.mp4"
        f.write_bytes(b"x")
        assert ffmpeg_service.extract_frame(str(f)) is None

    @needs_ffmpeg
    def test_falls_back_to_ffmpeg_only_when_opencv_fails(self, tmp_path, monkeypatch):
        v = _make_video(tmp_path / "a.mp4")
        calls = []
        real = ffmpeg_service.extract_frame
        monkeypatch.setattr(ffmpeg_service, "extract_frame", lambda p, **k: calls.append(p) or real(p, **k))
        assert video_probe.extract_first_frame(str(v)) is not None
        assert calls == []                                        # OpenCV 讀得到 → 不用 ffmpeg
        monkeypatch.setattr(video_probe, "_first_frame_opencv", lambda p: None)
        img = video_probe.extract_first_frame(str(v))
        assert img is not None and img.size == (320, 240) and calls == [str(v)]

    @needs_ffmpeg
    def test_real_opencv_failure_av1(self, tmp_path):
        """真正的 OpenCV 失敗：AV1 在這個 OpenCV 版本打得開但讀不到幀（環境不同時自動略過）。"""
        try:
            v = _make_video(tmp_path / "av1.mkv", codec=("-c:v", "libaom-av1", "-cpu-used", "8", "-pix_fmt", "yuv420p"))
        except subprocess.CalledProcessError:
            pytest.skip("這個 ffmpeg 沒有 AV1 編碼器")
        if video_probe._first_frame_opencv(str(v)) is not None:
            pytest.skip("這個 OpenCV 讀得到 AV1")
        img = video_probe.extract_first_frame(str(v))
        if img is None:
            pytest.skip("這個 ffmpeg 沒有 AV1 解碼器")
        assert img.size == (320, 240)

    @needs_ffmpeg
    def test_preview_and_thumbnail_endpoints_use_the_fallback(self, tmp_path, monkeypatch):
        v = _make_video(tmp_path / "hevc_like.mkv")
        aid = _asset(v, size=v.stat().st_size, mtime=v.stat().st_mtime)
        monkeypatch.setattr(video_probe, "_first_frame_opencv", lambda p: None)
        r = client.get(f"/api/candidates/preview/{aid}")
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
        assert Image.open(io.BytesIO(r.content)).size == (320, 240)
        t = client.get(f"/api/thumbnails/{aid}")
        assert t.status_code == 200 and t.headers["content-type"] == "image/jpeg"

    def test_preview_422_mentions_missing_ffmpeg(self, tmp_path, monkeypatch):
        v = tmp_path / "x.mkv"
        v.write_bytes(b"garbage")
        aid = _asset(v, size=7)
        monkeypatch.setattr(video_probe, "_first_frame_opencv", lambda p: None)
        monkeypatch.setattr(ffmpeg_service, "find_ffmpeg", lambda: None)
        r = client.get(f"/api/candidates/preview/{aid}")
        assert r.status_code == 422 and "ffmpeg" in r.json()["detail"]

    @needs_ffmpeg
    def test_preview_422_when_even_ffmpeg_cannot_read(self, tmp_path, monkeypatch):
        v = tmp_path / "x.mkv"
        v.write_bytes(b"garbage" * 100)
        aid = _asset(v, size=700)
        monkeypatch.setattr(video_probe, "_first_frame_opencv", lambda p: None)
        r = client.get(f"/api/candidates/preview/{aid}")
        assert r.status_code == 422 and "第一幀" in r.json()["detail"] and "沒有安裝" not in r.json()["detail"]

    def test_photo_preview_unchanged(self, tmp_path):
        p = tmp_path / "a.png"
        Image.new("RGB", (40, 30), (1, 2, 3)).save(p)
        r = client.get(f"/api/candidates/preview/{_asset(p, media='photo', size=p.stat().st_size)}")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"


# ── 影片分數是規則固定值，不是 AI 信心度（VD-30）─────────────────
class TestFixedScores:
    def _evaluate(self, **settings_):
        import json
        from app.models.schema import Setting
        with SyncSessionLocal() as s:
            for k, v in settings_.items():
                s.merge(Setting(key=k, value=json.dumps(v)))
            s.commit()
            rules_engine.evaluate_all_rules(s)

    def _scores(self, rule):
        with SyncSessionLocal() as s:
            return sorted((c.score, c.is_keeper) for c in s.query(Candidate).filter(Candidate.rule == rule))

    def test_constants(self):
        assert VIDEO_FIXED_SCORES == {"video_same_size": 1.0, "video_tiny": 0.7}

    def test_same_size_is_1_0_for_keeper_and_members(self):
        for i in range(3):
            _asset(f"D:/v/dup{i}.mp4", size=5000, sha="a" * 64)
        _asset("D:/v/other.mp4", size=6000, sha="b" * 64)
        self._evaluate()
        assert self._scores("video_same_size") == [(1.0, False), (1.0, False), (1.0, True)]

    def test_tiny_is_0_7_and_independent_of_threshold_and_size(self):
        _asset("D:/v/t1.mp4", size=10 * 1024)
        _asset("D:/v/t2.mp4", size=90 * 1024)
        _asset("D:/v/big.mp4", size=50 * 1024 * 1024)
        self._evaluate(rule_video_tiny_enabled=True)
        assert self._scores("video_tiny") == [(0.7, False), (0.7, False)]
        with SyncSessionLocal() as s:
            s.query(Candidate).delete()
            s.commit()
        self._evaluate(rule_video_tiny_enabled=True, video_tiny_threshold_kb=50)
        assert self._scores("video_tiny") == [(0.7, False)]

    def test_tiny_rule_is_off_by_default(self):
        _asset("D:/v/t1.mp4", size=10 * 1024)
        self._evaluate()
        assert self._scores("video_tiny") == []

    def test_photo_exact_dup_scores_unchanged(self):
        for i in range(2):
            _asset(f"D:/p/a{i}.jpg", media="photo", size=999, sha="c" * 64)
        self._evaluate()
        assert self._scores("exact_dup") == [(1.0, False), (1.0, True)]


# ── video 階段：OpenCV 失敗時改用 ffprobe（VD-21）──────────────────
class TestProbeFallback:
    @pytest.fixture
    def vid(self, tmp_path):
        return _make_video(tmp_path / "a.mp4", dur=2) if HAS_FFMPEG else tmp_path / "none.mp4"

    @needs_ffmpeg
    def test_opencv_ok_keeps_opencv_values_and_does_not_call_ffprobe(self, vid, monkeypatch):
        called = []
        monkeypatch.setattr(ffmpeg_service, "probe", lambda p: called.append(p))
        r = video_probe.probe(str(vid))
        assert called == []
        assert (r["width"], r["height"]) == (320, 240) and r["duration"] == pytest.approx(2.0, abs=0.2)

    @needs_ffmpeg
    def test_opencv_cannot_open_uses_ffprobe(self, vid, monkeypatch):
        monkeypatch.setattr(video_probe, "_probe_opencv", lambda p: (None, "OpenCV 無法開啟影片"))
        r = video_probe.probe(str(vid))
        assert (r["width"], r["height"], r["codec"]) == (320, 240, "h264")
        assert r["duration"] == pytest.approx(2.0, abs=0.2) and r["size"] == vid.stat().st_size

    @needs_ffmpeg
    def test_opencv_partial_only_fills_missing_fields(self, vid, monkeypatch):
        monkeypatch.setattr(video_probe, "_probe_opencv",
                            lambda p: ({"duration": None, "width": 111, "height": 222, "codec": "xvid"}, None))
        r = video_probe.probe(str(vid))
        assert (r["width"], r["height"], r["codec"]) == (111, 222, "xvid")      # OpenCV 有的不被覆蓋
        assert r["duration"] == pytest.approx(2.0, abs=0.2)                      # 缺的由 ffprobe 補

    @needs_ffmpeg
    def test_real_opencv_failure_av1_gets_info_from_ffprobe(self, tmp_path):
        try:
            v = _make_video(tmp_path / "av1.mkv", codec=("-c:v", "libaom-av1", "-cpu-used", "8", "-pix_fmt", "yuv420p"))
        except subprocess.CalledProcessError:
            pytest.skip("這個 ffmpeg 沒有 AV1 編碼器")
        r = video_probe.probe(str(v))
        assert (r["width"], r["height"]) == (320, 240) and r["duration"] is not None

    def test_without_ffprobe_old_behaviour_is_kept(self, tmp_path, monkeypatch):
        from app.stubs.video_probe_stub import VideoProbeError
        monkeypatch.setattr(ffmpeg_service, "find_ffprobe", lambda: None)
        monkeypatch.setattr(video_probe, "_probe_opencv", lambda p: (None, "OpenCV 無法開啟影片（格式不支援或檔案損壞）"))
        f = tmp_path / "x.mp4"
        f.write_bytes(b"x")
        with pytest.raises(VideoProbeError, match="OpenCV 無法開啟"):
            video_probe.probe(str(f))

    @needs_ffmpeg
    def test_garbage_file_still_fails_with_error(self, tmp_path):
        from app.stubs.video_probe_stub import VideoProbeError
        f = tmp_path / "bad.mp4"
        f.write_bytes(b"not a video" * 100)
        with pytest.raises(VideoProbeError):
            video_probe.probe(str(f))
        with pytest.raises(VideoProbeError, match="檔案不存在"):
            video_probe.probe(str(tmp_path / "nope.mp4"))

    @needs_ffmpeg
    def test_video_stage_stores_ffprobe_result_when_opencv_fails(self, vid, monkeypatch):
        from app.services.video_stage import run_video_stage
        aid = _asset(vid, size=vid.stat().st_size, mtime=vid.stat().st_mtime)
        monkeypatch.setattr(video_probe, "_probe_opencv", lambda p: (None, "OpenCV 無法開啟影片"))
        with SyncSessionLocal() as s:
            r = run_video_stage(s)
        assert r["processed"] == 1 and r["failed"] == 0
        with SyncSessionLocal() as s:
            vi = s.get(VideoInfo, aid)
            assert (vi.width, vi.height, vi.codec) == (320, 240, "h264")
            assert s.query(__import__("app.models.schema", fromlist=["AnalysisFailure"]).AnalysisFailure).count() == 0
