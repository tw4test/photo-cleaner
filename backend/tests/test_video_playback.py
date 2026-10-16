"""
v0.7.0（VD-）：影片原檔 Range 串流、播放判斷、ffmpeg 轉檔（進度、取消、快取、容量上限）。

需要 ffmpeg / ffprobe 的測試在沒有安裝時自動略過（ffmpeg 是選用的）。
測試影片用 ffmpeg 的 lavfi 來源現場產生，不依賴任何外部檔案。
"""
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import SyncSessionLocal, init_db
from app.main import app
from app.models.schema import Asset, File, Root, VideoInfo
from app.services import ffmpeg_service, video_playback, video_transcode
from app.services.range_file import RangeNotSatisfiable, parse_range

client = TestClient(app)
HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="沒有安裝 ffmpeg / ffprobe")


# ── 共用 ────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _clean(tmp_path):
    init_db()
    with SyncSessionLocal() as s:
        for m in (VideoInfo, File, Asset, Root):
            s.query(m).delete()
        s.commit()
    video_transcode.shutdown()
    video_transcode._jobs.clear()
    ffmpeg_service.clear_probe_cache()
    shutil.rmtree(settings.VIDEO_CACHE_DIR, ignore_errors=True)
    yield
    video_transcode.shutdown()
    shutil.rmtree(settings.VIDEO_CACHE_DIR, ignore_errors=True)


def _asset(path: Path, media="video", status="analysed", codec=None, duration=None):
    with SyncSessionLocal() as s:
        root = s.query(Root).first()
        if root is None:
            root = Root(path="X:/root")
            s.add(root)
            s.flush()
        sz = path.stat().st_size if path.exists() else 1000
        a = Asset(root_id=root.id, media_type=media, primary_path=str(path), size_total=sz, paired_paths=[], status=status)
        s.add(a)
        s.flush()
        s.add(File(asset_id=a.id, path=str(path), size=sz, mtime=1000.0, format=path.suffix.lstrip("."), role="primary"))
        if codec or duration:
            s.add(VideoInfo(asset_id=a.id, duration=duration, width=320, height=240, codec=codec))
        s.commit()
        return a.id


def _ff(args, out: Path):
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args, str(out)], check=True,
                   capture_output=True)
    return out


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("videos")
    return d


def _make(media, name, src, codec_args):
    p = media / name
    if not p.exists():
        _ff(["-f", "lavfi", "-i", src, *codec_args], p)
    return p


@pytest.fixture
def mp4(media):
    return _make(media, "ok.mp4", "testsrc=size=320x240:rate=15:duration=2",
                 ["-c:v", "libx264", "-pix_fmt", "yuv420p"])


@pytest.fixture
def avi(media):
    return _make(media, "old.avi", "testsrc=size=320x240:rate=15:duration=2", ["-c:v", "mpeg4"])


# ── Range 解析（不需要 ffmpeg）──────────────────────────────────
class TestParseRange:
    def test_forms(self):
        assert parse_range(None, 100) is None
        assert parse_range("bytes=0-9", 100) == (0, 9)
        assert parse_range("bytes=10-", 100) == (10, 99)
        assert parse_range("bytes=-10", 100) == (90, 99)
        assert parse_range("bytes=90-500", 100) == (90, 99)       # 超出檔尾要截斷
        assert parse_range("bytes=-500", 100) == (0, 99)

    def test_ignored(self):
        assert parse_range("bytes=0-1,5-6", 100) is None           # 多範圍：忽略
        assert parse_range("items=0-1", 100) is None
        assert parse_range("bytes=9-3", 100) is None
        assert parse_range("garbage", 100) is None

    def test_unsatisfiable(self):
        with pytest.raises(RangeNotSatisfiable):
            parse_range("bytes=100-", 100)
        with pytest.raises(RangeNotSatisfiable):
            parse_range("bytes=-0", 100)


