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
    # 1. 總資產統計 (依據不重複 Asset 計算)
    total_assets = await db.scalar(select(func.count(Asset.id)).where(Asset.status != "deleted")) or 0
    photo_count = await db.scalar(select(func.count(Asset.id)).where(Asset.media_type == MediaType.PHOTO, Asset.status != "deleted")) or 0
    raw_count = await db.scalar(select(func.count(Asset.id)).where(Asset.media_type == MediaType.RAW, Asset.status != "deleted")) or 0
    video_count = await db.scalar(select(func.count(Asset.id)).where(Asset.media_type == MediaType.VIDEO, Asset.status != "deleted")) or 0
    total_size = await db.scalar(select(func.sum(Asset.size_total)).where(Asset.status != "deleted")) or 0

    # 2. 候選清理項目統計 (依據不重複且建議刪除的 Asset 計算，排除建議保留的 Keeper)
    candidates_count_query = (
        select(func.count(func.distinct(Candidate.asset_id)))
        .join(Asset, Candidate.asset_id == Asset.id)
        .where(
            Candidate.is_keeper == False,
            Candidate.state == CandidateState.CANDIDATE,
            Asset.status != "deleted"
        )
    )
    candidates_count = await db.scalar(candidates_count_query) or 0

    # 各分類候選相片張數 (不重複資產)
    async def get_rule_distinct_count(rule_name_or_list):
        if isinstance(rule_name_or_list, list):
            condition = Candidate.rule.in_(rule_name_or_list)
        else:
            condition = (Candidate.rule == rule_name_or_list)
            
        q = (
            select(func.count(func.distinct(Candidate.asset_id)))
            .join(Asset, Candidate.asset_id == Asset.id)
            .where(
                condition,
                Candidate.is_keeper == False,
                Candidate.state == CandidateState.CANDIDATE,
                Asset.status != "deleted"
            )
        )
        return await db.scalar(q) or 0

    screenshot_count = await get_rule_distinct_count("screenshot")
    document_count = await get_rule_distinct_count("document")
    meme_count = await get_rule_distinct_count("meme")
    mistouch_count = await get_rule_distinct_count("mistouch")

    exact_dup_count = await get_rule_distinct_count("exact_dup")
    near_dup_count = await get_rule_distinct_count("near_dup")
    blur_count = await get_rule_distinct_count("blur")
    exposure_count = await get_rule_distinct_count(["over_exposure", "under_exposure"])

    # 3. 預計可釋放容量 (只計算非 Keeper 且處於 candidate 或 queued 的資產總容量)
    releasable_size_query = (
        select(func.sum(func.distinct(Asset.size_total)))
        .join(Candidate, Candidate.asset_id == Asset.id)
        .where(
            Candidate.is_keeper == False,
            Candidate.state.in_([CandidateState.CANDIDATE, CandidateState.QUEUED]),
            Asset.status != "deleted"
        )
    )
    releasable_size = await db.scalar(releasable_size_query) or 0

    # 4. 歷史累計已刪除容量
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
            "screenshot": screenshot_count,
            "document": document_count,
            "meme": meme_count,
            "mistouch": mistouch_count,
            "exact_dup": exact_dup_count,
            "near_dup": near_dup_count,
            "blur": blur_count,
            "exposure": exposure_count,
            "releasable_size_bytes": releasable_size,
        },
        "history": {
            "deleted_size_bytes": deleted_size,
            "deleted_batches": deleted_batches,
        }
    }
