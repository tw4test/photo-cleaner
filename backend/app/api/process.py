from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import os

from app.core.database import get_db, SyncSessionLocal
from app.models.schema import Asset, Candidate, File
from app.models.enums import AssetStatus, DeletionMode
from app.services.deletion import deletion_service

router = APIRouter(prefix="/api/process", tags=["process"])

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

from sqlalchemy.orm import selectinload

@router.get("/summary", response_model=QueuedSummaryResponse)
async def get_queued_summary(db: AsyncSession = Depends(get_db)):
    # 查詢所有處於 queued 狀態的資產
    query = (
        select(Asset, Candidate)
        .options(selectinload(Asset.files))
        .outerjoin(Candidate, Candidate.asset_id == Asset.id)
        .where(Asset.status == AssetStatus.QUEUED.value)
    )
    result = await db.execute(query)
    rows = result.all()

    total_size = 0
    items = []
    seen_asset_ids = set()

    for asset, cand in rows:
        if asset.id in seen_asset_ids:
            continue
        seen_asset_ids.add(asset.id)
        total_size += asset.size_total

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

        items.append(QueuedItemResponse(
            asset_id=asset.id,
            filename=filename,
            primary_path=asset.primary_path,
            media_type=asset.media_type,
            size_total=asset.size_total,
            paired_paths=asset.paired_paths or [],
            rule=cand.rule if cand else None,
            reason=cand.reason if cand else None,
            thumbnail_url=thumb_url
        ))

    return QueuedSummaryResponse(
        total_count=len(items),
        total_size_bytes=total_size,
        items=items
    )

@router.post("/execute")
async def execute_deletion(payload: ExecuteDeletionRequest):
    # 二次確認校驗
    if not payload.confirmed:
        raise HTTPException(status_code=400, detail="必須確認勾選同意執行刪除")

    with SyncSessionLocal() as session:
        result = deletion_service.execute_deletion(
            session=session,
            asset_ids=payload.asset_ids,
            mode=payload.mode
        )
    return result

@router.post("/unqueue")
async def unqueue_items(asset_ids: List[int], target_state: str = "candidate"):
    if target_state not in ["candidate", "kept"]:
        raise HTTPException(status_code=400, detail="不合法的目標狀態")

    with SyncSessionLocal() as session:
        session.query(Asset).filter(Asset.id.in_(asset_ids)).update(
            {"status": target_state}, synchronize_session=False
        )
        session.query(Candidate).filter(Candidate.asset_id.in_(asset_ids)).update(
            {"state": target_state}, synchronize_session=False
        )
        session.commit()

    return {"status": "ok", "message": f"已將 {len(asset_ids)} 項移出待刪清單"}