# ── 播放判斷（不需要 ffmpeg）────────────────────────────────────
class TestDecide:
    def probe(self, vc="h264", ac="aac", pix="yuv420p"):
        return {"video": {"codec": vc, "pix_fmt": pix}, "audio": None if ac is None else {"codec": ac}}

    def test_containers(self):
        assert video_playback.decide(".avi", None, None)[0] is False
        assert video_playback.decide(".wmv", None, None)[0] is False
        assert video_playback.decide(".flv", None, None)[0] is False
        assert video_playback.decide(".mp4", None, "avc1")[0] is True

    def test_codecs_with_probe(self):
        assert video_playback.decide(".mp4", self.probe(), None)[0] is True
        assert video_playback.decide(".mp4", self.probe(ac=None), None)[0] is True
        assert video_playback.decide(".webm", self.probe("vp9", "opus"), None)[0] is True
        assert video_playback.decide(".mkv", self.probe("h264", "aac"), None)[0] is True
        assert video_playback.decide(".mp4", self.probe("hevc"), None)[0] is False
        assert video_playback.decide(".mkv", self.probe("hevc"), None)[0] is False
        assert video_playback.decide(".mp4", self.probe(pix="yuv420p10le"), None)[0] is False
        assert video_playback.decide(".mkv", self.probe(ac="ac3"), None)[0] is False
        assert video_playback.decide(".mp4", self.probe("mpeg4"), None)[0] is False

    def test_classify_three_ways(self):
        """VD-11：direct（保證可播）/ try_first（HEVC、10-bit H.264：由瀏覽器與顯示卡決定，先試播）/ transcode（一定要轉）"""
        c = video_playback.classify
        assert c(".mp4", self.probe(), None)[0] == "direct"
        assert c(".webm", self.probe("vp9", "opus"), None)[0] == "direct"
        # HEVC / 10-bit：容器與音訊都沒問題 → 先試播
        assert c(".mp4", self.probe("hevc"), None)[0] == "try_first"
        assert c(".mov", self.probe("hevc", "aac"), None)[0] == "try_first"
        assert c(".mkv", self.probe("hevc", None), None)[0] == "try_first"
        assert c(".mp4", self.probe("h265"), None)[0] == "try_first"
        assert c(".mp4", self.probe(pix="yuv420p10le"), None)[0] == "try_first"
        assert "先嘗試直接播放" in c(".mp4", self.probe("hevc"), None)[1]
        # 音訊瀏覽器不支援：影像能播但沒有聲音，前端偵測不到 → 不冒險，直接轉檔
        assert c(".mp4", self.probe("hevc", "ac3"), None)[0] == "transcode"
        assert c(".mp4", self.probe("h264", "dts"), None)[0] == "transcode"
        # 容器不支援、或影像編碼本來就沒有任何瀏覽器支援 → 一定要轉
        assert c(".avi", self.probe("hevc"), None)[0] == "transcode"
        assert c(".wmv", self.probe("wmv3"), None)[0] == "transcode"
        assert c(".mp4", self.probe("mpeg4"), None)[0] == "transcode"
        assert c(".mp4", self.probe("mpeg2video"), None)[0] == "transcode"

    def test_classify_without_ffprobe(self):
        c = video_playback.classify
        for codec in ("hevc", "hev1", "HVC1"):
            assert c(".mp4", None, codec)[0] == "try_first"
        assert c(".mp4", None, "avc1")[0] == "direct"
        assert c(".avi", None, "hvc1")[0] == "transcode"
        assert c(".flv", None, None)[0] == "transcode"

    def test_without_ffprobe_uses_db_codec(self):
        for c in ("hevc", "hev1", "HVC1"):
            assert video_playback.decide(".mp4", None, c)[0] is False
        assert video_playback.decide(".mov", None, "avc1")[0] is True

    def test_mime(self):
        assert video_playback.mime_for(".mov") == "video/mp4"
        assert video_playback.mime_for(".MKV") == "video/x-matroska"
        assert video_playback.mime_for(".xyz") == "application/octet-stream"


