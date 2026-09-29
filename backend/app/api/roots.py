from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
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
    raw_path = payload.path.strip()
    if not raw_path:
        raise HTTPException(status_code=400, detail="請輸入有效的目錄路徑")

    # Validate path exists and is a directory
    try:
        target_path = Path(raw_path).resolve()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"路徑格式不合法：{str(e)}")

    if not target_path.exists() or not target_path.is_dir():
        raise HTTPException(status_code=400, detail=f"目錄路徑不存在或非資料夾：{payload.path}")

    target_str = str(target_path)

    # 取得現有所有掃描路徑進行比對
    all_roots_res = await db.execute(select(Root))
    all_roots = all_roots_res.scalars().all()

    for r in all_roots:
        existing_path = Path(r.path).resolve()

        # 1. 檢查完全重複 (不分大小寫與格式化)
        if existing_path == target_path or str(existing_path).lower() == target_str.lower():
            raise HTTPException(status_code=400, detail=f"此目錄「{r.path}」已在掃描清單中，無法重複加入。")

        # 2. 檢查是否已被現有的上層遞迴目錄涵蓋
        if r.recursive:
            try:
                target_path.relative_to(existing_path)
                raise HTTPException(
                    status_code=400,
                    detail=f"此目錄已被已登錄的上層目錄「{r.path}」（已開啟遞迴掃描）涵蓋，無需重複加入。"
                )
            except ValueError:
                pass

        # 3. 檢查新目錄是否涵蓋了現有的子目錄
        if payload.recursive:
            try:
                existing_path.relative_to(target_path)
                raise HTTPException(
                    status_code=400,
                    detail=f"欲新增的目錄涵蓋了已登錄的子目錄「{r.path}」。請先移除該子目錄後再加入上層目錄，以避免重複掃描。"
                )
            except ValueError:
                pass

    root = Root(
        path=target_str,
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
