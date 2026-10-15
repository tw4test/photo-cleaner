"""
CLIP 批次推理測速與 GPU 診斷（不寫資料庫，不影響現有資料）。

用法（backend 目錄，已啟用 venv）：
    python scripts/bench_clip.py E:\\DCIM\\Test01
    python scripts/bench_clip.py E:\\DCIM\\Test01 --limit 2000 --batch 64 --loaders 8
    $env:PC_CLIP_PRECISION="fp16"; python scripts/bench_clip.py E:\\DCIM\\Test01   # 強制指定精度

輸出分五部分：
  1. 環境：GPU 型號、算力版本、顯存、PyTorch / CUDA 版本
  2. 讀圖 + 前處理速度（實際相片）
  3. 純 GPU 運算速度（使用自動選出的精度）→ 判斷 GPU 本身快不快
  4. 主機 → GPU 傳輸速度（一般複製 vs pin_memory）→ 判斷是否卡在傳輸
  5. 實際管線速度（與 clip 階段相同的呼叫路徑）
最後依結果給出 10 萬張預估時間與瓶頸判斷。

相片數量少（例如 < 500 張）也沒關係：第 3、4、5 部分會重複執行固定次數取得穩定數字。
"""
import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.scanner import SUPPORTED_PHOTO_EXTS, SUPPORTED_RAW_EXTS  # noqa: E402
from app.services.thumbnail import thumbnail_service  # noqa: E402
from app.stubs.clip_stub import clip_extractor, is_oom_error, needs_quick_gelu  # noqa: E402

MIN_ITEMS = 512   # 第 3–5 部分至少處理這麼多張（不足時重複使用已讀入的相片）


def _collect(folder, limit):
    exts = SUPPORTED_PHOTO_EXTS | SUPPORTED_RAW_EXTS
    files = []
    for root, _, names in os.walk(folder):
        for n in sorted(names):
            if Path(n).suffix.lower() in exts:
                files.append(os.path.join(root, n))
                if len(files) >= limit:
                    return files
    return files


def _sync(torch, dev):
    if dev == "cuda":
        torch.cuda.synchronize()