# ── 串流端點 ────────────────────────────────────────────────────
class TestStream:
    @pytest.fixture
    def vid(self, tmp_path):
        p = tmp_path / "clip.mp4"
        p.write_bytes(bytes(range(256)) * 4096)           # 1 MiB，內容可驗證
        return p, _asset(p)

    def test_full_and_headers(self, vid):
        p, aid = vid
        r = client.get(f"/api/videos/{aid}/stream")
        assert r.status_code == 200
        assert r.headers["accept-ranges"] == "bytes"
        assert r.headers["content-type"].startswith("video/mp4")
        assert r.content == p.read_bytes()

    def test_partial_content(self, vid):
        p, aid = vid
        data = p.read_bytes()
        r = client.get(f"/api/videos/{aid}/stream", headers={"Range": "bytes=1000-1999"})
        assert r.status_code == 206
        assert r.headers["content-range"] == f"bytes 1000-1999/{len(data)}"
        assert r.headers["content-length"] == "1000"
        assert r.content == data[1000:2000]

    def test_open_ended_and_suffix_range(self, vid):
        p, aid = vid
        data = p.read_bytes()
        r = client.get(f"/api/videos/{aid}/stream", headers={"Range": "bytes=1048000-"})
        assert r.status_code == 206 and r.content == data[1048000:]
        r = client.get(f"/api/videos/{aid}/stream", headers={"Range": "bytes=-100"})
        assert r.status_code == 206 and r.content == data[-100:]

    def test_range_across_chunk_boundary(self, tmp_path):
        p = tmp_path / "big.mp4"
        data = os.urandom(2_500_000)                      # 大於 1 MiB 的讀取分塊
        p.write_bytes(data)
        aid = _asset(p)
        r = client.get(f"/api/videos/{aid}/stream", headers={"Range": "bytes=1000000-2200000"})
        assert r.status_code == 206 and r.content == data[1000000:2200001]

    def test_unsatisfiable_is_416(self, vid):
        p, aid = vid
        r = client.get(f"/api/videos/{aid}/stream", headers={"Range": "bytes=99999999-"})
        assert r.status_code == 416
        assert r.headers["content-range"] == f"bytes */{p.stat().st_size}"

    def test_bad_or_multi_range_returns_whole_file(self, vid):
        p, aid = vid
        for h in ("bytes=0-1,5-6", "nonsense"):
            r = client.get(f"/api/videos/{aid}/stream", headers={"Range": h})
            assert r.status_code == 200 and len(r.content) == p.stat().st_size

    def test_head(self, vid):
        p, aid = vid
        r = client.head(f"/api/videos/{aid}/stream")
        assert r.status_code == 200 and r.content == b""
        assert r.headers["content-length"] == str(p.stat().st_size)
        r = client.head(f"/api/videos/{aid}/stream", headers={"Range": "bytes=0-9"})
        assert r.status_code == 206 and r.headers["content-length"] == "10"

    def test_404_cases(self, tmp_path):
        assert client.get("/api/videos/999999/stream").status_code == 404
        photo = tmp_path / "a.jpg"
        photo.write_bytes(b"x")
        assert client.get(f"/api/videos/{_asset(photo, media='photo')}/stream").status_code == 404
        gone = tmp_path / "gone.mp4"
        assert client.get(f"/api/videos/{_asset(gone)}/stream").status_code == 404       # 檔案不存在
        deleted = tmp_path / "d.mp4"
        deleted.write_bytes(b"x")
        assert client.get(f"/api/videos/{_asset(deleted, status='deleted')}/stream").status_code == 404
        missing = tmp_path / "m.mp4"
        missing.write_bytes(b"x")
        assert client.get(f"/api/videos/{_asset(missing, status='missing')}/stream").status_code == 404

    def test_does_not_modify_file(self, vid):
        p, aid = vid
        before = hashlib.sha256(p.read_bytes()).hexdigest()
        client.get(f"/api/videos/{aid}/stream")
        client.get(f"/api/videos/{aid}/stream", headers={"Range": "bytes=5-50"})
        assert hashlib.sha256(p.read_bytes()).hexdigest() == before


