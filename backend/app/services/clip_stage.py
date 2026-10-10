"""
clip_stage.py
=============
CLIP 分析階段服務。

負責對所有照片與 RAW 資產執行 CLIP 特徵提取與零樣本分類，
並將結果 upsert 至 `clip_features` 資料表。

支援以下情境：
- 首次分析（無 `clip_features` 記錄）
- 模型設定變更後的過期記錄重新分析
- 重試先前失敗的資產（`retry_failed=True`）

Multi-threading
---------------
候選資產篩選在主線程完成。實際 CLIP 推理分發至 `runners` 個 worker
執行緒（ThreadPoolExecutor），每個 worker 使用獨立的 SyncSessionLocal
session。進度以 threading.Lock 保護的計數器推進。

⚠️ GPU 注意事項：當 device='cuda' 時，建議 runners=1，
   多 thread 搶占同一 CUDA context 可能導致效能下降或錯誤。
"""

import json
import logging
import math
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.core.config import settings
from app.models.schema import Asset, ClipFeature, AnalysisFailure, File
from app.stubs.clip_stub import clip_extractor

logger = logging.getLogger(__name__)

# 零樣本分類標籤
_ZS_LABELS = ["螢幕截圖", "文件", "誤拍", "梗圖"]


def _get_clip_settings(session) -> tuple[str, str]:
    """
    取得目前生效的 CLIP 模型設定。

    優先讀取資料庫中的使用者設定；若無設定記錄則回退至 DEFAULT_SETTINGS。

    Returns:
        (model, pretrained) 字串元組。
    """
    from app.api.settings import DEFAULT_SETTINGS

    model = DEFAULT_SETTINGS["clip_model"]
    pretrained = DEFAULT_SETTINGS["clip_pretrained"]

    try:
        from app.models.schema import Setting

        s_model = session.query(Setting).get("clip_model")
        s_pre = session.query(Setting).get("clip_pretrained")
        if s_model:
            model = json.loads(s_model.value)
        if s_pre:
            pretrained = json.loads(s_pre.value)
    except Exception:
        pass  # DB 不可用時靜默降級至預設值

    return model, pretrained


def _resolve_device(device: str) -> str:
    """
    解析並驗證目標運算裝置。

    - 'cuda'：強制使用 GPU；若不可用則拋出 RuntimeError。
    - 'auto'：自動選擇，有 GPU 優先用 GPU，否則靜默回退至 CPU。
    - 'cpu'：直接使用 CPU。

    Returns:
        實際使用的裝置字串：'cuda' 或 'cpu'。

    Raises:
        RuntimeError: 當 `device='cuda'` 但 CUDA 不可用時。
    """
    cuda_available = getattr(clip_extractor, "cuda_available", False)

    if device == "cuda":
        if not cuda_available:
            from app.services.cuda_manager import cuda_manager
            hw = cuda_manager.get_cuda_status()
            if hw.get("has_nvidia_gpu"):
                raise RuntimeError(
                    f"偵測到您的電腦配備 {hw.get('gpu_name')}，但目前環境為 CPU 版 PyTorch。"
                    "請在介面點擊「一鍵啟用 GPU 加速」下載安裝 CUDA 套件，或改選「自動」/「CPU」模式。"
                )
            raise RuntimeError("本機未檢測到相容的 NVIDIA GPU，無法強制使用 CUDA 模式。請選擇 CPU 或自動模式。")
        logger.info("[clip_stage] 使用裝置：CUDA（GPU）")
        return "cuda"

    if device == "auto":
        if cuda_available:
            logger.info("[clip_stage] 自動選擇裝置：CUDA（GPU）")
            return "cuda"
        else:
            logger.info("[clip_stage] 自動選擇裝置：CPU（GPU 不可用）")
            return "cpu"

    # device == 'cpu' 或其他未知值，統一使用 CPU
    logger.info("[clip_stage] 使用裝置：CPU")
    return "cpu"


def _get_failed_asset_ids(session, stage: str) -> set[int]:
    """回傳指定階段有失敗記錄的資產 ID 集合。"""
    rows = (
        session.query(AnalysisFailure.asset_id)
        .filter(AnalysisFailure.stage == stage)
        .all()
    )
    return {r.asset_id for r in rows}


def _upsert_analysis_failure(
    session, asset_id: int, stage: str, error_msg: str
) -> None:
    """
    Upsert 分析失敗記錄。

    若同一資產同一階段已有失敗記錄，更新錯誤訊息與時間；
    否則新增一筆記錄。
    """
    existing = (
        session.query(AnalysisFailure)
        .filter_by(asset_id=asset_id, stage=stage)
        .first()
    )
    if existing:
        existing.error_msg = error_msg
        existing.occurred_at = datetime.utcnow()
    else:
        session.add(
            AnalysisFailure(
                asset_id=asset_id,
                stage=stage,
                error_msg=error_msg,
                occurred_at=datetime.utcnow(),
            )
        )


