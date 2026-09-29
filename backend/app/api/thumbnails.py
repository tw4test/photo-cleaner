from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from pathlib import Path

from app.core.database import get_db
from app.models.schema import Asset
from app.services.thumbnail import thumbnail_service

router = APIRouter(prefix="/api/thumbnails", tags=["thumbnails"])

@router.get("/{asset_id}")
async def get_asset_thumbnail(asset_id: int, db: AsyncSession = Depends(get_db)):
    asset = await db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="找不到指定的資產")

    thumb_path = thumbnail_service.generate_thumbnail(asset.primary_path)
    if not thumb_path.exists():
        raise HTTPException(status_code=404, detail="無法生成縮圖")

    return FileResponse(str(thumb_path), media_type="image/jpeg")