# ── info ────────────────────────────────────────────────────────
class TestInfo:
    @needs_ffmpeg
    def test_h264_mp4_is_direct(self, mp4):
        r = client.get(f"/api/videos/{_asset(mp4)}/info")
        assert r.status_code == 200
        j = r.json()
        assert j["direct_playable"] is True and j["needs_transcode"] is False
        assert j["video_codec"] == "h264" and j["width"] == 320 and j["height"] == 240
        assert j["duration"] == pytest.approx(2.0, abs=0.2)
        assert j["stream_url"].startswith(f"/api/videos/{j['asset_id']}/stream")
        assert j["transcode"]["status"] == "idle"

    @needs_ffmpeg
    def test_avi_needs_transcode(self, avi):
        j = client.get(f"/api/videos/{_asset(avi)}/info").json()
        assert j["needs_transcode"] is True and j["ffmpeg_available"] is True and j["install_hint"] is None

    @needs_ffmpeg
    def test_info_playback_field_for_real_files(self, media, mp4, avi):
        hevc = _make(media, "hevc2.mp4", "testsrc=size=320x240:rate=15:duration=1",
                     ["-c:v", "libx265", "-pix_fmt", "yuv420p", "-tag:v", "hvc1"])
        ac3 = media / "ac3.mkv"
        if not ac3.exists():
            _ff(["-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=1", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "ac3"], ac3)
        ids = {n: _asset(p) for n, p in (("h264", mp4), ("avi", avi), ("hevc", hevc), ("ac3", ac3))}
        got = {n: client.get(f"/api/videos/{i}/info").json()["playback"] for n, i in ids.items()}
        assert got == {"h264": "direct", "avi": "transcode", "hevc": "try_first", "ac3": "transcode"}
        j = client.get(f"/api/videos/{ids['hevc']}/info").json()
        assert j["playback"] == "try_first" and j["direct_playable"] is False and j["needs_transcode"] is True   # 舊欄位維持原意
        assert "先嘗試直接播放" in j["reason"]

    @needs_ffmpeg
    def test_hevc_needs_transcode(self, media):
        p = _make(media, "hevc.mp4", "testsrc=size=320x240:rate=15:duration=1",
                  ["-c:v", "libx265", "-pix_fmt", "yuv420p", "-tag:v", "hvc1"])
        j = client.get(f"/api/videos/{_asset(p)}/info").json()
        assert j["video_codec"] == "hevc" and j["needs_transcode"] is True

    def test_without_ffmpeg_still_answers_and_gives_install_hint(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ffmpeg_service, "find_ffmpeg", lambda: None)
        monkeypatch.setattr(ffmpeg_service, "find_ffprobe", lambda: None)
        p = tmp_path / "x.avi"
        p.write_bytes(b"not really a video")
        j = client.get(f"/api/videos/{_asset(p)}/info").json()
        assert j["needs_transcode"] is True and j["ffmpeg_available"] is False
        assert "winget install" in j["install_hint"]
        # 沒有 ffprobe：HEVC 靠資料庫裡的 codec 判斷
        q = tmp_path / "y.mp4"
        q.write_bytes(b"x")
        assert client.get(f"/api/videos/{_asset(q, codec='hvc1')}/info").json()["needs_transcode"] is True
        assert client.get(f"/api/videos/{_asset(tmp_path / 'z.mp4')}/info").status_code == 404   # 檔案不存在


