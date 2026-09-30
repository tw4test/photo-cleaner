import io
from fastapi import APIRouter, HTTPException, Depends, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from typing import Optional
from pathlib import Path

from app.core.database import get_db, SyncSessionLocal
from app.models.schema import Asset, File
from app.services.thumbnail import thumbnail_service

router = APIRouter(prefix="/api/thumbnails", tags=["thumbnails"])

FALLBACK_ERROR_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 240" width="320" height="240">
  <rect width="320" height="240" fill="#0f172a"/>
  <rect x="10" y="10" width="300" height="220" rx="8" fill="#1e293b" stroke="#334155" stroke-dasharray="4 4"/>
  <circle cx="160" cy="105" r="24" fill="#334155"/>
  <path d="M152 105 L168 105 M160 97 L160 113" stroke="#94a3b8" stroke-width="2" stroke-linecap="round"/>
  <text x="160" y="150" fill="#94a3b8" font-family="system-ui, sans-serif" font-size="12" font-weight="500" text-anchor="middle">無法載入預覽圖</text>
  <text x="160" y="170" fill="#64748b" font-family="system-ui, sans-serif" font-size="10" text-anchor="middle">檔案可能已移動或格式不支援</text>
</svg>"""


@router.get("/{asset_id}")
async def get_asset_thumbnail(
    asset_id: int,
    v: Optional[str] = Query(None, description="版本號或檔案時間戳記以支援 Immutable 快取"),
    db: AsyncSession = Depends(get_db)
):
    """
    獲取指定資產之縮圖：
    1. 支援 URL 版本戳記 (v=mtime) 進行 HTTP 31536000s Immutable 永久快取。
    2. RAW+JPG 配對時優先提取伴生 JPG，影片即時擷取第 1 幀。
    3. 若讀取失敗，回傳動態 SVG 佔位圖且設置 no-cache，不汙染磁碟快取並保留重試機會。
    """
    stmt = (
        select(Asset)
        .options(selectinload(Asset.files))
        .where(Asset.id == asset_id)
    )
    result = await db.execute(stmt)
    asset = result.scalar_one_or_none()

    if not asset:
        raise HTTPException(status_code=404, detail="找不到指定的資產")

    primary_file = None
    if asset.files:
        for f in asset.files:
            if f.role == "primary":
                primary_file = f
                break
        if not primary_file and asset.files:
            primary_file = asset.files[0]

    mtime = primary_file.mtime if primary_file else None
    size = primary_file.size if primary_file else None

    # 取得伴生圖路徑 (如 RAW + JPG 中的 JPG 檔案)
    companion_path = None
    if asset.paired_paths and isinstance(asset.paired_paths, list) and len(asset.paired_paths) > 0:
        companion_path = asset.paired_paths[0]

    thumb_path = thumbnail_service.generate_thumbnail(
        file_path=asset.primary_path,
        companion_path=companion_path,
        mtime=mtime,
        size=size
    )

    if not thumb_path or not thumb_path.exists():
        # 降級動態回傳 SVG 佔位圖，不快取
        return Response(
            content=FALLBACK_ERROR_SVG,
            media_type="image/svg+xml",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0"
            }
        )

    # 成功生成或命中快取：若有版本參數，發送 HTTP Immutable 強快取
    headers = {}
    if v:
        headers["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        headers["Cache-Control"] = "public, max-age=86400"

    return FileResponse(
        str(thumb_path),
        media_type="image/jpeg",
        headers=headers
    )


@router.post("/cleanup")
async def cleanup_orphan_thumbnails(db: AsyncSession = Depends(get_db)):
    """
    掃描磁碟縮圖快取目錄，清理所有已不存在於資料庫中的孤兒縮圖檔案，回收空間並確保完全一致。
    """
    # 查詢所有非 deleted 狀態的有效資產
    stmt = select(Asset).options(selectinload(Asset.files)).where(Asset.status != "deleted")
    result = await db.execute(stmt)
    active_assets = result.scalars().all()

    valid_keys = set()
    for asset in active_assets:
        primary_file = None
        for f in asset.files:
            if f.role == "primary":
                primary_file = f
                break
        mtime = primary_file.mtime if primary_file else None
        size = primary_file.size if primary_file else None
        companion_path = asset.paired_paths[0] if (asset.paired_paths and len(asset.paired_paths) > 0) else None

        cache_key = thumbnail_service.get_cache_key(
            file_path=asset.primary_path,
            mtime=mtime,
            size=size,
            companion_path=companion_path
        )
        valid_keys.add(cache_key)

    cleanup_stats = thumbnail_service.cleanup_orphan_thumbnails(valid_keys)

    return {
        "status": "ok",
        "active_assets_count": len(active_assets),
        "deleted_orphan_thumbnails": cleanup_stats["deleted_count"],
        "reclaimed_bytes": cleanup_stats["reclaimed_bytes"],
        "reclaimed_mb": round(cleanup_stats["reclaimed_bytes"] / (1024 * 1024), 2)
    }
