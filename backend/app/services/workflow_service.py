"""
workflow_service.py（v0.6）— 首頁與資料庫頁共用的「流程狀態」與「主要分類」統計。

主要分類（primary category）：每張待審核相片只歸入一個類別，所以各類別加總 = 待審核總數。
優先順序（先命中者為準）：完全重複 → 近似重複 → 連拍 → 截圖 → 文件 → 梗圖 → 誤拍 → 模糊 → 曝光 → 影片
"""
from typing import Any, Dict, List

from sqlalchemy import func

from app.core.progress import progress_tracker
from app.models.schema import ActionLog, AnalysisFailure, Asset, Candidate, Root
from app.services import evaluation_state as es
from app.services.stats_service import stats_service

PRIMARY_ORDER: List[tuple] = [
    ("exact_dup", "完全重複", ("exact_dup",)),
    ("near_dup", "近似重複", ("near_dup",)),
    ("burst", "連拍多餘", ("burst",)),
    ("screenshot", "螢幕截圖", ("screenshot",)),
    ("document", "文件 / 收據", ("document",)),
    ("meme", "梗圖 / 迷因", ("meme",)),
    ("mistouch", "誤拍 / 廢片", ("mistouch",)),
    ("blur", "模糊", ("blur",)),
    ("exposure", "曝光不良", ("over_exposure", "under_exposure")),
    ("video", "影片", ("video_same_size", "video_tiny")),
]
_RULE_TO_PRIMARY = {r: (i, key) for i, (key, _, rules) in enumerate(PRIMARY_ORDER) for r in rules}

_ACTIVE = Asset.status.notin_(["deleted", "missing"])


def _state_breakdown(session) -> Dict[str, Dict[str, int]]:
    """候選相片依審核狀態統計（非 Keeper、不重複資產）。"""
    rows = (session.query(Candidate.asset_id, Candidate.state, Asset.size_total)
            .join(Asset, Candidate.asset_id == Asset.id)
            .filter(Candidate.is_keeper == False, _ACTIVE).distinct().all())  # noqa: E712
    out = {s: {"count": 0, "size_bytes": 0} for s in ("candidate", "queued", "kept")}
    seen = set()
    for aid, state, size in rows:
        if aid in seen or state not in out:
            continue
        seen.add(aid)
        out[state]["count"] += 1
        out[state]["size_bytes"] += size or 0
    return out


def primary_breakdown(session, state: str = "candidate") -> List[Dict[str, Any]]:
    rows = (session.query(Candidate.asset_id, Candidate.rule, Asset.size_total)
            .join(Asset, Candidate.asset_id == Asset.id)
            .filter(Candidate.is_keeper == False, Candidate.state == state, _ACTIVE).all())  # noqa: E712
    best: Dict[int, tuple] = {}
    for aid, rule, size in rows:
        rank = _RULE_TO_PRIMARY.get(rule)
        if rank is None:
            continue
        if aid not in best or rank[0] < best[aid][0]:
            best[aid] = (rank[0], rank[1], size or 0)
    totals = {key: {"key": key, "label": label, "count": 0, "size_bytes": 0} for key, label, _ in PRIMARY_ORDER}
    for _, key, size in best.values():
        totals[key]["count"] += 1
        totals[key]["size_bytes"] += size
    return [totals[key] for key, _, _ in PRIMARY_ORDER]


def workflow_status(session) -> Dict[str, Any]:
    dash = stats_service.get_dashboard_stats(session)
    stages = stats_service.get_analysis_stage_stats(session)
    roots = session.query(Root).all()
    missing = session.query(func.count(Asset.id)).filter(Asset.status == "missing").scalar() or 0
    deleted = session.query(func.count(Asset.id)).filter(Asset.status == "deleted").scalar() or 0
    failures = (session.query(func.count(AnalysisFailure.id)).join(Asset, AnalysisFailure.asset_id == Asset.id)
                .filter(_ACTIVE).scalar() or 0)
    last_scan = max((r.last_scan_at for r in roots if r.last_scan_at), default=None)
    analysis_pending = sum(stages[k]["pending"] + stages[k].get("stale", 0) for k in stages)
    analysed_total = stages["basic"]["done"] + stages["video"]["done"]
    ever = es.last_evaluated_at(session)
    stale = es.is_stale(session) if (ever or analysed_total) else False
    review = _state_breakdown(session)
    breakdown = primary_breakdown(session)
    return {
        "library": {
            "roots": len(roots),
            "roots_offline": sum(1 for r in roots if not r.available),
            "roots_never_scanned": sum(1 for r in roots if not r.last_scan_at),
            "last_scan_at": last_scan.isoformat() if last_scan else None,
            "assets": dash["assets"],
            "missing": missing,
        },
        "analysis": {"stages": stages, "pending_total": analysis_pending, "failures": failures},
        "evaluation": {
            "last_evaluated_at": ever,
            "is_stale": bool(stale),
            "analysed_since": es.analysed_since(session) if (ever or analysed_total) else 0,
            "last_result": es.last_result(session),
        },
        "review": review,
        "breakdown": breakdown,
        "breakdown_total": sum(b["count"] for b in breakdown),
        "deleted": {"count": deleted, "size_bytes": dash["history"]["deleted_size_bytes"],
                    "batches": dash["history"]["deleted_batches"]},
        "job": progress_tracker.get_state(),
    }