# ── ffmpeg 狀態與缺 ffmpeg 時的降級 ──────────────────────────────
class TestFfmpegOptional:
    def test_status_when_missing(self, monkeypatch):
        monkeypatch.setattr(ffmpeg_service, "find_ffmpeg", lambda: None)
        monkeypatch.setattr(ffmpeg_service, "find_ffprobe", lambda: None)
        j = client.get("/api/videos/ffmpeg").json()
        assert j["available"] is False and j["version"] is None and "winget" in j["install_hint"]
        assert j["cache"]["limit_bytes"] == settings.VIDEO_CACHE_MAX_BYTES

    def test_transcode_is_503_with_hint_but_stream_still_works(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ffmpeg_service, "find_ffmpeg", lambda: None)
        p = tmp_path / "x.avi"
        p.write_bytes(b"0123456789")
        aid = _asset(p)
        r = client.post(f"/api/videos/{aid}/transcode")
        assert r.status_code == 503 and "ffmpeg" in r.json()["detail"]
        assert client.get(f"/api/videos/{aid}/stream").content == b"0123456789"
        assert client.post(f"/api/videos/{aid}/transcode/cancel").json()["status"] == "idle"

    def test_configured_path_is_used(self, tmp_path, monkeypatch):
        fake = tmp_path / "my-ffmpeg.exe"
        fake.write_bytes(b"")
        monkeypatch.setattr(settings, "FFMPEG_PATH", str(fake))
        assert ffmpeg_service.find_ffmpeg() == str(fake)
        monkeypatch.setattr(settings, "FFMPEG_PATH", str(tmp_path / "nope.exe"))
        assert ffmpeg_service.find_ffmpeg() is None


def _wait(aid, want=("done", "error", "cancelled"), timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/videos/{aid}/transcode").json()
        if j["status"] in want:
            return j
        time.sleep(0.1)
    raise AssertionError(f"逾時，最後狀態：{j}")


def _probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(path)],
                       capture_output=True, check=True)
    return json.loads(r.stdout)["streams"]


