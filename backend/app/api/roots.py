from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, ConfigDict
from typing import List, Optional
from datetime import datetime
from pathlib import Path

from app.core.database import get_db
from app.models.schema import Root

router = APIRouter(prefix="/api/roots", tags=["roots"])

class RootCreate(BaseModel):
    path: str
    recursive: bool = True
    exclude_patterns: str = ""
    priority: int = 0

class RootResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    path: str
    recursive: bool
    exclude_patterns: str
    priority: int
    last_scan_at: Optional[datetime] = None
    available: bool

@router.get("", response_model=List[RootResponse])
async def list_roots(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Root).order_by(Root.priority.asc(), Root.id.asc()))
    return result.scalars().all()

@router.post("", response_model=RootResponse)
async def create_root(payload: RootCreate, db: AsyncSession = Depends(get_db)):
    # Validate path exists
    target_path = Path(payload.path).resolve()
    if not target_path.exists() or not target_path.is_dir():
        raise HTTPException(status_code=400, detail=f"目錄路徑不存在或非資料夾：{payload.path}")

    # Check duplicate
    existing = await db.execute(select(Root).where(Root.path == str(target_path)))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="此路徑已加入過掃描清單")

    root = Root(
        path=str(target_path),
        recursive=payload.recursive,
        exclude_patterns=payload.exclude_patterns,
        priority=payload.priority,
        available=True
    )
    db.add(root)
    await db.commit()
    await db.refresh(root)
    return root

@router.delete("/{root_id}")
async def delete_root(root_id: int, db: AsyncSession = Depends(get_db)):
    root = await db.get(Root, root_id)
    if not root:
        raise HTTPException(status_code=404, detail="找不到指定的路徑")
    await db.delete(root)
    await db.commit()
    return {"status": "ok", "message": "已移除路徑"}
