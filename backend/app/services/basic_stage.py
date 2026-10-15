"""
basic_stage.py（v0.5）
──────────────────────
縮圖 + 基本特徵。每張相片只讀取、解碼一次：
  1. 解碼來源：RAW+JPG 用伴生 JPG，其餘用主檔（RAW 取內嵌預覽）；JPEG 以 draft 降解析度解碼。
  2. 依 EXIF 轉正後，同一張影像同時用來寫縮圖快取與計算 pHash / 模糊 / 亮度。
  3. 失敗（含讀不到圖）→ decode_status='error' + analysis_failures，不寫任何假特徵。

多執行緒：候選篩選在主執行緒；解碼與特徵分散到 runners 個 worker，
每個 worker 使用自己的 SyncSessionLocal session，每 COMMIT_EVERY 筆 commit 一次。
"""
import logging
import math
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.models.schema import Asset, Feature, AnalysisFailure
from app.services.thumbnail import thumbnail_service, thumb_key_args
from app.services.feature_pipeline import feature_pipeline, ANALYSIS_MAX_SIDE

logger = logging.getLogger(__name__)

COMMIT_EVERY = 50


def _thumb_exists(asset: Asset) -> bool:
    return thumbnail_service.has_thumbnail(**thumb_key_args(asset))


def _upsert_failure(session: Session, asset_id: int, stage: str, msg: str) -> None:
    session.query(AnalysisFailure).filter(
        AnalysisFailure.asset_id == asset_id,
        AnalysisFailure.stage == stage,
    ).delete(synchronize_session=False)
    session.add(AnalysisFailure(asset_id=asset_id, stage=stage, error_msg=msg[:2000],
                                occurred_at=datetime.utcnow()))


def _needs_processing(asset: Asset, retry_failed: bool) -> bool:
    feat: Optional[Feature] = asset.features
    if feat is None:
        return True
    if feat.decode_status == "error":
        return retry_failed
    if not _thumb_exists(asset):
        return True
    return False


def _split_chunks(items: list, n: int) -> list[list]:
    if not items:
        return []
    n = min(n, len(items))
    size = math.ceil(len(items) / n)
    return [items[i: i + size] for i in range(0, len(items), size)]


def compute_asset_basic(asset: Asset) -> dict:
    """解碼 → 寫縮圖 → 計算特徵（不碰資料庫；失敗拋出例外）。"""
    args = thumb_key_args(asset)
    companion = args["companion_path"]
    if companion and Path(companion).exists():
        source, is_raw = companion, False
    else:
        source, is_raw = asset.primary_path, asset.media_type == "raw"
    img, meta = feature_pipeline.open_image(source, is_raw=is_raw, max_side=ANALYSIS_MAX_SIDE)
    thumbnail_service.save_from_image(img, **args)
    return feature_pipeline.extract_basic(source, is_raw=is_raw, preloaded_image=img, meta=meta)


def apply_asset_basic(session: Session, asset: Asset, feats: dict) -> None:
    feat_record: Optional[Feature] = asset.features
    if feat_record is None:
        feat_record = Feature(asset_id=asset.id)
        session.add(feat_record)
    feat_record.phash = feats["phash"]
    feat_record.blur_score = feats["blur_score"]
    feat_record.brightness = feats["brightness"]
    feat_record.width = feats["width"]
    feat_record.height = feats["height"]
    feat_record.taken_at = feats["taken_at"]
    feat_record.exif = feats["exif"]
    feat_record.decode_status = "ok"
    feat_record.analyzed_at = datetime.utcnow()
    if asset.status == "unanalysed":
        asset.status = "analysed"
    session.query(AnalysisFailure).filter(
        AnalysisFailure.asset_id == asset.id,
        AnalysisFailure.stage == "basic",
    ).delete(synchronize_session=False)


def _record_basic_failure(session: Session, asset_id: int, exc: Exception) -> None:
    asset = session.get(Asset, asset_id)
    if asset is None:
        return
    feat_record = asset.features
    if feat_record is None:
        feat_record = Feature(asset_id=asset_id)
        session.add(feat_record)
    # 失敗時清空所有特徵，確保不會有殘留或假的 pHash 參與比對
    feat_record.phash = None
    feat_record.blur_score = None
    feat_record.brightness = None
    feat_record.decode_status = "error"
    feat_record.analyzed_at = datetime.utcnow()
    _upsert_failure(session, asset_id, "basic", f"{type(exc).__name__}: {exc}")


def _flush_buffer(session: Session, buffer: list) -> None:
    """
    把記憶體中累積的結果一次寫入並 commit。
    寫入交易只在這裡短暫開啟，解碼期間不持有 SQLite 寫入鎖，多個 worker 才能真正並行。
    """
    if not buffer:
        return
    try:
        for kind, asset, payload in buffer:
            if kind == "ok":
                apply_asset_basic(session, asset, payload)
            else:
                _record_basic_failure(session, asset.id, payload)
        session.commit()
    except Exception:
        # 例如資料庫長時間被鎖：放棄這一批，下次執行時這些資產仍會被視為待處理
        logger.exception("basic_stage: 寫入失敗，本批 %d 筆結果捨棄，下次重跑", len(buffer))
        session.rollback()
    buffer.clear()


