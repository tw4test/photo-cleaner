"""
v0.6.1：設定儲存「依需要才評估」。

- 沒有變更 → 不寫入、不評估、不檢查任務
- 只改效能類設定（runners、批次大小）→ 只寫入，分析進行中也能儲存
- 改了會影響評估的設定 → 預設評估；任務進行中回 409 且不寫入任何東西
- re_evaluate=false → 只寫入
- 還原預設：只有被還原的設定會影響評估時才評估
"""
import pytest
from fastapi.testclient import TestClient

from app.core.database import SyncSessionLocal, init_db
from app.core.progress import ProgressState, progress_tracker
from app.core.settings_meta import NON_EVALUATION_KEYS, changed_settings, needs_evaluation
from app.main import app
from app.models.schema import Setting

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_settings():
    init_db()
    progress_tracker.state = ProgressState()
    progress_tracker._cancel_requested = False
    with SyncSessionLocal() as s:
        s.query(Setting).delete()
        s.commit()
    yield
    progress_tracker.ensure_finished()


def _get():
    return client.get("/api/settings").json()


def _put(settings, re_evaluate=True):
    return client.put("/api/settings", json={"settings": settings, "re_evaluate": re_evaluate})


def _busy():
    assert progress_tracker.try_start_analyze(["basic"])


class TestPureHelpers:
    def test_unknown_key_counts_as_affecting_evaluation(self):
        assert needs_evaluation(["some_future_setting"])

    def test_performance_keys_do_not_affect_evaluation(self):
        assert not needs_evaluation(["clip_runners", "clip_batch_size", "basic_runners"])
        assert {"hash_runners", "basic_runners", "clip_runners", "video_runners", "clip_batch_size"} <= NON_EVALUATION_KEYS

    def test_changed_settings_drops_equal_values(self):
        cur = _get()
        assert changed_settings(cur, {"blur_threshold": cur["blur_threshold"]}) == {}


class TestSave:
    def test_no_change_is_noop_even_while_busy(self):
        cur = _get()
        _busy()
        r = _put({"blur_threshold": cur["blur_threshold"]})
        assert r.status_code == 200
        assert r.json()["changed"] == [] and r.json()["evaluated"] is False

    def test_runner_change_saves_without_evaluating_even_while_busy(self):
        _busy()
        r = _put({"clip_runners": 3, "clip_batch_size": 64})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["evaluated"] is False and set(body["changed"]) == {"clip_runners", "clip_batch_size"}
        cur = _get()
        assert cur["clip_runners"] == 3 and cur["clip_batch_size"] == 64

    def test_threshold_change_evaluates(self):
        r = _put({"blur_threshold": 123.0})
        assert r.status_code == 200, r.text
        assert r.json()["evaluated"] is True and r.json()["changed"] == ["blur_threshold"]
        assert _get()["blur_threshold"] == 123.0

    def test_threshold_change_refused_while_busy_and_nothing_written(self):
        before = _get()["blur_threshold"]
        _busy()
        r = _put({"blur_threshold": before + 50})
        assert r.status_code == 409
        progress_tracker.ensure_finished()
        assert _get()["blur_threshold"] == before

    def test_threshold_change_without_reevaluate_is_saved_while_busy(self):
        _busy()
        r = _put({"blur_threshold": 77.0}, re_evaluate=False)
        assert r.status_code == 200 and r.json()["evaluated"] is False
        assert _get()["blur_threshold"] == 77.0

    def test_invalid_value_is_422_and_nothing_written(self):
        before = _get()
        r = _put({"phash_threshold": 999})
        assert r.status_code == 422
        assert _get() == before


class TestReset:
    def test_reset_with_only_performance_overrides_does_not_evaluate(self):
        assert _put({"clip_runners": 3}).status_code == 200
        _busy()
        r = client.post("/api/settings/reset")
        assert r.status_code == 200 and r.json()["evaluated"] is False

    def test_reset_with_threshold_override_is_refused_while_busy(self):
        assert _put({"blur_threshold": 55.0}).status_code == 200
        _busy()
        r = client.post("/api/settings/reset")
        assert r.status_code == 409
        progress_tracker.ensure_finished()
        assert _get()["blur_threshold"] == 55.0

    def test_reset_with_threshold_override_evaluates(self):
        assert _put({"blur_threshold": 55.0}).status_code == 200
        r = client.post("/api/settings/reset")
        assert r.status_code == 200 and r.json()["evaluated"] is True


def test_meta_endpoint():
    m = client.get("/api/settings/meta").json()
    assert m["defaults"]["near_dup_method"] in m["choices"]["near_dup_method"]
    assert m["ranges"]["phash_threshold"] == [0, 20]
    assert "clip_runners" in m["non_evaluation_keys"]
