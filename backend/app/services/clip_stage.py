"""
clip_stage.py（v0.5 GPU 批次管線）
=================================

    DB 查詢待處理資產（主執行緒）
      └─► N 條 loader 執行緒：讀 320px 縮圖（無則讀原檔）→ 補邊成正方形 → 正規化
            └─► 依序收集成批（batch_size 張）
                  └─► 單一推理迴圈：fp16 forward（CUDA）→ 零樣本分類
                        └─► writer 執行緒：每批一次 bulk upsert + commit

- runners（clip_runners）= loader 執行緒數；GPU 推理永遠只有一條流水線。
- batch_size：設定 clip_batch_size，0 = 依顯存自動（≥12GB:128、≥6GB:64、其餘 32；CPU:16）。
- 顯存不足（OOM）時自動把批次減半重試同一批，不會產生任何假資料。
- 單張讀圖/推理失敗 → analysis_failures（stage='clip'），可用「重試失敗」再跑。
- 模型或權重與目前設定不同的記錄視為過期，會被重算。
"""
import logging
import queue
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.defaults import get_setting
from app.models.schema import AnalysisFailure, Asset, ClipFeature
from app.services.thumbnail import thumb_key_args, thumbnail_service
from app.stubs.clip_stub import clip_extractor, is_oom_error

logger = logging.getLogger(__name__)

WRITE_QUEUE_MAX = 8


def _get_clip_settings(session) -> Tuple[str, str]:
    return get_setting(session, "clip_model"), get_setting(session, "clip_pretrained")


def _failed_asset_ids(session) -> set:
    return {r.asset_id for r in session.query(AnalysisFailure.asset_id).filter(AnalysisFailure.stage == "clip")}


# ─────────────────────────────────────────────
# Writer
# ─────────────────────────────────────────────
class _Writer(threading.Thread):
    """單一寫入執行緒：接收 (成功列表, 失敗列表)，每批一次交易。"""

    def __init__(self, model: str, pretrained: str):
        super().__init__(name="clip-writer", daemon=True)
        self.q: "queue.Queue[Optional[Tuple[list, list]]]" = queue.Queue(maxsize=WRITE_QUEUE_MAX)
        self.model = model
        self.pretrained = pretrained
        self.error: Optional[BaseException] = None
        self.written = 0
        self.failed_written = 0

    def run(self):
        from app.core.database import SyncSessionLocal
        with SyncSessionLocal() as session:
            while True:
                item = self.q.get()
                if item is None:
                    break
                ok_rows, fail_rows = item
                try:
                    self._write(session, ok_rows, fail_rows)
                    session.commit()
                    self.written += len(ok_rows)
                    self.failed_written += len(fail_rows)
                except BaseException as exc:  # noqa: BLE001
                    session.rollback()
                    logger.exception("[clip_stage] 寫入資料庫失敗")
                    self.error = exc

    def _write(self, session, ok_rows: list, fail_rows: list):
        now = datetime.utcnow()
        if ok_rows:
            values = [
                {
                    "asset_id": aid,
                    "model": self.model,
                    "pretrained": self.pretrained,
                    "dim": int(emb.shape[0]),
                    "embedding": emb.astype(np.float32).tobytes(),
                    "zs": zs,
                    "analyzed_at": now,
                }
                for aid, emb, zs in ok_rows
            ]
            table = ClipFeature.__table__
            for i in range(0, len(values), 500):
                stmt = sqlite_insert(table).values(values[i:i + 500])
                stmt = stmt.on_conflict_do_update(
                    index_elements=[table.c.asset_id],
                    set_={c: stmt.excluded[c] for c in ("model", "pretrained", "dim", "embedding", "zs", "analyzed_at")},
                )
                session.execute(stmt)
            ok_ids = [r[0] for r in ok_rows]
            session.query(AnalysisFailure).filter(
                AnalysisFailure.stage == "clip", AnalysisFailure.asset_id.in_(ok_ids)
            ).delete(synchronize_session=False)
        if fail_rows:
            fail_ids = [r[0] for r in fail_rows]
            session.query(AnalysisFailure).filter(
                AnalysisFailure.stage == "clip", AnalysisFailure.asset_id.in_(fail_ids)
            ).delete(synchronize_session=False)
            session.add_all([
                AnalysisFailure(asset_id=aid, stage="clip", error_msg=msg[:2000], occurred_at=now)
                for aid, msg in fail_rows
            ])

    def put(self, ok_rows: list, fail_rows: list):
        if ok_rows or fail_rows:
            self.q.put((ok_rows, fail_rows))

    def close(self):
        self.q.put(None)
        self.join()


