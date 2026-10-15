import os
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db, SyncSessionLocal
from app.models.schema import Candidate, Asset
from app.services.rules_engine import rules_engine
from app.services.thumbnail import thumb_key_args

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
    candidate_ids: List[int] = []
    asset_ids: List[int] = []
    state: str  # "queued" | "kept" | "candidate"


@router.get("", response_model=List[CandidateItemResponse])
async def list_candidates(
    rule: Optional[str] = Query(None, description="篩選特定規則"),
    state: Optional[str] = Query(None, description="候選狀態: candidate, queued, kept"),
    media_type: Optional[str] = Query(None, description="photo, raw, video"),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(Candidate, Asset)
        .options(selectinload(Asset.files))
        .join(Asset, Candidate.asset_id == Asset.id)
        .where(Asset.status.notin_(["deleted", "missing"]))
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
    rows = (await db.execute(query)).all()

    # 以資產為單位聚合：同一張相片命中多個規則時只顯示一張卡片
    grouped: dict[int, dict] = {}
    for cand, asset in rows:
        if asset.id not in grouped:
            mtime = thumb_key_args(asset)["mtime"]
            thumb_url = f"/api/thumbnails/{asset.id}" + (f"?v={int(mtime)}" if mtime else "")
            grouped[asset.id] = {
                "id": cand.id, "asset_id": asset.id, "filename": os.path.basename(asset.primary_path),
                "primary_path": asset.primary_path, "media_type": asset.media_type,
                "size_total": asset.size_total, "rule": cand.rule, "rules": [cand.rule],
                "reason": cand.reason, "reasons": [cand.reason], "score": cand.score,
                "group_id": cand.group_id, "is_keeper": cand.is_keeper, "state": cand.state,
                "thumbnail_url": thumb_url,
            }
        else:
            entry = grouped[asset.id]
            if cand.rule not in entry["rules"]:
                entry["rules"].append(cand.rule)
            if cand.reason not in entry["reasons"]:
                entry["reasons"].append(cand.reason)
            if cand.score > entry["score"]:
                entry.update(score=cand.score, rule=cand.rule, reason=cand.reason, id=cand.id)
            if cand.group_id and not entry["group_id"]:
                entry["group_id"] = cand.group_id
            if cand.is_keeper:
                entry["is_keeper"] = True
    return [CandidateItemResponse(**v) for v in grouped.values()]


@router.post("/batch-status")
def batch_update_status(payload: BatchStatusUpdate):
    """
    變更候選狀態（只改資料庫）。以「資產」為單位：同一資產的所有候選記錄一起更新。
    加入待刪時檢查資產所屬的每個群組（鐵則 3），不論前端傳的是哪一條候選記錄的 ID。
    """
    if payload.state not in ("queued", "kept", "candidate"):
        raise HTTPException(status_code=400, detail="不合法的狀態值")
    with SyncSessionLocal() as session:
        asset_ids = set(payload.asset_ids)
        if payload.candidate_ids:
            asset_ids |= {
                aid for (aid,) in session.query(Candidate.asset_id)
                .filter(Candidate.id.in_(payload.candidate_ids)).distinct()
            }
        # 只允許還存在的資產
        asset_ids = {
            aid for (aid,) in session.query(Asset.id)
            .filter(Asset.id.in_(asset_ids), Asset.status.notin_(["deleted", "missing"]))
        }
        blocked: set = set()
        if payload.state == "queued":
            asset_ids, blocked = rules_engine.filter_queue_requests(session, asset_ids)
        if asset_ids:
            session.query(Candidate).filter(Candidate.asset_id.in_(asset_ids)).update(
                {"state": payload.state}, synchronize_session=False)
            session.query(Asset).filter(Asset.id.in_(asset_ids)).update(
                {"status": payload.state}, synchronize_session=False)
            session.commit()
    return {
        "status": "ok",
        "updated_count": len(asset_ids),
        "target_state": payload.state,
        "protected_count": len(blocked),
        "protected_asset_ids": sorted(blocked),
    }


@router.post("/evaluate")
def evaluate_rules():
    """重新執行偵測規則引擎（同步；若有其他任務進行中回傳 409）。"""
    from app.api.jobs import run_evaluate_now
    counts = run_evaluate_now()
    return {"status": "ok", "message": "已重新評估所有規則並產出候選項目", "counts": counts}