def _clear_analysis_failure(session, asset_id: int, stage: str) -> None:
    """清除指定資產指定階段的失敗記錄（分析成功後呼叫）。"""
    session.query(AnalysisFailure).filter_by(asset_id=asset_id, stage=stage).delete()


def _split_chunks(items: list, n: int) -> list[list]:
    """將 items 均分為最多 n 個非空 chunk。"""
    if not items:
        return []
    n = min(n, len(items))
    size = math.ceil(len(items) / n)
    return [items[i : i + size] for i in range(0, len(items), size)]


# ─────────────────────────────────────────────
# Worker
# ─────────────────────────────────────────────

def _clip_worker(
    asset_ids: list[int],
    current_model: str,
    current_pretrained: str,
    device_used: str,
    failed_ids: set[int],
    cancel_event: threading.Event,
    progress_lock: threading.Lock,
    progress_counter: list,
    total_assets: int,
    progress_tracker,
) -> dict:
    """
    Worker 函式：在獨立 session 中對指定資產列表執行 CLIP 特徵提取。

    Returns
    -------
    dict with keys: processed, done, stale_found, failed
    """
    from app.core.database import SyncSessionLocal

    processed = 0
    done = 0
    stale_found = 0
    failed = 0

    with SyncSessionLocal() as session:
        for asset_id in asset_ids:
            if cancel_event.is_set():
                break

            asset: Optional[Asset] = session.get(Asset, asset_id)
            if asset is None:
                with progress_lock:
                    progress_counter[0] += 1
                continue

            primary_path = asset.primary_path

            # 確認實體檔案存在
            if not Path(primary_path).exists():
                logger.debug(
                    "[clip_stage] 跳過 asset_id=%d，檔案不存在：%s",
                    asset.id, primary_path,
                )
                with progress_lock:
                    progress_counter[0] += 1
                    current = progress_counter[0]
                if progress_tracker:
                    try:
                        progress_tracker.update_analyze_progress(current, total_assets, primary_path)
                    except InterruptedError:
                        cancel_event.set()
                continue

            # 判斷是否需要處理
            clip_rec: Optional[ClipFeature] = asset.clip_feature
            is_missing = clip_rec is None
            is_stale = (
                not is_missing
                and (
                    clip_rec.model != current_model
                    or clip_rec.pretrained != current_pretrained
                )
            )
            is_failed_retry = asset.id in failed_ids

            if not is_missing and not is_stale and not is_failed_retry:
                done += 1
                with progress_lock:
                    progress_counter[0] += 1
                    current = progress_counter[0]
                if progress_tracker:
                    try:
                        progress_tracker.update_analyze_progress(current, total_assets, primary_path)
                    except InterruptedError:
                        cancel_event.set()
                continue

            if is_stale:
                stale_found += 1
                logger.debug(
                    "[clip_stage] asset_id=%d 的 CLIP 記錄已過期（model 或 pretrained 不符），重新分析",
                    asset.id,
                )

            # 執行 CLIP 特徵提取
            try:
                embedding: bytes = clip_extractor.extract_embedding(
                    primary_path,
                    model_name=current_model,
                    pretrained=current_pretrained,
                    device=device_used,
                )
                zs_scores: dict = clip_extractor.classify_zero_shot(
                    primary_path,
                    _ZS_LABELS,
                    model_name=current_model,
                    pretrained=current_pretrained,
                    device=device_used,
                )
                dim = len(embedding) // 4  # float32 → 每個元素 4 bytes

                # Upsert ClipFeature
                if clip_rec is None:
                    clip_rec = ClipFeature(asset_id=asset.id)
                    session.add(clip_rec)

                clip_rec.model = current_model
                clip_rec.pretrained = current_pretrained
                clip_rec.dim = dim
                clip_rec.embedding = embedding
                clip_rec.zs = zs_scores
                clip_rec.analyzed_at = datetime.utcnow()

                _clear_analysis_failure(session, asset.id, "clip")

                session.flush()
                session.commit()
                processed += 1
                logger.debug(
                    "[clip_stage] asset_id=%d 分析完成，dim=%d，zs=%s",
                    asset.id, dim, zs_scores,
                )

            except Exception as exc:
                session.rollback()
                error_msg = str(exc)
                logger.warning(
                    "[clip_stage] asset_id=%d 分析失敗：%s", asset.id, error_msg
                )
                try:
                    _upsert_analysis_failure(session, asset.id, "clip", error_msg)
                    session.flush()
                    session.commit()
                except Exception:
                    session.rollback()
                failed += 1

            finally:
                with progress_lock:
                    progress_counter[0] += 1
                    current = progress_counter[0]
                if progress_tracker:
                    try:
                        progress_tracker.update_analyze_progress(current, total_assets, primary_path)
                    except InterruptedError:
                        cancel_event.set()

    return {"processed": processed, "done": done, "stale_found": stale_found, "failed": failed}


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
) -> dict:
    """
    執行 CLIP 分析階段。

    遍歷所有需要處理的照片／RAW 資產，提取 CLIP 嵌入向量與零樣本分類分數，
    並將結果 upsert 至 `clip_features` 資料表。

    需要分析的資產條件（符合其一即處理）：
    1. 尚無 `clip_features` 記錄
    2. 現有記錄的 model／pretrained 與當前設定不符（過期）
    3. 若 `retry_failed=True`：之前在 'clip' 階段失敗的資產

    跳過條件：
    - `asset.status` 為 'deleted' 或 'missing'
    - `asset.primary_path` 所指向的實體檔案不存在

    Args:
        session:          SQLAlchemy 同步 Session（用於候選資產查詢）。
        device:           運算裝置，'auto' | 'cuda' | 'cpu'。
        retry_failed:     若為 True，亦重新處理有失敗記錄的資產。
        merge_with_basic: 若為 True，表示與 basic 階段同次執行（保留供未來優化用）。
        progress_tracker: 可選的進度追蹤物件。
        runners:          並行 worker 線程數。預設使用 settings.CLIP_RUNNERS。
                          ⚠️ 使用 CUDA 時建議設為 1。

    Returns:
        dict with keys:
            - processed   (int): 本次成功處理的資產數
            - done        (int): 現有且未過期的資產數（跳過）
            - stale_found (int): 偵測到過期記錄的資產數
            - failed      (int): 本次分析失敗的資產數
            - device_used (str): 實際使用的裝置

    Raises:
        RuntimeError: 當 device='cuda' 但 CUDA 不可用時。
    """
    if runners is None:
        runners = settings.CLIP_RUNNERS
    runners = max(1, runners)

    # 1. 解析裝置
    device_used = _resolve_device(device)

    # GPU 模式下自動縮減為 1 runner，避免 CUDA context 衝突
    if device_used == "cuda" and runners > 1:
        logger.info(
            "[clip_stage] 偵測到 CUDA 裝置，自動將 runners 從 %d 調整為 1（GPU 推理單線程最佳）",
            runners,
        )
        runners = 1

    # 2. 取得目前 CLIP 模型設定
    current_model, current_pretrained = _get_clip_settings(session)
    logger.info(
        "[clip_stage] 模型設定：model=%s, pretrained=%s", current_model, current_pretrained
    )

    # 3. 取得有 CLIP 失敗記錄的資產 ID（若需重試）
    failed_ids: set[int] = set()
    if retry_failed:
        failed_ids = _get_failed_asset_ids(session, "clip")
        logger.info("[clip_stage] 找到 %d 筆先前 CLIP 失敗記錄，將重試", len(failed_ids))

    # 4. 查詢所有非 video 類型、且未刪除/遺失的資產
    photo_raw_assets: list[Asset] = (
        session.query(Asset)
        .filter(
            Asset.media_type.in_(["photo", "raw"]),
            Asset.status.notin_(["deleted", "missing"]),
        )
        .all()
    )

    total_assets = len(photo_raw_assets)
    asset_ids = [a.id for a in photo_raw_assets]

    if progress_tracker:
        progress_tracker.set_current_stage("clip", total=total_assets)

    logger.info(
        "[clip_stage] %d 個候選資產，使用 %d runner(s)，裝置：%s",
        total_assets, runners, device_used,
    )

    if total_assets == 0:
        result = {"processed": 0, "done": 0, "stale_found": 0, "failed": 0, "device_used": device_used}
        logger.info("[clip_stage] 完成：%s", result)
        return result

    # 5. 並行執行前 commit，確保 worker session 可見主 session 的最新資料
    session.commit()

    cancel_event = threading.Event()
    if progress_tracker and progress_tracker.is_cancel_requested():
        cancel_event.set()

    progress_lock = threading.Lock()
    progress_counter = [0]

    chunks = _split_chunks(asset_ids, runners)
    actual_runners = len(chunks)
    logger.info(
        "[clip_stage] dispatching %d asset(s) across %d thread(s)",
        total_assets, actual_runners,
    )

    # 統計計數器（aggregate）
    total_processed = 0
    total_done = 0
    total_stale = 0
    total_failed = 0

    with ThreadPoolExecutor(max_workers=actual_runners) as executor:
        futures = [
            executor.submit(
                _clip_worker,
                chunk,
                current_model,
                current_pretrained,
                device_used,
                failed_ids,
                cancel_event,
                progress_lock,
                progress_counter,
                total_assets,
                progress_tracker,
            )
            for chunk in chunks
        ]
        for future in as_completed(futures):
            try:
                r = future.result()
                total_processed += r["processed"]
                total_done += r["done"]
                total_stale += r["stale_found"]
                total_failed += r["failed"]
            except Exception as exc:
                logger.error("[clip_stage] worker raised unexpected error: %s", exc, exc_info=True)

    # Worker 在獨立 session 中 commit，主 session 的 identity map 已過期。
    session.expire_all()

    result = {
        "processed": total_processed,
        "done": total_done,
        "stale_found": total_stale,
        "failed": total_failed,
        "device_used": device_used,
    }
    logger.info("[clip_stage] 完成：%s", result)
    return result
