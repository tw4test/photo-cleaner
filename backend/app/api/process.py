import os
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db, SyncSessionLocal
from app.core.progress import progress_tracker
from app.models.schema import Asset, Candidate
from app.models.enums import AssetStatus
from app.services.deletion import deletion_service
from app.services.thumbnail import thumb_key_args

router = APIRouter(prefix="/api/process", tags=["process"])

PERMANENT_CONFIRM_TEXT = "永久刪除"


class QueuedItemResponse(BaseModel):
    asset_id: int
    filename: str
    primary_path: str
    media_type: str
    size_total: int
    paired_paths: List[str]
    rule: Optional[str] = None
    reason: Optional[str] = None
    thumbnail_url: str


class QueuedSummaryResponse(BaseModel):
    total_count: int
    total_size_bytes: int
    items: List[QueuedItemResponse]


class ExecuteDeletionRequest(BaseModel):
    asset_ids: Optional[List[int]] = None
    mode: str = "trash"  # "trash" | "permanent"
    confirmed: bool = False
    confirm_text: Optional[str] = None  # 永久刪除時必須等於「永久刪除」


@router.get("/summary", response_model=QueuedSummaryResponse)
async def get_queued_summary(db: AsyncSession = Depends(get_db)):
    query = (
        select(Asset, Candidate)
        .options(selectinload(Asset.files))
        .outerjoin(Candidate, Candidate.asset_id == Asset.id)
        .where(Asset.status == AssetStatus.QUEUED.value)
        .order_by(Asset.id, Candidate.score.desc())
    )
    rows = (await db.execute(query)).all()
    total_size = 0
    items = []
    seen = set()
    for asset, cand in rows:
        if asset.id in seen:
            continue
        seen.add(asset.id)
        total_size += asset.size_total
        mtime = thumb_key_args(asset)["mtime"]
        items.append(QueuedItemResponse(
            asset_id=asset.id,
            filename=os.path.basename(asset.primary_path),
            primary_path=asset.primary_path,
            media_type=asset.media_type,
            size_total=asset.size_total,
            paired_paths=asset.paired_paths or [],
            rule=cand.rule if cand else None,
            reason=cand.reason if cand else None,
            thumbnail_url=f"/api/thumbnails/{asset.id}" + (f"?v={int(mtime)}" if mtime else ""),
        ))
    return QueuedSummaryResponse(total_count=len(items), total_size_bytes=total_size, items=items)


@router.post("/execute")
def execute_deletion(payload: ExecuteDeletionRequest):
    if not payload.confirmed:
        raise HTTPException(status_code=400, detail="必須確認勾選同意執行刪除")
    if payload.mode not in ("trash", "permanent"):
        raise HTTPException(status_code=400, detail=f"目前不支援的刪除模式：{payload.mode}")
    if payload.mode == "permanent" and (payload.confirm_text or "").strip() != PERMANENT_CONFIRM_TEXT:
        raise HTTPException(status_code=400, detail=f"永久刪除必須輸入確認文字「{PERMANENT_CONFIRM_TEXT}」")
    if progress_tracker.state.is_running:
        raise HTTPException(status_code=409, detail="分析或評估進行中，請完成後再執行刪除")
    with SyncSessionLocal() as session:
        return deletion_service.execute_deletion(session=session, asset_ids=payload.asset_ids, mode=payload.mode)


@router.post("/unqueue")
def unqueue_items(asset_ids: List[int] = Body(...), target_state: str = Query("candidate")):
    if target_state not in ("candidate", "kept"):
        raise HTTPException(status_code=400, detail="不合法的目標狀態")
    with SyncSessionLocal() as session:
        session.query(Asset).filter(Asset.id.in_(asset_ids), Asset.status == AssetStatus.QUEUED.value).update(
            {"status": target_state}, synchronize_session=False)
        session.query(Candidate).filter(Candidate.asset_id.in_(asset_ids)).update(
            {"state": target_state}, synchronize_session=False)
        session.commit()
    return {"status": "ok", "message": f"已將 {len(asset_ids)} 項移出待刪清單"}