def _timed_rate(fn, n_items, torch, dev, repeats=1):
    _sync(torch, dev)
    t = time.perf_counter()
    for _ in range(repeats):
        fn()
    _sync(torch, dev)
    dt = time.perf_counter() - t
    return n_items * repeats / dt, dt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--limit", type=int, default=2000)
    ap.add_argument("--batch", type=int, default=0, help="0 = 自動")
    ap.add_argument("--loaders", type=int, default=max(2, (os.cpu_count() or 4) // 2))
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    ap.add_argument("--model", default="ViT-B-16")
    ap.add_argument("--pretrained", default="openai")
    ap.add_argument("--from-originals", action="store_true", help="不使用縮圖快取，直接讀原檔")
    a = ap.parse_args()

    import torch

    files = _collect(a.folder, a.limit)
    if not files:
        print("找不到相片")
        return
    dev = clip_extractor.resolve_device(a.device)

    # ── 1. 環境 ──────────────────────────────────────────
    print("=" * 70)
    print("1. 環境")
    print(f"   PyTorch {torch.__version__}，CUDA（編譯）{torch.version.cuda}，cuDNN {torch.backends.cudnn.version()}")
    if dev == "cuda":
        p = torch.cuda.get_device_properties(0)
        print(f"   GPU：{p.name}，算力 {p.major}.{p.minor}，顯存 {p.total_memory / 1024 ** 3:.1f} GB，"
              f"SM {p.multi_processor_count}")
    else:
        print("   使用 CPU")
    t = time.perf_counter()
    backend = clip_extractor.get_backend(a.model, a.pretrained, dev)
    print(f"   模型 {a.model}/{a.pretrained} 載入於 {dev}，耗時 {time.perf_counter() - t:.1f}s"
          f"（含精度實測），QuickGELU={needs_quick_gelu(a.model, a.pretrained)}")
    print(f"   精度：{backend.precision}"
          + (f"（自動選擇；實測 fp16 {backend.precision_bench['fp16']} 張/秒、"
             f"fp32 {backend.precision_bench['fp32']} 張/秒）" if backend.precision_bench else "（指定或 CPU）"))
    bs = a.batch or clip_extractor.recommended_batch_size(dev)
    S = backend.image_size

    # ── 2. 讀圖 ──────────────────────────────────────────
    print("=" * 70)
    print(f"2. 讀圖 + 前處理（{len(files)} 張，loader {a.loaders}）")

    def load(path):
        thumb = None
        if not a.from_originals:
            tp = thumbnail_service.get_thumbnail_path(path)
            thumb = str(tp) if tp.exists() else None
        try:
            img = clip_extractor.load_image(path, is_raw=Path(path).suffix.lower() in SUPPORTED_RAW_EXTS,
                                            thumb_path=thumb)
            return clip_extractor.preprocess(img, S, backend.mean, backend.std), thumb is not None
        except Exception:
            return None, False

    t = time.perf_counter()
    with ThreadPoolExecutor(a.loaders) as pool:
        loaded = list(pool.map(load, files))
    t_load = time.perf_counter() - t
    arrays = [x for x, _ in loaded if x is not None]
    hits = sum(1 for _, h in loaded if h)
    load_rate = len(arrays) / t_load
    print(f"   {len(arrays)} 張 / {t_load:.1f}s = {load_rate:.1f} 張/秒（縮圖命中 {hits}，失敗 {len(files) - len(arrays)}）")
    if not arrays:
        return
    if hits == 0 and not a.from_originals:
        print("   ⚠ 沒有命中縮圖：這是讀原檔的速度。先跑 basic 階段後，clip 階段讀的是 320px 縮圖，會快很多。")
    while len(arrays) < MIN_ITEMS:
        arrays = arrays + arrays
    arrays = arrays[:max(MIN_ITEMS, (len(arrays) // bs) * bs)]
    n = len(arrays)
    host_batches = [np.stack(arrays[i:i + bs]) for i in range(0, n, bs)]

    # ── 3. 純 GPU 運算 ───────────────────────────────────
    print("=" * 70)
    print(f"3. 純運算速度（資料已在{'GPU' if dev == 'cuda' else '記憶體'}上，{backend.precision}，批次 {bs}，共 {n} 張）")
    model = backend.model
    try:
        x = torch.from_numpy(host_batches[0]).to(dev, dtype=backend.dtype)
        if dev == "cuda":
            torch.cuda.reset_peak_memory_stats()
        with torch.inference_mode():
            model.encode_image(x)
            gpu_rate, _ = _timed_rate(lambda: model.encode_image(x), bs, torch, dev, repeats=len(host_batches))
        print(f"   {gpu_rate:.1f} 張/秒")
        if dev == "cuda":
            print(f"   峰值顯存：{torch.cuda.max_memory_allocated() / 1024 ** 2:.0f} MB")
        del x
    except Exception as e:
        if is_oom_error(e):
            print(f"   批次 {bs} 顯存不足，請改用 --batch {max(1, bs // 2)}")
            return
        raise

    # ── 4. 傳輸 ──────────────────────────────────────────
    xfer_plain = xfer_pinned = None
    if dev == "cuda":
        print("=" * 70)
        print("4. 主機 → GPU 傳輸")
        it = iter(range(10 ** 9))

        def plain():
            torch.from_numpy(host_batches[next(it) % len(host_batches)]).to("cuda", dtype=backend.dtype)

        def pinned():
            torch.from_numpy(host_batches[next(it) % len(host_batches)]).pin_memory().to(
                "cuda", dtype=backend.dtype, non_blocking=True)

        plain()
        xfer_plain, _ = _timed_rate(plain, bs, torch, dev, repeats=len(host_batches))
        xfer_pinned, _ = _timed_rate(pinned, bs, torch, dev, repeats=len(host_batches))
        print(f"   一般複製：{xfer_plain:.0f} 張/秒")
        print(f"   pin_memory：{xfer_pinned:.0f} 張/秒（對照，未使用）")

    # ── 5. 實際管線 ──────────────────────────────────────
    print("=" * 70)
    print("5. 實際管線（clip_extractor.encode_batch，與 clip 階段相同）")
    clip_extractor.encode_batch(backend, host_batches[0])
    cur = bs
    t = time.perf_counter()
    for hb in host_batches:
        i = 0
        while i < len(hb):
            try:
                clip_extractor.encode_batch(backend, hb[i:i + cur])
                i += cur
            except Exception as e:
                if is_oom_error(e) and cur > 1:
                    cur //= 2
                    print(f"   顯存不足 → 批次降為 {cur}")
                    continue
                raise
    _sync(torch, dev)
    pipe_rate = n / (time.perf_counter() - t)
    print(f"   {pipe_rate:.1f} 張/秒（批次 {cur}）")

    # ── 結論 ─────────────────────────────────────────────
    print("=" * 70)
    e2e = min(load_rate, pipe_rate)
    print(f"預估 clip 階段整體速度：約 {e2e:.1f} 張/秒 → 10 萬張約 {100000 / e2e / 60:.0f} 分鐘")
    print("瓶頸判斷：")
    if pipe_rate < gpu_rate * 0.5:
        print(f"   • 實際管線只有純運算的 {pipe_rate / gpu_rate:.0%}：時間花在 GPU 運算以外（傳輸 / 前後處理）。")
    if load_rate < pipe_rate:
        print("   • 讀圖比推理慢：先跑 basic 產生縮圖、或加大 --loaders。")
    else:
        print("   • GPU 推理是瓶頸。")
    print("請把以上全部輸出貼給我。")


if __name__ == "__main__":
    main()
