from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import selectinload

from app.core.database import SyncSessionLocal
from app.models.schema import Asset
from app.services.thumbnail import thumbnail_service, thumb_key_args

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
def get_asset_thumbnail(asset_id: int, v: Optional[str] = Query(None)):
    """
    取得資產縮圖（def：縮圖產生在 threadpool 執行，不阻塞 event loop / SSE）。
    帶 v=mtime 時回傳 immutable 長快取；失敗回傳 SVG 佔位圖且不快取。
    """
    with SyncSessionLocal() as session:
        asset = (session.query(Asset).options(selectinload(Asset.files))
                 .filter(Asset.id == asset_id).first())
        if not asset:
            raise HTTPException(status_code=404, detail="找不到指定的資產")
        args = thumb_key_args(asset)
    thumb_path = thumbnail_service.generate_thumbnail(**args)
    if not thumb_path or not thumb_path.exists():
        return Response(content=FALLBACK_ERROR_SVG, media_type="image/svg+xml",
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate",
                                 "Pragma": "no-cache", "Expires": "0"})
    cache = "public, max-age=31536000, immutable" if v else "public, max-age=86400"
    return FileResponse(str(thumb_path), media_type="image/jpeg", headers={"Cache-Control": cache})


@router.post("/cleanup")
def cleanup_orphan_thumbnails():
    """清理資料庫中已不存在的資產的縮圖。"""
    with SyncSessionLocal() as session:
        assets = (session.query(Asset).options(selectinload(Asset.files))
                  .filter(Asset.status != "deleted").all())
        valid_keys = {thumbnail_service.get_cache_key(**thumb_key_args(a)) for a in assets}
        n = len(assets)
    stats = thumbnail_service.cleanup_orphan_thumbnails(valid_keys)
    return {
        "status": "ok",
        "active_assets_count": n,
        "deleted_orphan_thumbnails": stats["deleted_count"],
        "reclaimed_bytes": stats["reclaimed_bytes"],
        "reclaimed_mb": round(stats["reclaimed_bytes"] / (1024 * 1024), 2),
    }
