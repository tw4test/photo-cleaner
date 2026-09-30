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
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

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
            raise RuntimeError(
                "CUDA 不可用：請選擇 CPU 或自動模式。目前系統無法使用 GPU。"
            )
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


def run_clip_stage(
    session,
    device: str = "auto",
    retry_failed: bool = False,
    merge_with_basic: bool = False,
    progress_tracker=None,
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
        session:          SQLAlchemy 同步 Session。
        device:           運算裝置，'auto' | 'cuda' | 'cpu'。
        retry_failed:     若為 True，亦重新處理有失敗記錄的資產。
        merge_with_basic: 若為 True，表示與 basic 階段同次執行，可合併解碼。
        progress_tracker: 可選的進度追蹤物件。

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
    # 1. 解析裝置
    device_used = _resolve_device(device)

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
    if progress_tracker:
        progress_tracker.set_current_stage("clip", total=total_assets)

    # 統計計數器
    processed = 0
    done = 0
    stale_found = 0
    failed = 0

    for idx, asset in enumerate(photo_raw_assets):
        if progress_tracker and progress_tracker.is_cancel_requested():
            logger.info("[clip_stage] 收到使用者取消請求，提前停止")
            break

        # 5. 確認實體檔案存在
        if not Path(asset.primary_path).exists():
            logger.debug(
                "[clip_stage] 跳過 asset_id=%d，檔案不存在：%s",
                asset.id,
                asset.primary_path,
            )
            if progress_tracker:
                progress_tracker.update_analyze_progress(idx + 1, total_assets, asset.primary_path)
            continue

        # 6. 判斷是否需要處理
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
            # 記錄已存在且為最新，跳過
            done += 1
            if progress_tracker:
                progress_tracker.update_analyze_progress(idx + 1, total_assets, asset.primary_path)
            continue

        if is_stale:
            stale_found += 1
            logger.debug(
                "[clip_stage] asset_id=%d 的 CLIP 記錄已過期（model 或 pretrained 不符），重新分析",
                asset.id,
            )

        # 7. 執行 CLIP 特徵提取
        try:
            embedding: bytes = clip_extractor.extract_embedding(
                asset.primary_path,
                model_name=current_model,
                pretrained=current_pretrained,
                device=device_used,
            )
            zs_scores: dict = clip_extractor.classify_zero_shot(
                asset.primary_path,
                _ZS_LABELS,
                model_name=current_model,
                pretrained=current_pretrained,
                device=device_used,
            )
            dim = len(embedding) // 4  # float32 → 每個元素 4 bytes

            # 8. Upsert ClipFeature
            if clip_rec is None:
                clip_rec = ClipFeature(asset_id=asset.id)
                session.add(clip_rec)

            clip_rec.model = current_model
            clip_rec.pretrained = current_pretrained
            clip_rec.dim = dim
            clip_rec.embedding = embedding
            clip_rec.zs = zs_scores
            clip_rec.analyzed_at = datetime.utcnow()

            # 成功後清除失敗記錄
            _clear_analysis_failure(session, asset.id, "clip")

            session.flush()
            processed += 1
            logger.debug(
                "[clip_stage] asset_id=%d 分析完成，dim=%d，zs=%s",
                asset.id,
                dim,
                zs_scores,
            )

        except Exception as exc:
            # 9. 記錄失敗，繼續處理下一個資產
            error_msg = str(exc)
            logger.warning(
                "[clip_stage] asset_id=%d 分析失敗：%s", asset.id, error_msg
            )
            _upsert_analysis_failure(session, asset.id, "clip", error_msg)
            session.flush()
            failed += 1

        finally:
            if progress_tracker:
                progress_tracker.update_analyze_progress(idx + 1, total_assets, asset.primary_path)

    # 10. 統一提交
    session.commit()

    result = {
        "processed": processed,
        "done": done,
        "stale_found": stale_found,
        "failed": failed,
        "device_used": device_used,
    }
    logger.info("[clip_stage] 完成：%s", result)
    return result
