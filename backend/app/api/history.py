from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

from app.core.database import get_db
from app.models.schema import ActionLog, Asset

router = APIRouter(prefix="/api/history", tags=["history"])

class BatchSummaryItem(BaseModel):
    batch_id: str
    action: str
    mode: str
    total_files: int
    at: datetime

class ActionLogItem(BaseModel):
    id: int
    batch_id: str
    asset_id: Optional[int]
    action: str
    mode: str
    original_path: str
    at: datetime

@router.get("/batches", response_model=List[BatchSummaryItem])
async def list_history_batches(db: AsyncSession = Depends(get_db)):
    # Group by batch_id
    query = (
        select(
            ActionLog.batch_id,
            ActionLog.action,
            ActionLog.mode,
            func.count(ActionLog.id).label("total_files"),
            func.max(ActionLog.at).label("at")
        )
        .group_by(ActionLog.batch_id, ActionLog.action, ActionLog.mode)
        .order_by(desc("at"))
    )
    result = await db.execute(query)
    rows = result.all()

    batches = []
    for r in rows:
        batches.append(BatchSummaryItem(
            batch_id=r.batch_id,
            action=r.action,
            mode=r.mode,
            total_files=r.total_files,
            at=r.at
        ))
    return batches

@router.get("/batches/{batch_id}", response_model=List[ActionLogItem])
async def get_batch_details(batch_id: str, db: AsyncSession = Depends(get_db)):
    query = select(ActionLog).where(ActionLog.batch_id == batch_id).order_by(ActionLog.id.asc())
    result = await db.execute(query)
    logs = result.scalars().all()
    return logs