def _basic_worker(asset_ids, cancel_event, progress_lock, progress_counter, total_assets, progress_tracker) -> dict:
    from app.core.database import SyncSessionLocal
    done = failed = 0
    buffer: list = []
    with SyncSessionLocal() as session:
        for asset_id in asset_ids:
            if cancel_event.is_set():
                break
            asset: Optional[Asset] = (
                session.query(Asset)
                .options(selectinload(Asset.files), selectinload(Asset.features))
                .filter(Asset.id == asset_id).first()
            )
            if asset is None:
                continue
            primary_path = asset.primary_path
            try:
                feats = compute_asset_basic(asset)
                buffer.append(("ok", asset, feats))
                done += 1
            except Exception as exc:
                logger.warning("basic_stage: asset %s (%s) 失敗：%s", asset_id, primary_path, exc)
                buffer.append(("fail", asset, exc))
                failed += 1
            if len(buffer) >= COMMIT_EVERY:
                _flush_buffer(session, buffer)
            with progress_lock:
                progress_counter[0] += 1
                current = progress_counter[0]
            if progress_tracker:
                try:
                    progress_tracker.update_analyze_progress(current, total_assets, primary_path)
                except InterruptedError:
                    cancel_event.set()
        _flush_buffer(session, buffer)
    return {"done": done, "failed": failed}


def run_basic_stage(
    session: Session,
    retry_failed: bool = False,
    merge_with_clip: bool = False,
    progress_tracker=None,
    runners: Optional[int] = None,
) -> dict:
    """
    執行 basic 階段。回傳 {processed, done, failed}。
    merge_with_clip：保留參數相容；v0.5 起 clip 直接讀 basic 產生的縮圖，不需要合併解碼。
    """
    if runners is None:
        runners = settings.BASIC_RUNNERS
    runners = max(1, runners)
    stats = {"processed": 0, "done": 0, "failed": 0}

    candidates = (
        session.query(Asset)
        .options(selectinload(Asset.files), selectinload(Asset.features))
        .filter(Asset.media_type.in_(["photo", "raw"]), Asset.status.notin_(["deleted", "missing"]))
        .all()
    )
    to_process_ids = [a.id for a in candidates if _needs_processing(a, retry_failed)]
    total_to_process = len(to_process_ids)
    if progress_tracker:
        progress_tracker.set_current_stage("basic", total=total_to_process, runners=runners)
    logger.info("basic_stage: %d to process (of %d), %d runner(s)", total_to_process, len(candidates), runners)
    if total_to_process == 0:
        return stats

    session.commit()
    cancel_event = threading.Event()
    if progress_tracker and progress_tracker.is_cancel_requested():
        cancel_event.set()
    progress_lock = threading.Lock()
    progress_counter = [0]
    chunks = _split_chunks(to_process_ids, runners)
    with ThreadPoolExecutor(max_workers=len(chunks)) as executor:
        futures = [
            executor.submit(_basic_worker, chunk, cancel_event, progress_lock, progress_counter,
                            total_to_process, progress_tracker)
            for chunk in chunks
        ]
        for future in as_completed(futures):
            try:
                result = future.result()
                stats["done"] += result["done"]
                stats["failed"] += result["failed"]
                stats["processed"] += result["done"] + result["failed"]
            except Exception as exc:
                logger.error("basic_stage: worker raised unexpected error: %s", exc, exc_info=True)
    session.expire_all()
    if cancel_event.is_set() and progress_tracker:
        raise InterruptedError("使用者已取消分析任務")
    logger.info("basic_stage complete — %s", stats)
    return stats


def run_rebuild_thumbs_only(session: Session, progress_tracker=None) -> dict:
    """補算缺少縮圖的相片（已有 basic 特徵者），不重算其他特徵。"""
    stats = {"processed": 0, "done": 0, "failed": 0}
    assets = (
        session.query(Asset)
        .options(selectinload(Asset.files), selectinload(Asset.features))
        .filter(Asset.media_type.in_(["photo", "raw"]), Asset.status.notin_(["deleted", "missing"]))
        .all()
    )
    targets = [a for a in assets if a.features is not None and a.features.decode_status == "ok"
               and not _thumb_exists(a)]
    total = len(targets)
    if progress_tracker:
        progress_tracker.set_current_stage("processing", total=total,
                                           label=f"正在補算缺少的縮圖（共 {total} 張）...")
    for idx, asset in enumerate(targets):
        if progress_tracker is not None and progress_tracker.is_cancel_requested():
            break
        stats["processed"] += 1
        try:
            out = thumbnail_service.generate_thumbnail(**thumb_key_args(asset))
            if out is None:
                raise ValueError("縮圖產生失敗")
            stats["done"] += 1
        except Exception as exc:
            logger.warning("rebuild_thumbs: failed for %s — %s", asset.primary_path, exc)
            stats["failed"] += 1
        finally:
            if progress_tracker:
                try:
                    progress_tracker.update_analyze_progress(idx + 1, total, asset.primary_path)
                except InterruptedError:
                    break
    return stats