# ─────────────────────────────────────────────
# Loader
# ─────────────────────────────────────────────
def _load_one(item: Dict[str, Any], image_size: int, mean, std):
    """在 loader 執行緒中執行：讀圖 + 前處理。回傳 (asset_id, path, array 或 例外)。"""
    try:
        img = clip_extractor.load_image(item["path"], is_raw=item["is_raw"], thumb_path=item["thumb"])
        arr = clip_extractor.preprocess(img, image_size, mean, std)
        return item["asset_id"], item["path"], arr
    except Exception as exc:  # noqa: BLE001
        return item["asset_id"], item["path"], exc


# ─────────────────────────────────────────────
# Encoder with OOM back-off
# ─────────────────────────────────────────────
class _BatchEncoder:
    def __init__(self, backend, batch_size: int):
        self.backend = backend
        self.batch_size = max(1, batch_size)

    def encode(self, ids: List[int], arrays: List[np.ndarray]):
        """回傳 (成功列表[(id, emb, zs)], 失敗列表[(id, msg)])。"""
        ok, fail = [], []
        i = 0
        while i < len(arrays):
            n = self.batch_size
            chunk_ids, chunk = ids[i:i + n], arrays[i:i + n]
            try:
                emb, zs = clip_extractor.encode_batch(self.backend, np.stack(chunk))
                ok.extend(zip(chunk_ids, emb, zs))
                i += len(chunk)
            except Exception as exc:  # noqa: BLE001
                if is_oom_error(exc) and self.batch_size > 1:
                    if hasattr(self.backend, "empty_cache"):
                        self.backend.empty_cache()
                    self.batch_size = max(1, self.batch_size // 2)
                    logger.warning("[clip_stage] 顯存不足，批次大小降為 %d 後重試", self.batch_size)
                    continue
                if len(chunk) == 1:
                    fail.append((chunk_ids[0], f"{type(exc).__name__}: {exc}"))
                    i += 1
                    continue
                # 非 OOM 錯誤：逐張重跑找出有問題的那張
                for aid, arr in zip(chunk_ids, chunk):
                    try:
                        e1, z1 = clip_extractor.encode_batch(self.backend, arr[None])
                        ok.append((aid, e1[0], z1[0]))
                    except Exception as exc1:  # noqa: BLE001
                        fail.append((aid, f"{type(exc1).__name__}: {exc1}"))
                i += len(chunk)
        return ok, fail


# ─────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────
def run_clip_stage(
    session,
    device: str = "auto",
    retry_failed: bool = False,
    merge_with_basic: bool = False,
    progress_tracker=None,
    runners: Optional[int] = None,
    batch_size: Optional[int] = None,
) -> dict:
    """
    執行 CLIP 階段。回傳：
      processed / done / stale_found / failed / skipped_failed / device_used / batch_size / images_per_sec
    Raises:
      ClipDeviceError（RuntimeError）：指定 cuda 但不可用；模型載入失敗時拋出原始例外。
      InterruptedError：使用者取消。
    """
    if runners is None:
        runners = settings.CLIP_RUNNERS
    runners = max(1, int(runners))
    device_used = clip_extractor.resolve_device(device)
    model, pretrained = _get_clip_settings(session)

    # 1. 篩選待處理資產
    failed_ids = _failed_asset_ids(session)
    assets = (
        session.query(Asset)
        .options(selectinload(Asset.files), selectinload(Asset.clip_feature))
        .filter(Asset.media_type.in_(["photo", "raw"]), Asset.status.notin_(["deleted", "missing"]))
        .all()
    )
    todo: List[Dict[str, Any]] = []
    done = stale = skipped_failed = 0
    for a in assets:
        rec = a.clip_feature
        is_current = rec is not None and rec.model == model and rec.pretrained == pretrained
        if is_current and a.id not in failed_ids:
            done += 1
            continue
        if rec is None and a.id in failed_ids and not retry_failed:
            skipped_failed += 1
            continue
        if rec is not None and not is_current:
            stale += 1
        thumb = thumbnail_service.get_thumbnail_path(**thumb_key_args(a))
        todo.append({
            "asset_id": a.id,
            "path": a.primary_path,
            "is_raw": a.media_type == "raw",
            "thumb": str(thumb) if thumb.exists() else None,
        })
    total = len(todo)
    result = {"processed": 0, "done": done, "stale_found": stale, "failed": 0,
              "skipped_failed": skipped_failed, "device_used": device_used,
              "batch_size": 0, "images_per_sec": 0.0}
    if progress_tracker:
        progress_tracker.set_current_stage("clip", total=total, runners=runners)
    logger.info("[clip_stage] model=%s/%s device=%s 待處理 %d（已完成 %d、過期 %d、略過失敗 %d）",
                model, pretrained, device_used, total, done, stale, skipped_failed)
    if total == 0:
        return result
    session.commit()

    # 2. 載入模型（失敗 → 整個工作失敗，訊息會顯示在進度列）
    if progress_tracker:
        progress_tracker.set_stage_info(stage_label=f"正在載入 CLIP 模型 {model}（{device_used}）...")
    backend = clip_extractor.get_backend(model, pretrained, device_used)
    bs = int(batch_size or get_setting(session, "clip_batch_size") or 0)
    if bs <= 0:
        bs = clip_extractor.recommended_batch_size(device_used)
    encoder = _BatchEncoder(backend, bs)
    result["batch_size"] = bs
    if progress_tracker:
        progress_tracker.set_stage_info(
            stage_label=f"正在執行 AI 語義向量提取與零樣本分類（{device_used.upper()}，批次 {bs}）...",
            device_used=device_used, batch_size=bs,
        )

    writer = _Writer(model, pretrained)
    writer.start()
    processed = failed = 0
    cancelled = False
    t0 = time.time()
    prefetch = max(bs * 3, runners * 4)
    try:
        with ThreadPoolExecutor(max_workers=runners, thread_name_prefix="clip-loader") as pool:
            pending: deque = deque()
            it = iter(todo)

            def _fill():
                while len(pending) < prefetch:
                    try:
                        item = next(it)
                    except StopIteration:
                        return
                    pending.append(pool.submit(_load_one, item, backend.image_size, backend.mean, backend.std))

            _fill()
            batch_ids: List[int] = []
            batch_arrs: List[np.ndarray] = []
            load_fail: List[Tuple[int, str]] = []
            last_path = ""
            while pending or batch_ids:
                if progress_tracker and progress_tracker.is_cancel_requested():
                    cancelled = True
                    break
                flush = not pending
                if pending:
                    aid, path, out = pending.popleft().result()
                    last_path = path
                    _fill()
                    if isinstance(out, Exception):
                        load_fail.append((aid, f"{type(out).__name__}: {out}"))
                    else:
                        batch_ids.append(aid)
                        batch_arrs.append(out)
                    flush = len(batch_ids) >= encoder.batch_size or not pending
                if flush:
                    ok_rows, fail_rows = encoder.encode(batch_ids, batch_arrs) if batch_ids else ([], [])
                    fail_rows = load_fail + fail_rows
                    writer.put(ok_rows, fail_rows)
                    if writer.error:
                        raise RuntimeError(f"寫入 CLIP 結果失敗：{writer.error}")
                    processed += len(ok_rows)
                    failed += len(fail_rows)
                    batch_ids, batch_arrs, load_fail = [], [], []
                    if progress_tracker:
                        try:
                            progress_tracker.update_analyze_progress(processed + failed, total, last_path)
                        except InterruptedError:
                            cancelled = True
                            break
            # 剩下的讀圖失敗（例如最後幾張都失敗）
            if load_fail:
                writer.put([], load_fail)
                failed += len(load_fail)
            if cancelled:
                for f in pending:
                    f.cancel()
    finally:
        writer.close()
    session.expire_all()

    elapsed = max(time.time() - t0, 1e-6)
    result.update({
        "processed": processed,
        "failed": failed,
        "batch_size": encoder.batch_size,
        "images_per_sec": round((processed + failed) / elapsed, 1),
    })
    if writer.error:
        raise RuntimeError(f"寫入 CLIP 結果失敗：{writer.error}")
    logger.info("[clip_stage] 完成：%s", result)
    if cancelled:
        raise InterruptedError("使用者已取消分析任務")
    return result
