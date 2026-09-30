from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, update
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import os

from app.core.database import get_db, SyncSessionLocal
from app.models.schema import Candidate, Asset, Feature, Group, File
from app.models.enums import CandidateState, CandidateRule
from app.services.rules_engine import rules_engine

router = APIRouter(prefix="/api/candidates", tags=["candidates"])

class CandidateItemResponse(BaseModel):
    id: int
    asset_id: int
    filename: str
    primary_path: str
    media_type: str
    size_total: int
    rule: str
    rules: List[str] = []
    reason: str
    reasons: List[str] = []
    score: float
    group_id: Optional[int] = None
    is_keeper: bool
    state: str
    thumbnail_url: str

class BatchStatusUpdate(BaseModel):
    candidate_ids: List[int]
    state: str  # "queued" | "kept" | "candidate"

@router.get("", response_model=List[CandidateItemResponse])
async def list_candidates(
    rule: Optional[str] = Query(None, description="篩選特定規則"),
    state: Optional[str] = Query(None, description="候選狀態: candidate, queued, kept"),
    media_type: Optional[str] = Query(None, description="photo, raw, video"),
    db: AsyncSession = Depends(get_db)
):
    query = (
        select(Candidate, Asset)
        .options(selectinload(Asset.files))
        .join(Asset, Candidate.asset_id == Asset.id)
        .where(Asset.status != "deleted")
    )

    if rule and rule != "all":
        if rule == "exposure":
            query = query.where(Candidate.rule.in_(["over_exposure", "under_exposure"]))
        else:
            query = query.where(Candidate.rule == rule)

    if state:
        query = query.where(Candidate.state == state)

    if media_type:
        query = query.where(Asset.media_type == media_type)

    query = query.order_by(Candidate.group_id.desc().nullslast(), Candidate.score.desc())
    result = await db.execute(query)
    rows = result.all()

    # 以 Asset.id 為單位聚合，避免同一相片因命中多個規則而重複顯示多張卡片
    grouped_by_asset: dict[int, dict] = {}

    for cand, asset in rows:
        if asset.id not in grouped_by_asset:
            filename = os.path.basename(asset.primary_path)

            mtime_val = 0
            if asset.files:
                for f in asset.files:
                    if f.role == "primary":
                        mtime_val = int(f.mtime)
                        break
                if not mtime_val and asset.files:
                    mtime_val = int(asset.files[0].mtime)

            thumb_url = f"/api/thumbnails/{asset.id}"
            if mtime_val > 0:
                thumb_url += f"?v={mtime_val}"

            grouped_by_asset[asset.id] = {
                "id": cand.id,
                "asset_id": asset.id,
                "filename": filename,
                "primary_path": asset.primary_path,
                "media_type": asset.media_type,
                "size_total": asset.size_total,
                "rule": cand.rule,
                "rules": [cand.rule],
                "reason": cand.reason,
                "reasons": [cand.reason],
                "score": cand.score,
                "group_id": cand.group_id,
                "is_keeper": cand.is_keeper,
                "state": cand.state,
                "thumbnail_url": thumb_url
            }
        else:
            entry = grouped_by_asset[asset.id]
            if cand.rule not in entry["rules"]:
                entry["rules"].append(cand.rule)
            if cand.reason not in entry["reasons"]:
                entry["reasons"].append(cand.reason)
            if cand.score > entry["score"]:
                entry["score"] = cand.score
                entry["rule"] = cand.rule
                entry["reason"] = cand.reason
                entry["id"] = cand.id
            if cand.group_id and not entry["group_id"]:
                entry["group_id"] = cand.group_id
            if cand.is_keeper:
                entry["is_keeper"] = True

    candidates_list = [CandidateItemResponse(**v) for v in grouped_by_asset.values()]
    return candidates_list

@router.post("/batch-status")
async def batch_update_status(
    payload: BatchStatusUpdate,
    db: AsyncSession = Depends(get_db)
):
    if payload.state not in ["queued", "kept", "candidate"]:
        raise HTTPException(status_code=400, detail="不合法的狀態值")

    # 在 Sync Session 中檢查鐵則 3：保留最後一張保護
    with SyncSessionLocal() as sync_session:
        target_ids = payload.candidate_ids
        if payload.state == "queued":
            target_ids = rules_engine.ensure_keeper_protection(sync_session, payload.candidate_ids)

        if target_ids:
            # 取得受影響的資產 ID
            asset_ids = [
                r[0] for r in sync_session.query(Candidate.asset_id)
                .filter(Candidate.id.in_(target_ids))
                .distinct()
                .all()
            ]

            # 更新這些資產的所有 Candidate 記錄狀態
            sync_session.query(Candidate).filter(Candidate.asset_id.in_(asset_ids)).update(
                {"state": payload.state}, synchronize_session=False
            )

            # 同步更新 Asset status
            asset_status = "queued" if payload.state == "queued" else "kept" if payload.state == "kept" else "candidate"
            sync_session.query(Asset).filter(Asset.id.in_(asset_ids)).update(
                {"status": asset_status}, synchronize_session=False
            )

            sync_session.commit()

        protected_count = len(payload.candidate_ids) - len(target_ids)

    return {
        "status": "ok",
        "updated_count": len(target_ids),
        "target_state": payload.state,
        "protected_count": protected_count
    }

@router.post("/evaluate")
async def evaluate_rules():
    """重新執行偵測規則引擎"""
    with SyncSessionLocal() as session:
        rules_engine.evaluate_all_rules(session)
    return {"status": "ok", "message": "已重新評估所有規則並產出候選項目"}
