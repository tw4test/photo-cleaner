"""
測試共用設定（v0.5）

1. 測試一律使用暫存資料夾裡的獨立資料庫與縮圖快取（舊版會清空 backend/data/app.db 真實資料庫！）。
   必須在 import 任何 app 模組前設定環境變數。
2. 自動注入假的 CLIP 後端（不需要 PyTorch、不需要下載模型）。
   要測真實模型：設定環境變數 PC_REAL_CLIP=1，並執行 `pytest -m real_clip`。
"""
import os
import shutil
import tempfile
from pathlib import Path

_TEST_ROOT = Path(tempfile.mkdtemp(prefix="photo_cleaner_test_"))
os.environ["DATA_DIR"] = str(_TEST_ROOT / "data")
os.environ["THUMBS_CACHE_DIR"] = str(_TEST_ROOT / "thumbs_cache")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{(_TEST_ROOT / 'data' / 'test.db').as_posix()}"
os.environ["SYNC_DATABASE_URL"] = f"sqlite:///{(_TEST_ROOT / 'data' / 'test.db').as_posix()}"

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from app.core.database import init_db, SyncSessionLocal  # noqa: E402
from app.core.progress import progress_tracker  # noqa: E402
from app.models.schema import (  # noqa: E402
    Root, Asset, File, Feature, ClipFeature, VideoInfo,
    Candidate, Group, ActionLog, AnalysisFailure, Setting
)
from app.stubs.clip_stub import clip_extractor, OPENAI_MEAN, OPENAI_STD  # noqa: E402


class FakeClipBackend:
    """
    可預測的假 CLIP：向量 = 縮小後的像素（相似的圖 → 相似的向量），文字向量以 prompt 雜湊產生。
    """
    image_size = 32
    mean = OPENAI_MEAN
    std = OPENAI_STD
    logit_scale = 100.0
    embedding_dim = 512
    calls = 0
    fail_on_call = None      # 例如設為 1 → 第一次 encode_images 拋出 OOM
    oom_until_batch = None   # 批次 > 此值時拋出 OOM

    def __init__(self, model_name, pretrained, device):
        self.model_name, self.pretrained, self.device = model_name, pretrained, device

    def encode_images(self, batch):
        FakeClipBackend.calls += 1
        if FakeClipBackend.oom_until_batch is not None and len(batch) > FakeClipBackend.oom_until_batch:
            raise RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB")
        n = len(batch)
        small = batch.reshape(n, 3, 8, 4, 8, 4).mean(axis=(3, 5)).reshape(n, -1)  # 192
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


@pytest.fixture(autouse=True)
def fake_clip(request):
    if request.node.get_closest_marker("real_clip"):
        clip_extractor.set_backend_factory(None, cuda_available=None)
        yield None
        return
    FakeClipBackend.calls = 0
    FakeClipBackend.oom_until_batch = None
    clip_extractor.set_backend_factory(FakeClipBackend, cuda_available=False)
    yield FakeClipBackend
    clip_extractor.set_backend_factory(None, cuda_available=None)


@pytest.fixture(autouse=True)
def clean_db():
    init_db()
    with SyncSessionLocal() as session:
        for model in (ActionLog, Candidate, Group, AnalysisFailure, ClipFeature, Feature, VideoInfo,
                      File, Asset, Root, Setting):
            session.query(model).delete()
        session.commit()
    # 每個測試開始時任務追蹤器都是閒置
    progress_tracker.state.is_running = False
    progress_tracker._cancel_requested = False
    yield


def pytest_configure(config):
    config.addinivalue_line("markers", "real_clip: 使用真實 open_clip 模型（需 PC_REAL_CLIP=1）")


def pytest_collection_modifyitems(config, items):
    if os.environ.get("PC_REAL_CLIP") == "1":
        return
    skip = pytest.mark.skip(reason="設定 PC_REAL_CLIP=1 才執行真實 CLIP 測試")
    for item in items:
        if "real_clip" in item.keywords:
            item.add_marker(skip)


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TEST_ROOT, ignore_errors=True)
