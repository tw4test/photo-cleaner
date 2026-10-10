"""
Analysis Status API：提供各分析階段的統計與失敗清單查詢。
"""

import logging
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Optional

from app.core.database import get_db
from app.models.schema import (
    Asset, File, Feature, ClipFeature, VideoInfo, AnalysisFailure, Setting
)
from app.api.settings import DEFAULT_SETTINGS
import json

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


async def _get_clip_settings(db: AsyncSession) -> tuple[str, str]:
    """從資料庫讀取目前的 CLIP 模型設定。"""
    model = DEFAULT_SETTINGS.get("clip_model", "ViT-B-16")
    pretrained = DEFAULT_SETTINGS.get("clip_pretrained", "openai")
    try:
        s_model = await db.get(Setting, "clip_model")
        s_pre = await db.get(Setting, "clip_pretrained")
        if s_model:
            model = json.loads(s_model.value)
        if s_pre:
            pretrained = json.loads(s_pre.value)
    except Exception:
        pass
    return model, pretrained


async def _get_compute_device(db: AsyncSession) -> str:
    """從資料庫讀取目前的運算裝置設定。"""
    device = DEFAULT_SETTINGS.get("compute_device", "auto")
    try:
        s = await db.get(Setting, "compute_device")
        if s:
            device = json.loads(s.value)
    except Exception:
        pass
    return device


@router.get("/status")
async def get_analysis_status(db: AsyncSession = Depends(get_db)):
    """
    取得各分析階段的統計資訊，以及目前設定的 CLIP 模型與運算裝置。

    回傳格式：
    {
        "stages": {
            "hash": {"pending": N, "done": N, "not_needed": N, "failed": N},
            "basic": {"pending": N, "done": N, "failed": N},
            "clip": {"pending": N, "done": N, "stale": N, "failed": N},
            "video": {"pending": N, "done": N, "failed": N}
        },
        "clip_model": "ViT-B-16",
        "clip_pretrained": "openai",
        "device": "auto"
    }
    """
    clip_model, clip_pretrained = await _get_clip_settings(db)
    device = await _get_compute_device(db)

    from app.core.database import SyncSessionLocal
    from app.services.stats_service import stats_service
    with SyncSessionLocal() as session:
        stages_stats = stats_service.get_analysis_stage_stats(
            session=session,
            clip_model=clip_model,
            clip_pretrained=clip_pretrained,
        )

    # ── runners 設定 ────────────────────────────────────────────────────────────
    runners_data = {}
    for stage_key in ("hash", "basic", "clip", "video"):
        setting_key = f"{stage_key}_runners"
        default_val = DEFAULT_SETTINGS.get(setting_key, 1)
        try:
            s_runner = await db.get(Setting, setting_key)
            if s_runner:
                runners_data[stage_key] = json.loads(s_runner.value)
            else:
                runners_data[stage_key] = default_val
        except Exception:
            runners_data[stage_key] = default_val

    return {
        "stages": stages_stats,
        "clip_model": clip_model,
        "clip_pretrained": clip_pretrained,
        "device": device,
        "runners": runners_data,
    }


@router.get("/failures")
async def get_analysis_failures(
    stage: Optional[str] = Query(None, description="篩選的分析階段 (hash|basic|clip|video)"),
    db: AsyncSession = Depends(get_db),
):
    """
    取得分析失敗清單。

    可依 stage 篩選（hash、basic、clip、video）。
    回傳包含 asset 路徑與錯誤訊息的列表。
    """
    valid_stages = {"hash", "basic", "clip", "video"}
    if stage and stage not in valid_stages:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail=f"不合法的 stage：{stage!r}")

    q = (
        select(
            AnalysisFailure.id,
            AnalysisFailure.asset_id,
            AnalysisFailure.stage,
            AnalysisFailure.error_msg,
            AnalysisFailure.occurred_at,
            Asset.primary_path,
            Asset.media_type,
        )
        .join(Asset, AnalysisFailure.asset_id == Asset.id)
        .where(Asset.status.notin_(["deleted"]))
    )
    if stage:
        q = q.where(AnalysisFailure.stage == stage)
    q = q.order_by(AnalysisFailure.occurred_at.desc()).limit(500)

    result = await db.execute(q)
    rows = result.all()

    return [
        {
            "id": r.id,
            "asset_id": r.asset_id,
            "stage": r.stage,
            "error_msg": r.error_msg,
            "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
            "primary_path": r.primary_path,
            "media_type": r.media_type,
        }
        for r in rows
    ]
