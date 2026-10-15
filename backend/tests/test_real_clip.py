"""
真實 open_clip 模型的煙霧測試（預設略過）。
執行方式（Windows PowerShell，於 backend 目錄）：
    $env:PC_REAL_CLIP="1"; pytest -m real_clip -s
第一次執行會下載模型權重（ViT-B-16 / openai，約 600MB）。
"""
import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.stubs.clip_stub import clip_extractor, CATEGORY_KEYS

pytestmark = pytest.mark.real_clip


def _screenshot_like() -> Image.Image:
    img = Image.new("RGB", (390, 844), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 390, 44], fill=(245, 245, 245))
    d.text((16, 14), "9:41", fill=(0, 0, 0))
    for i in range(10):
        y = 80 + i * 70
        d.rounded_rectangle([20 if i % 2 else 120, y, 270 if i % 2 else 370, y + 50], 18,
                            fill=(230, 230, 235) if i % 2 else (0, 122, 255))
        d.text((40 if i % 2 else 140, y + 18), "Hello, see you tomorrow?", fill=(0, 0, 0))
    return img


@pytest.mark.parametrize("device", ["auto"])
def test_real_model_batch(device):
    dev = clip_extractor.resolve_device(device)
    backend = clip_extractor.get_backend("ViT-B-16", "openai", dev)
    imgs = [_screenshot_like(), Image.new("RGB", (640, 480), (0, 0, 0))]
    batch = np.stack([clip_extractor.preprocess(i, backend.image_size, backend.mean, backend.std) for i in imgs])
    emb, zs = clip_extractor.encode_batch(backend, batch)
    print("device:", dev, "dim:", emb.shape, zs)
    assert emb.shape == (2, backend.embedding_dim)
    assert np.allclose(np.linalg.norm(emb, axis=1), 1.0, atol=1e-2)
    assert set(zs[0]) == set(CATEGORY_KEYS)
    assert max(zs[0], key=zs[0].get) == "screenshot"
