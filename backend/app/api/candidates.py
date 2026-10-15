import os
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db, SyncSessionLocal
from app.models.schema import Candidate, Asset
from app.services import review_service
from app.services.review_logic import WAIVABLE_RULES, rules_for_category
from app.services.rules_engine import rules_engine
from app.services.thumbnail import thumb_key_args

router = APIRouter(prefix="/api/candidates", tags=["candidates"])

State = Literal["candidate", "queued", "kept"]
Sort = Literal["score", "size", "path", "group"]
Keepers = Literal["show", "hide", "only"]


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
    # v0.6.3：'screenshot' / 'document' → 具有該規則候選記錄的資產不受「群組至少留一張」保護
    waive_protection_for: Optional[str] = None


# ─────────────────────────────────────────────
# 舊版：一次回傳全部候選（保留給舊程式與測試；新的審核頁改用 /page）
# ─────────────────────────────────────────────
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


# ─────────────────────────────────────────────
# v0.6.3：統計與分頁
# ─────────────────────────────────────────────
@router.get("/summary")
def get_summary(state: State = "candidate"):
    """各審核狀態與各分類的數量 / 容量（不重複資產，不含 Keeper）。"""
    with SyncSessionLocal() as session:
        return review_service.summary(session, state)


@router.get("/page")
def get_page(
    category: str = "all",
    state: State = "candidate",
    sort: Sort = "score",
    min_score: Optional[float] = Query(None, ge=0.0, le=1.0),
    keepers: Keepers = "show",
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
):
    """分頁取得候選（以資產為單位）。排序固定穩定（同分以資產 id 排序），可安全用 offset 續讀。"""
    try:
        rules_for_category(category)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    with SyncSessionLocal() as session:
        return review_service.fetch_page(session, state=state, category=category, sort=sort,
                                         min_score=min_score, keepers=keepers, offset=offset, limit=limit)


# ─────────────────────────────────────────────
# 變更狀態（以資產 id）
# ─────────────────────────────────────────────
@router.post("/batch-status")
def batch_update_status(payload: BatchStatusUpdate):
    """
    變更候選狀態（只改資料庫）。以「資產」為單位：同一資產的所有候選記錄一起更新。
    加入待刪時檢查資產所屬的每個群組（鐵則 3），不論前端傳的是哪一條候選記錄的 ID。
    v0.6.3：
      - waive_protection_for='screenshot'|'document'：具有該規則候選記錄的資產不受群組保護（使用者決定）。
      - 回傳 undo_token（記憶體內，重新啟動後失效）與 updated_asset_ids。
    """
    if payload.state not in ("queued", "kept", "candidate"):
        raise HTTPException(status_code=400, detail="不合法的狀態值")
    if payload.waive_protection_for is not None and payload.waive_protection_for not in WAIVABLE_RULES:
        raise HTTPException(status_code=400, detail=f"waive_protection_for 只能是 {list(WAIVABLE_RULES)}")
    with SyncSessionLocal() as session:
        asset_ids = set(payload.asset_ids)
        if payload.candidate_ids:
            asset_ids |= {
                aid for (aid,) in session.query(Candidate.asset_id)
                .filter(Candidate.id.in_(payload.candidate_ids)).distinct()
            }
        result = review_service.apply_state_change(
            session, asset_ids, payload.state,
            payload.waive_protection_for if payload.state == "queued" else None)
        session.commit()
        token = review_service.record_undo(payload.state, result)
    return {
        "status": "ok",
        "updated_count": len(result.updated),
        "updated_asset_ids": result.updated if len(result.updated) <= 20000 else [],
        "target_state": payload.state,
        "protected_count": len(result.blocked),
        "protected_asset_ids": sorted(result.blocked),
        "undo_token": token,
    }


# ─────────────────────────────────────────────
# v0.6.3：依條件批次變更（預覽 → 確認）
# ─────────────────────────────────────────────
class BulkFilter(BaseModel):
    category: str = "all"
    state: State = "candidate"
    min_score: Optional[float] = Field(None, ge=0.0, le=1.0)


class BulkRequest(BaseModel):
    filter: BulkFilter
    target_state: State
    exclude_asset_ids: List[int] = []
    dry_run: bool = True
    expected_count: Optional[int] = None   # 實際執行時必填：預覽看到的張數（排除後），不符就拒絕


@router.post("/bulk-status")
def bulk_status(payload: BulkRequest):
    """
    依條件批次變更狀態。永遠只動「非 Keeper」的相片。
    - dry_run=true：回傳符合條件的張數、容量與隨機抽樣（供使用者確認）。
    - dry_run=false：必須帶 expected_count；與目前實際張數不符（清單已變更）→ 409，不寫入任何東西。
    category 為 screenshot / document 且 target_state 為 queued 時，自動套用保護豁免。
    """
    f = payload.filter
    try:
        rules_for_category(f.category)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if payload.target_state == f.state:
        raise HTTPException(status_code=400, detail="目標狀態與目前狀態相同")
    waive = f.category if (payload.target_state == "queued" and f.category in WAIVABLE_RULES) else None

    with SyncSessionLocal() as session:
        matching = review_service.matching_assets(session, state=f.state, category=f.category, min_score=f.min_score)
        excluded = set(payload.exclude_asset_ids)
        final = {aid: size for aid, size in matching.items() if aid not in excluded}
        size_bytes = sum(final.values())

        if payload.dry_run:
            sample = review_service.sample_items(session, final.keys(), state=f.state, category=f.category,
                                                 min_score=f.min_score)
            return {
                "status": "preview", "count": len(final), "size_bytes": size_bytes, "waived": waive is not None,
                "target_state": payload.target_state,
                "sample": [{"asset_id": i["asset_id"], "filename": i["filename"],
                            "thumbnail_url": i["thumbnail_url"], "score": i["score"]} for i in sample],
            }

        if payload.expected_count is None:
            raise HTTPException(status_code=400, detail="實際執行必須帶 expected_count（先預覽）")
        if payload.expected_count != len(final):
            raise HTTPException(
                status_code=409,
                detail=f"清單已變更（預期 {payload.expected_count} 張，目前 {len(final)} 張），請重新預覽")

        result = review_service.apply_state_change(session, final.keys(), payload.target_state, waive)
        session.commit()
        token = review_service.record_undo(payload.target_state, result)
        updated_size = sum(final.get(aid, 0) for aid in result.updated)
    return {
        "status": "ok",
        "updated_count": len(result.updated),
        "target_state": payload.target_state,
        "protected_count": len(result.blocked),
        "protected_asset_ids": sorted(result.blocked)[:50],
        "size_bytes": updated_size,
        "undo_token": token,
    }


class UndoRequest(BaseModel):
    token: str


@router.post("/undo")
def undo_change(payload: UndoRequest):
    """復原最近一次狀態變更（只還原仍處於變更後狀態的相片）。"""
    with SyncSessionLocal() as session:
        restored = review_service.undo(session, payload.token)
        if restored is None:
            raise HTTPException(status_code=404, detail="找不到可復原的紀錄（可能已復原過，或後端已重新啟動）")
        session.commit()
    return {"status": "ok", "restored_count": restored}


@router.post("/evaluate")
def evaluate_rules():
    """重新執行偵測規則引擎（同步；若有其他任務進行中回傳 409）。"""
    from app.api.jobs import run_evaluate_now
    counts = run_evaluate_now()
    return {"status": "ok", "message": "已重新評估所有規則並產出候選項目", "counts": counts}