# ── 轉檔 ────────────────────────────────────────────────────────
@needs_ffmpeg
class TestTranscode:
    def test_avi_to_h264_mp4_original_untouched(self, avi):
        before = hashlib.sha256(avi.read_bytes()).hexdigest()
        mtime = avi.stat().st_mtime_ns
        aid = _asset(avi)
        r = client.post(f"/api/videos/{aid}/transcode")
        assert r.status_code == 200
        j = _wait(aid)
        assert j["status"] == "done" and j["progress"] == 100.0, j
        assert j["url"].startswith(f"/api/videos/{aid}/transcoded")
        out = video_transcode.cached_file(str(avi))
        assert out is not None and out.parent.parent == settings.VIDEO_CACHE_DIR
        v = next(s for s in _probe(out) if s["codec_type"] == "video")
        assert v["codec_name"] == "h264" and v["pix_fmt"] == "yuv420p"
        assert not list(settings.VIDEO_CACHE_DIR.rglob("*.part"))
        # 原檔一個位元組都沒動
        assert hashlib.sha256(avi.read_bytes()).hexdigest() == before and avi.stat().st_mtime_ns == mtime

    def test_transcoded_is_served_with_range(self, avi):
        aid = _asset(avi)
        assert client.get(f"/api/videos/{aid}/transcoded").status_code == 404      # 還沒轉
        client.post(f"/api/videos/{aid}/transcode")
        _wait(aid)
        out = video_transcode.cached_file(str(avi))
        data = out.read_bytes()
        r = client.get(f"/api/videos/{aid}/transcoded", headers={"Range": "bytes=0-99"})
        assert r.status_code == 206 and r.content == data[:100]
        assert r.headers["content-type"].startswith("video/mp4")
        assert client.get(f"/api/videos/{aid}/transcoded").content == data
        # +faststart：moov 在 mp4 前段，才能邊下載邊播、拖曳進度條
        assert data.find(b"moov") != -1 and data.find(b"moov") < data.find(b"mdat")

    def test_second_request_hits_cache_without_running_ffmpeg(self, avi, monkeypatch):
        aid = _asset(avi)
        client.post(f"/api/videos/{aid}/transcode")
        _wait(aid)
        spawned = []
        monkeypatch.setattr(video_transcode, "_spawn", lambda cmd: spawned.append(cmd))
        j = client.post(f"/api/videos/{aid}/transcode").json()
        assert j["status"] == "done" and j["from_cache"] is True and not spawned

    def test_downscales_to_1080_short_side_landscape_and_portrait(self, media):
        land = _make(media, "land.mp4", "testsrc=size=2560x1440:rate=5:duration=1",
                     ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"])
        port = _make(media, "port.mp4", "testsrc=size=1440x2560:rate=5:duration=1",
                     ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"])
        small = _make(media, "small.avi", "testsrc=size=640x360:rate=5:duration=1", ["-c:v", "mpeg4"])
        got = {}
        for name, p in (("land", land), ("port", port), ("small", small)):
            aid = _asset(p)
            client.post(f"/api/videos/{aid}/transcode")
            assert _wait(aid)["status"] == "done"
            v = next(s for s in _probe(video_transcode.cached_file(str(p))) if s["codec_type"] == "video")
            got[name] = (v["width"], v["height"])
        assert got["land"] == (1920, 1080)
        assert got["port"] == (1080, 1920)
        assert got["small"] == (640, 360)                 # 不放大

    def test_audio_is_aac_and_silent_source_works(self, media):
        with_audio = media / "with_audio.avi"
        if not with_audio.exists():
            _ff(["-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=1", "-f", "lavfi",
                 "-i", "sine=frequency=440:duration=1", "-c:v", "mpeg4", "-c:a", "mp3"], with_audio)
        aid = _asset(with_audio)
        client.post(f"/api/videos/{aid}/transcode")
        assert _wait(aid)["status"] == "done"
        kinds = {s["codec_type"]: s["codec_name"] for s in _probe(video_transcode.cached_file(str(with_audio)))}
        assert kinds == {"video": "h264", "audio": "aac"}

    def test_broken_file_reports_error_and_leaves_nothing(self, tmp_path):
        bad = tmp_path / "bad.avi"
        bad.write_bytes(b"this is not a video" * 100)
        aid = _asset(bad)
        client.post(f"/api/videos/{aid}/transcode")
        j = _wait(aid)
        assert j["status"] == "error" and j["error"]
        assert not list(settings.VIDEO_CACHE_DIR.rglob("*.part")) and not list(settings.VIDEO_CACHE_DIR.rglob("*.mp4"))

    def test_progress_is_reported_while_running(self, media):
        long_src = _make(media, "long.mp4", "testsrc2=size=1280x720:rate=30:duration=40",
                         ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"])
        aid = _asset(long_src)
        client.post(f"/api/videos/{aid}/transcode")
        seen = []
        t0 = time.time()
        while time.time() - t0 < 60:
            j = client.get(f"/api/videos/{aid}/transcode").json()
            seen.append(j["progress"])
            if j["status"] != "running" or (j["progress"] or 0) > 5:
                break
            time.sleep(0.1)
        assert any(p is not None and 0 < p < 100 for p in seen), seen
        client.post(f"/api/videos/{aid}/transcode/cancel")


@needs_ffmpeg
class TestCancel:
    @pytest.fixture
    def long_vid(self, media):
        return _make(media, "long2.mp4", "testsrc2=size=1920x1080:rate=30:duration=60",
                     ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"])

    @pytest.fixture
    def spy(self, monkeypatch):
        """包住 Popen，記錄 kill / terminate 是否被呼叫。"""
        calls = {"kill": 0, "terminate": 0, "procs": []}
        real = video_transcode._spawn

        class Spy(subprocess.Popen):
            def kill(self):
                calls["kill"] += 1
                return super().kill()

            def terminate(self):
                calls["terminate"] += 1
                return super().terminate()

        def spawn(cmd):
            p = Spy(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    encoding="utf-8", errors="replace")
            calls["procs"].append(p)
            return p

        monkeypatch.setattr(video_transcode, "_spawn", spawn)
        return calls

    def _wait_running_with_part(self, aid):
        t0 = time.time()
        while time.time() - t0 < 20:
            if any(settings.VIDEO_CACHE_DIR.rglob("*.part")):
                return
            time.sleep(0.05)
        raise AssertionError("轉檔沒有開始寫 .part")

    def test_cancel_uses_kill_not_terminate_and_is_fast(self, long_vid, spy):
        aid = _asset(long_vid)
        client.post(f"/api/videos/{aid}/transcode")
        self._wait_running_with_part(aid)
        t0 = time.time()
        r = client.post(f"/api/videos/{aid}/transcode/cancel")
        took = time.time() - t0
        assert r.status_code == 200 and r.json()["status"] == "cancelled"
        assert took < 5, f"取消花了 {took:.1f} 秒（ffmpeg 可能被 SIGTERM 轉完整支）"
        assert spy["kill"] >= 1 and spy["terminate"] == 0
        assert spy["procs"][0].poll() is not None                       # 行程已經結束
        assert not list(settings.VIDEO_CACHE_DIR.rglob("*.part"))       # 半成品已刪
        assert not list(settings.VIDEO_CACHE_DIR.rglob("*.mp4"))        # 也沒有留下 .mp4
        assert client.get(f"/api/videos/{aid}/transcode").json()["status"] == "cancelled"
        assert client.get(f"/api/videos/{aid}/transcoded").status_code == 404

    def test_cancel_is_idempotent(self, long_vid, spy):
        aid = _asset(long_vid)
        client.post(f"/api/videos/{aid}/transcode")
        self._wait_running_with_part(aid)
        assert client.post(f"/api/videos/{aid}/transcode/cancel").json()["status"] == "cancelled"
        assert client.post(f"/api/videos/{aid}/transcode/cancel").json()["status"] == "cancelled"
        assert spy["kill"] == 1

    def test_starting_another_video_cancels_the_previous_one(self, long_vid, avi, spy):
        a1, a2 = _asset(long_vid), _asset(avi)
        client.post(f"/api/videos/{a1}/transcode")
        self._wait_running_with_part(a1)
        client.post(f"/api/videos/{a2}/transcode")
        assert client.get(f"/api/videos/{a1}/transcode").json()["status"] == "cancelled"
        assert _wait(a2)["status"] == "done"
        assert spy["terminate"] == 0 and spy["procs"][0].poll() is not None

    def test_same_video_twice_does_not_start_second_process(self, long_vid, spy):
        aid = _asset(long_vid)
        client.post(f"/api/videos/{aid}/transcode")
        self._wait_running_with_part(aid)
        j = client.post(f"/api/videos/{aid}/transcode").json()
        assert j["status"] == "running" and len(spy["procs"]) == 1
        client.post(f"/api/videos/{aid}/transcode/cancel")

    def test_shutdown_kills_running_ffmpeg(self, long_vid, spy):
        aid = _asset(long_vid)
        client.post(f"/api/videos/{aid}/transcode")
        self._wait_running_with_part(aid)
        video_transcode.shutdown()
        assert spy["procs"][0].poll() is not None and not list(settings.VIDEO_CACHE_DIR.rglob("*.part"))

    def test_cancel_works_even_when_process_has_not_started_yet(self, long_vid, monkeypatch):
        """取消發生在行程建立之前：行程一建立就會被殺。"""
        started = []
        real = video_transcode._spawn

        def slow_spawn(cmd):
            time.sleep(0.5)
            p = real(cmd)
            started.append(p)
            return p

        monkeypatch.setattr(video_transcode, "_spawn", slow_spawn)
        aid = _asset(long_vid)
        client.post(f"/api/videos/{aid}/transcode")
        assert client.post(f"/api/videos/{aid}/transcode/cancel").json()["status"] == "cancelled"
        assert started and started[0].poll() is not None


# ── 快取上限與清理（不需要 ffmpeg）───────────────────────────────
class TestCache:
    def _fake(self, name, size, age_s):
        d = settings.VIDEO_CACHE_DIR / name[:2]
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{name}.mp4"
        f.write_bytes(b"x" * size)
        t = time.time() - age_s
        os.utime(f, (t, t))
        return f

    def test_evicts_least_recently_used_until_under_limit(self, monkeypatch):
        monkeypatch.setattr(settings, "VIDEO_CACHE_MAX_BYTES", 2500)
        old = self._fake("aa_old", 1000, 300)
        mid = self._fake("bb_mid", 1000, 200)
        new = self._fake("cc_new", 1000, 100)
        removed = video_transcode.enforce_cache_limit()
        assert removed == [old.name] and not old.exists() and mid.exists() and new.exists()
        assert video_transcode.cache_usage() == {"bytes": 2000, "files": 2, "limit_bytes": 2500}

    def test_protected_file_is_never_evicted_even_if_alone_over_limit(self, monkeypatch):
        monkeypatch.setattr(settings, "VIDEO_CACHE_MAX_BYTES", 500)
        old = self._fake("aa_old", 400, 300)
        big = self._fake("bb_big", 1000, 1)
        video_transcode.enforce_cache_limit(protect=big)
        assert big.exists() and not old.exists()

    def test_touch_makes_a_file_most_recent(self, monkeypatch):
        monkeypatch.setattr(settings, "VIDEO_CACHE_MAX_BYTES", 1500)
        a = self._fake("aa_a", 1000, 300)
        b = self._fake("bb_b", 1000, 100)
        video_transcode.touch(a)                   # a 剛被用過 → b 變成最舊
        video_transcode.enforce_cache_limit()
        assert a.exists() and not b.exists()

    def test_no_cache_dir_is_fine(self):
        assert video_transcode.enforce_cache_limit() == []
        assert video_transcode.cache_usage()["files"] == 0

    def test_cleanup_partials(self):
        f = self._fake("zz_keep", 10, 1)
        part = f.with_name("zz_other.mp4.part")
        part.write_bytes(b"half")
        assert video_transcode.cleanup_partials() == 1
        assert f.exists() and not part.exists()

    def test_default_limit_is_5gb_and_dir_is_under_data_dir(self):
        assert settings.VIDEO_CACHE_MAX_BYTES == 5 * 1024 ** 3
        assert settings.VIDEO_CACHE_DIR == settings.DATA_DIR / "video_cache"
        assert settings.VIDEO_TRANSCODE_MAX_HEIGHT == 1080

    def test_cache_key_changes_when_source_changes(self):
        a = video_transcode.cache_key("/x.avi", 100, 1)
        assert a != video_transcode.cache_key("/x.avi", 101, 1)
        assert a != video_transcode.cache_key("/x.avi", 100, 2)
        assert a == video_transcode.cache_key("/x.avi", 100, 1)


def test_app_name_and_version():
    assert settings.APP_NAME == "相片清理助手" and settings.APP_VERSION == "0.7.0"
    assert client.get("/api/health").json()["version"] == "0.7.0"


def test_routes_registered():
    paths = app.openapi()["paths"]
    for p in ("/api/videos/ffmpeg", "/api/videos/{asset_id}/info", "/api/videos/{asset_id}/stream",
              "/api/videos/{asset_id}/transcode", "/api/videos/{asset_id}/transcode/cancel",
              "/api/videos/{asset_id}/transcoded"):
        assert p in paths, p


def test_source_never_calls_terminate():
    """鐵則：取消用 proc.kill()（ffmpeg 收到 SIGTERM 會先轉完整支）。"""
    src = Path(video_transcode.__file__).read_text(encoding="utf-8")
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith(("#", '"""')))
    assert ".terminate(" not in code and "proc.kill()" in code
