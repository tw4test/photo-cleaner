"""
preview.py（v0.6.5）— 審核頁的大圖預覽與重複群組成員。
掛在 candidates 的 router 底下（見 api/candidates.py 結尾）：
  GET /api/candidates/preview/{asset_id}   大圖（原檔或縮成最長邊 2560px 的 JPEG）
  GET /api/candidates/group/{group_id}     群組成員（含各狀態，Keeper 排最前面）
"""
from collections import defaultdict
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import selectinload

from app.core.database import SyncSessionLocal
from app.models.schema import Asset, Candidate, Feature, Group
from app.services import preview_service
from app.services.thumbnail import thumb_key_args

router = APIRouter()

MAX_GROUP_MEMBERS = 200


@router.get("/preview/{asset_id}")
def get_preview(asset_id: int, v: Optional[str] = Query(None)):
    """帶 v=mtime 時回傳長快取（檔案變動時 thumbnail_url 的 v 會跟著變）。"""
    with SyncSessionLocal() as session:
        asset = (session.query(Asset).options(selectinload(Asset.files))
                 .filter(Asset.id == asset_id).first())
        if asset is None or asset.status == "deleted":
            raise HTTPException(status_code=404, detail="找不到指定的資產")
        primary = asset.primary_path
        companion = thumb_key_args(asset)["companion_path"]
        media_type = asset.media_type
    try:
        pv = preview_service.render(primary, companion, media_type)
    except preview_service.PreviewNotFound:
        raise HTTPException(status_code=404, detail="檔案不存在（可能已移動、刪除或磁碟離線）")
    except preview_service.PreviewDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"無法解碼這個檔案：{exc}")
    cache = "private, max-age=31536000, immutable" if v else "private, max-age=300"
    if pv.path:
        return FileResponse(pv.path, media_type=pv.media_type, headers={"Cache-Control": cache})
    return Response(content=pv.data, media_type=pv.media_type, headers={"Cache-Control": cache})


@router.get("/group/{group_id}")
def get_group(group_id: int) -> Dict[str, Any]:
    with SyncSessionLocal() as session:
        group = session.get(Group, group_id)
        rows = (session.query(Candidate).join(Asset, Asset.id == Candidate.asset_id)
                .filter(Candidate.group_id == group_id, Asset.status.notin_(["deleted", "missing"])).all())
        if group is None and not rows:
            raise HTTPException(status_code=404, detail="找不到指定的群組")
        by_asset: Dict[int, List[Candidate]] = defaultdict(list)
        for c in rows:
            by_asset[c.asset_id].append(c)
        ids = sorted(by_asset)
        members: List[Dict[str, Any]] = []
        if ids:
            assets = {a.id: a for a in session.query(Asset).options(selectinload(Asset.files)).filter(Asset.id.in_(ids))}
            feats = {f.asset_id: f for f in session.query(Feature).filter(Feature.asset_id.in_(ids))}
            for aid in ids:
                a, cs = assets.get(aid), by_asset[aid]
                if a is None:
                    continue
                f = feats.get(aid)
                mtime = thumb_key_args(a)["mtime"]
                best = max(cs, key=lambda c: (c.score, -c.id))
                rules: List[str] = []
                for c in sorted(cs, key=lambda c: (-c.score, c.id)):
                    if c.rule not in rules:
                        rules.append(c.rule)
                members.append({
                    "asset_id": a.id,
                    "filename": a.primary_path.replace("\\", "/").rsplit("/", 1)[-1],
                    "primary_path": a.primary_path,
                    "media_type": a.media_type,
                    "size_total": a.size_total,
                    "width": f.width if f and f.width else None,
                    "height": f.height if f and f.height else None,
                    "sharpness": round(f.blur_score, 1) if f and f.blur_score is not None else None,
                    "is_keeper": any(c.is_keeper for c in cs),
                    "state": best.state,
                    "score": best.score,
                    "rules": rules,
                    "thumbnail_url": f"/api/thumbnails/{a.id}" + (f"?v={int(mtime)}" if mtime else ""),
                })
        # Keeper 最前面；其餘依清晰度（高 → 低，沒有的排最後）、檔案大小
        members.sort(key=lambda m: (not m["is_keeper"], m["sharpness"] is None, -(m["sharpness"] or 0.0),
                                    -m["size_total"], m["asset_id"]))
        return {"group_id": group_id, "type": group.type if group else None,
                "members": members[:MAX_GROUP_MEMBERS]}
