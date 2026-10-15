"""
建立功能測試用的相片庫（對應 photo-cleaner-functional-test-plan.md 第 1 節）。

兩種模式：
  1. 合成模式（不需要任何相片）：
        python scripts/make_test_library.py E:\\PCTest
     產生 40 張程式繪製的圖片當作「一般相片」，所有預期數字都可精確計算。

  2. 混合模式（使用你自己的相片，建議 600 張以上）：
        python scripts/make_test_library.py E:\\PCTest --mixed E:\\MyPhotos --max 700
     把相片「複製」到 E:\\PCTest\\A\\mixed（保留子資料夾結構，原檔不會被改動），
     再從中挑幾張 JPG/PNG 製造已知答案的情境（完全重複、近似、模糊…）。

兩種模式都會加入：完全重複（含跨根目錄）、近似重複、模糊、過暗、過曝、壞圖、0 位元組圖、
壞影片、正常影片、Live Photo 配對、排除資料夾 @eaDir、中文與 emoji 路徑。

輸出結構：
  <out>\\A\\...           根目錄 A（優先度 0，排除 @eaDir）
  <out>\\B\\...           根目錄 B（優先度 1）
  <out>\\expected.json    已知答案（測試會讀取）
  <out>\\.pc_test_library 標記檔；只有含此標記的資料夾才允許 --force 覆蓋

接著執行功能測試：
  $env:PC_FUNC_LIB="E:\\PCTest"; pytest tests\\test_functional.py -v
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
from PIL import Image, ImageFilter, ImageOps

MARKER = ".pc_test_library"
PHOTO_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.tiff', '.tif', '.bmp', '.gif', '.heic', '.heif'}
RAW_EXTS = {'.cr2', '.cr3', '.nef', '.arw', '.dng', '.orf', '.rw2', '.pef', '.raf'}
VIDEO_EXTS = {'.mp4', '.mov', '.avi', '.mkv', '.wmv', '.flv', '.webm', '.m4v'}
INJECT_EXTS = {'.jpg', '.jpeg', '.png'}

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:  # pragma: no cover
    pass


# ─────────────────────────────────────────────
# 圖片產生
# ─────────────────────────────────────────────
def texture(seed: int, w: int = 800, h: int = 600) -> Image.Image:
    """隨機色塊 + 細雜訊：彼此差異大、清晰（Laplacian 高）、亮度中等。"""
    rng = np.random.default_rng(seed)
    img = np.empty((h, w, 3), dtype=np.int16)
    img[:] = rng.integers(60, 200, size=3)
    for _ in range(14):
        x0, y0 = int(rng.integers(0, w - 40)), int(rng.integers(0, h - 40))
        x1, y1 = int(rng.integers(x0 + 30, w)), int(rng.integers(y0 + 30, h))
        img[y0:y1, x0:x1] = rng.integers(20, 236, size=3)
    img += rng.integers(-25, 26, size=(h, w, 1), dtype=np.int16)
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))


def noise_image(seed: int, lo: int, hi: int, w: int = 800, h: int = 600) -> Image.Image:
    rng = np.random.default_rng(seed)
    return Image.fromarray(rng.integers(lo, hi + 1, size=(h, w, 3)).astype(np.uint8))


def save_jpg(img: Image.Image, path: Path, quality: int = 92):
    path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(path, format="JPEG", quality=quality)


def write_video(path: Path) -> bool:
    try:
        import cv2
    except Exception:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (160, 120))
    if not vw.isOpened():
        return False
    rng = np.random.default_rng(42)
    for _ in range(30):
        vw.write(rng.integers(0, 255, size=(120, 160, 3)).astype(np.uint8))
    vw.release()
    cap = cv2.VideoCapture(str(path))
    ok = cap.isOpened() and cap.get(cv2.CAP_PROP_FRAME_COUNT) > 0
    cap.release()
    if not ok:
        path.unlink(missing_ok=True)
    return ok


def open_upright(path: Path) -> Optional[Image.Image]:
    try:
        with Image.open(path) as im:
            im.load()
            out = ImageOps.exif_transpose(im).convert("RGB")
        return out if min(out.size) >= 200 else None
    except Exception:
        return None


# ─────────────────────────────────────────────
# 建立相片庫
# ─────────────────────────────────────────────
def _prepare_out(out: Path, force: bool):
    if out.exists() and any(out.iterdir()):
        if not force:
            raise SystemExit(f"輸出資料夾 {out} 不是空的。加上 --force 覆蓋（只限之前由本程式建立的資料夾）。")
        if not (out / MARKER).exists():
            raise SystemExit(f"{out} 沒有 {MARKER} 標記，為了安全不會刪除。請指定新的空資料夾。")
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / MARKER).write_text("photo-cleaner functional test library\n", encoding="utf-8")


def _copy_pool(mixed: Path, dest: Path, max_files: int) -> List[Path]:
    exts = PHOTO_EXTS | RAW_EXTS | VIDEO_EXTS
    picked: List[Path] = []
    for root, dirs, files in os.walk(mixed):
        dirs.sort()
        for name in sorted(files):
            if Path(name).suffix.lower() not in exts:
                continue
            src = Path(root) / name
            dst = dest / src.relative_to(mixed)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            picked.append(dst)
            if len(picked) >= max_files:
                return picked
    return picked


def build(out: Path, mixed: Optional[Path] = None, max_files: int = 700, force: bool = False,
          synthetic_count: int = 40) -> Dict:
    out = Path(out).resolve()
    _prepare_out(out, force)
    A, B = out / "A", out / "B"
    rel = lambda p: Path(p).relative_to(out).as_posix()  # noqa: E731

    # 1. 一般相片池
    pool_dir = A / "mixed"
    if mixed:
        pool = _copy_pool(Path(mixed), pool_dir, max_files)
        mode = "mixed"
    else:
        pool = []
        for i in range(synthetic_count):
            p = pool_dir / f"m{i:03d}.jpg"
            save_jpg(texture(1000 + i), p)
            pool.append(p)
        mode = "synthetic"

    # 2. 製造情境用的來源相片（各不相同）
    # 排除屬於配對的相片（RAW+JPG 的 JPG 不是主檔；Live Photo 的相片帶有 MOV），情境才會單純
    stems: Dict[tuple, int] = {}
    for p in pool:
        k = (str(p.parent).lower(), p.stem.lower())
        stems[k] = stems.get(k, 0) + 1
    sources: List[Path] = []
    for p in sorted(pool):
        if stems[(str(p.parent).lower(), p.stem.lower())] > 1:
            continue
        if p.suffix.lower() in INJECT_EXTS and open_upright(p) is not None:
            sources.append(p)
    # 平均分散取樣，避免都挑到同一個資料夾的連拍
    if len(sources) >= 5:
        step = max(1, len(sources) // 5)
        sources = [sources[i * step] for i in range(5)]
    while len(sources) < 5:   # 相片不夠：補合成圖
        p = pool_dir / f"_src{len(sources)}.jpg"
        save_jpg(texture(5000 + len(sources)), p)
        pool.append(p)
        sources.append(p)

    exp: Dict = {
        "version": 1,
        "mode": mode,
        "roots": [
            {"name": "A", "dir": "A", "priority": 0, "exclude": "@eaDir"},
            {"name": "B", "dir": "B", "priority": 1, "exclude": ""},
        ],
        "pool_count": len(pool),
    }

    # 3. 完全重複（含跨根目錄）
    s0, s1 = sources[0], sources[1]
    g0 = [s0, A / "dup" / f"{s0.stem}_copy1{s0.suffix}", A / "dup" / f"{s0.stem}_copy2{s0.suffix}",
          B / "dup" / f"{s0.stem}_copyB{s0.suffix}"]
    g1 = [s1, B / "dup" / f"{s1.stem}_copyB{s1.suffix}"]
    for g in (g0, g1):
        for dst in g[1:]:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(g[0], dst)
    exp["exact_groups"] = [[rel(p) for p in g0], [rel(p) for p in g1]]

    # 4. 近似重複：縮半 + 低畫質重存
    near = []
    for s in (sources[2], sources[3]):
        img = open_upright(s)
        half = A / "near" / f"{s.stem}_half.jpg"
        q60 = A / "near" / f"{s.stem}_q60.jpg"
        save_jpg(img.resize((img.width // 2, img.height // 2), Image.Resampling.LANCZOS), half)
        save_jpg(img, q60, quality=60)
        near.append({"original": rel(s), "variants": [rel(half), rel(q60)]})
    exp["near_groups"] = near

    # 5. 畫質
    if mode == "mixed":
        b = open_upright(sources[4])
        b.thumbnail((1024, 1024))
    else:
        b = texture(9001)   # 合成模式：用不在相片庫裡的圖，避免與其他圖近似
    save_jpg(b.filter(ImageFilter.GaussianBlur(8)), A / "quality" / "blurry.jpg")
    save_jpg(noise_image(9002, 0, 30), A / "quality" / "dark.jpg")
    save_jpg(noise_image(9003, 225, 255), A / "quality" / "bright.jpg")
    exp["blurry"] = ["A/quality/blurry.jpg"]
    exp["dark"] = ["A/quality/dark.jpg"]
    exp["bright"] = ["A/quality/bright.jpg"]

    # 6. 壞檔
    bad = A / "bad"
    bad.mkdir(parents=True, exist_ok=True)
    (bad / "corrupt.jpg").write_bytes(np.random.default_rng(1).integers(0, 256, 1000).astype(np.uint8).tobytes())
    (bad / "zero.jpg").write_bytes(b"")
    exp["bad_images"] = ["A/bad/corrupt.jpg", "A/bad/zero.jpg"]

    # 7. 影片
    vid = A / "video"
    vid.mkdir(parents=True, exist_ok=True)
    (vid / "broken.mp4").write_bytes(np.random.default_rng(2).integers(0, 256, 4000).astype(np.uint8).tobytes())
    exp["broken_videos"] = ["A/video/broken.mp4"]
    exp["ok_videos"] = ["A/video/ok.avi"] if write_video(vid / "ok.avi") else []

    # 8. Live Photo 配對（MOV 內容不重要，配對檔不會被當成影片分析）
    save_jpg(texture(7001), A / "pairs" / "IMG_9001.JPG")
    (A / "pairs" / "IMG_9001.MOV").write_bytes(b"\x00\x00\x00\x18ftypqt  " + bytes(2000))
    exp["live_pair"] = {"primary": "A/pairs/IMG_9001.JPG", "pair": "A/pairs/IMG_9001.MOV"}

    # 9. 排除資料夾、中文 / emoji 路徑
    save_jpg(texture(7002), A / "@eaDir" / "thumb.jpg")
    exp["excluded"] = ["A/@eaDir/thumb.jpg"]
    uni = A / "深層 資料夾" / "中文 檔名 😀.jpg"
    save_jpg(texture(7003), uni)
    exp["unicode"] = [rel(uni)]

    # 10. 合成模式：精確預期數字
    if mode == "synthetic":
        n_ok_video = len(exp["ok_videos"])
        photos = len(pool) + 3 + 1 + 4 + 3 + 2 + 1 + 1   # pool + dup副本 + B副本 + 近似 + 畫質 + 壞圖 + 配對 + 中文
        videos = 1 + n_ok_video
        exp["counts"] = {
            "assets": photos + videos,
            "photos": photos,
            "raws": 0,
            "videos": videos,
            "files": photos + videos + 1,                 # + Live Photo 的 MOV
            "basic_failed": 2,
            "clip_failed": 2,
            "video_failed": 1,
            "video_done": n_ok_video,
            "exact_dup_non_keepers": 4,
            "near_dup_non_keepers": 4,
            "blur": 1,
            "under_exposure": 1,
            "over_exposure": 1,
        }

    (out / "expected.json").write_text(json.dumps(exp, ensure_ascii=False, indent=2), encoding="utf-8")
    return exp


def main():
    ap = argparse.ArgumentParser(description="建立 Photo Cleaner 功能測試用相片庫")
    ap.add_argument("out", help="輸出資料夾（會建立 A、B 兩個根目錄）")
    ap.add_argument("--mixed", help="你的相片資料夾（只會複製，不會改動）")
    ap.add_argument("--max", type=int, default=700, help="最多複製幾個檔案（預設 700）")
    ap.add_argument("--force", action="store_true", help="覆蓋之前由本程式建立的資料夾")
    a = ap.parse_args()
    exp = build(Path(a.out), Path(a.mixed) if a.mixed else None, a.max, a.force)
    print(f"已建立測試相片庫：{Path(a.out).resolve()}（模式：{exp['mode']}，相片池 {exp['pool_count']} 個檔案）")
    print(f"  影片產生：{'成功' if exp['ok_videos'] else '失敗（略過正常影片情境）'}")
    print(f"下一步： $env:PC_FUNC_LIB=\"{Path(a.out).resolve()}\"; pytest tests\\test_functional.py -v")


if __name__ == "__main__":
    sys.exit(main())
