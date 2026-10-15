"""
Analysis Status API：提供各分析階段的統計與失敗清單查詢。
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.core.database import SyncSessionLocal
from app.core.defaults import DEFAULT_SETTINGS, get_setting
from app.models.schema import Asset, AnalysisFailure

# 向下相容：舊程式碼從這裡 import DEFAULT_SETTINGS
__all__ = ["router", "DEFAULT_SETTINGS"]

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/analysis", tags=["analysis"])


@router.get("/status")
def get_analysis_status():
    """各分析階段的統計資訊，以及目前的 CLIP 模型、運算裝置與 runner 設定。"""
    from app.services.stats_service import stats_service
    with SyncSessionLocal() as session:
        clip_model = get_setting(session, "clip_model")
        clip_pretrained = get_setting(session, "clip_pretrained")
        stages = stats_service.get_analysis_stage_stats(session, clip_model, clip_pretrained)
        return {
            "stages": stages,
            "clip_model": clip_model,
            "clip_pretrained": clip_pretrained,
            "device": get_setting(session, "compute_device"),
            "clip_batch_size": get_setting(session, "clip_batch_size"),
            "runners": {k: get_setting(session, f"{k}_runners") for k in ("hash", "basic", "clip", "video")},
        }


@router.get("/failures")
def get_analysis_failures(stage: Optional[str] = Query(None, description="hash|basic|clip|video")):
    valid_stages = {"hash", "basic", "clip", "video"}
    if stage and stage not in valid_stages:
        raise HTTPException(status_code=422, detail=f"不合法的 stage：{stage!r}")
    with SyncSessionLocal() as session:
        q = (
            session.query(AnalysisFailure.id, AnalysisFailure.asset_id, AnalysisFailure.stage,
                          AnalysisFailure.error_msg, AnalysisFailure.occurred_at,
                          Asset.primary_path, Asset.media_type)
            .join(Asset, AnalysisFailure.asset_id == Asset.id)
            .filter(Asset.status.notin_(["deleted"]))
        )
        if stage:
            q = q.filter(AnalysisFailure.stage == stage)
        rows = q.order_by(AnalysisFailure.occurred_at.desc()).limit(500).all()
        return [
            {
                "id": r.id, "asset_id": r.asset_id, "stage": r.stage, "error_msg": r.error_msg,
                "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
                "primary_path": r.primary_path, "media_type": r.media_type,
            }
            for r in rows
        ]
