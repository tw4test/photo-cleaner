from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Dict, Any

from app.core.database import get_db
from app.models.schema import Asset, Candidate, ActionLog
from app.models.enums import MediaType, CandidateState, ActionType

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

@router.get("/stats")
async def get_dashboard_stats(db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    # 1. 資產統計
    total_assets = await db.scalar(select(func.count(Asset.id)).where(Asset.status != "deleted")) or 0
    photo_count = await db.scalar(select(func.count(Asset.id)).where(Asset.media_type == MediaType.PHOTO, Asset.status != "deleted")) or 0
    raw_count = await db.scalar(select(func.count(Asset.id)).where(Asset.media_type == MediaType.RAW, Asset.status != "deleted")) or 0
    video_count = await db.scalar(select(func.count(Asset.id)).where(Asset.media_type == MediaType.VIDEO, Asset.status != "deleted")) or 0
    total_size = await db.scalar(select(func.sum(Asset.size_total)).where(Asset.status != "deleted")) or 0

    # 2. 候選統計
    candidates_count = await db.scalar(select(func.count(Candidate.id)).where(Candidate.state == CandidateState.CANDIDATE)) or 0
    exact_dup_count = await db.scalar(select(func.count(Candidate.id)).where(Candidate.rule == "exact_dup", Candidate.state == CandidateState.CANDIDATE)) or 0
    near_dup_count = await db.scalar(select(func.count(Candidate.id)).where(Candidate.rule == "near_dup", Candidate.state == CandidateState.CANDIDATE)) or 0
    blur_count = await db.scalar(select(func.count(Candidate.id)).where(Candidate.rule == "blur", Candidate.state == CandidateState.CANDIDATE)) or 0
    exposure_count = await db.scalar(select(func.count(Candidate.id)).where(Candidate.rule.in_(["over_exposure", "under_exposure"]), Candidate.state == CandidateState.CANDIDATE)) or 0
    burst_count = await db.scalar(select(func.count(Candidate.id)).where(Candidate.rule == "burst", Candidate.state == CandidateState.CANDIDATE)) or 0

    # 可釋放容量 (只計算非 Keeper 且處於 candidate 或 queued 的資產總容量)
    releasable_size_query = (
        select(func.sum(Asset.size_total))
        .join(Candidate, Candidate.asset_id == Asset.id)
        .where(Candidate.is_keeper == False, Candidate.state.in_([CandidateState.CANDIDATE, CandidateState.QUEUED]), Asset.status != "deleted")
    )
    releasable_size = await db.scalar(releasable_size_query) or 0

    # 3. 歷史累計已刪除容量
    deleted_size_query = (
        select(func.sum(Asset.size_total))
        .join(ActionLog, ActionLog.asset_id == Asset.id)
        .where(ActionLog.action == ActionType.DELETE)
    )
    deleted_size = await db.scalar(deleted_size_query) or 0
    deleted_batches = await db.scalar(select(func.count(func.distinct(ActionLog.batch_id)))) or 0

    return {
        "assets": {
            "total": total_assets,
            "photo_count": photo_count,
            "raw_count": raw_count,
            "video_count": video_count,
            "total_size_bytes": total_size,
        },
        "candidates": {
            "total": candidates_count,
            "exact_dup": exact_dup_count,
            "near_dup": near_dup_count,
            "blur": blur_count,
            "exposure": exposure_count,
            "burst": burst_count,
            "releasable_size_bytes": releasable_size,
        },
        "history": {
            "deleted_size_bytes": deleted_size,
            "deleted_batches": deleted_batches,
        }
    }
