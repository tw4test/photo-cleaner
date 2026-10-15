"""
評估狀態追蹤（v0.6）

- fingerprint()：把「會影響評估結果的輸入」濃縮成一個字串：
  相片庫資產狀態、各分析階段完成數與最新時間、目前設定。
  與上次評估時的指紋相同 → 評估結果不會改變，可以略過。
- analysed_since()：上次評估之後才完成 basic / CLIP 分析的資產數（給 UI 顯示「有 N 張尚未評估」）。
- candidate_set()：非 Keeper 候選資產集合，用來計算每次評估「新增 / 移除」幾張。
"""
import hashlib
import json
from datetime import datetime
from typing import Any, Dict, Optional, Set

from sqlalchemy import func, or_

from app.core.app_state import get_state, set_state
from app.core.defaults import load_effective_settings
from app.models.schema import Asset, Candidate, ClipFeature, Feature, File, VideoInfo

KEY_FINGERPRINT = "evaluate.fingerprint"
KEY_AT = "evaluate.at"
KEY_RESULT = "evaluate.result"

# 不影響評估結果的設定（runner 數、裝置等），不納入指紋
_IGNORED_SETTINGS = {"hash_runners", "basic_runners", "clip_runners", "video_runners", "compute_device",
                     "clip_batch_size", "auto_evaluate_on_save", "deletion_mode"}


def _iso(v) -> str:
    return v.isoformat() if isinstance(v, datetime) else str(v or "")


def fingerprint(session) -> str:
    # 只看「存在 / 已刪除 / 遺失」；審核動作（待刪、保留）不影響評估結果，不應讓評估變成過期
    def n(*cond):
        return session.query(func.count(Asset.id)).filter(*cond).scalar()
    parts = {
        "assets": [n(Asset.status.notin_(["deleted", "missing"])), n(Asset.status == "deleted"),
                   n(Asset.status == "missing")],
        "max_asset": session.query(func.max(Asset.id)).scalar(),
        "sha": session.query(func.count(File.id)).filter(File.sha256.isnot(None)).scalar(),
        "feat": [session.query(func.count(Feature.asset_id)).scalar(), _iso(session.query(func.max(Feature.analyzed_at)).scalar())],
        "clip": [session.query(func.count(ClipFeature.asset_id)).scalar(), _iso(session.query(func.max(ClipFeature.analyzed_at)).scalar())],
        "video": session.query(func.count(VideoInfo.asset_id)).scalar(),
        "settings": {k: v for k, v in sorted(load_effective_settings(session).items()) if k not in _IGNORED_SETTINGS},
    }
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def candidate_set(session) -> Set[int]:
    return {a for (a,) in session.query(Candidate.asset_id).filter(Candidate.is_keeper == False).distinct()}  # noqa: E712


def last_evaluated_at(session) -> Optional[str]:
    return get_state(session, KEY_AT)


def is_stale(session) -> bool:
    return get_state(session, KEY_FINGERPRINT) != fingerprint(session)


def analysed_since(session) -> int:
    """上次評估之後才完成 basic 或 CLIP 分析的資產數；從未評估過時 = 全部已分析資產。"""
    at = get_state(session, KEY_AT)
    active = Asset.status.notin_(["deleted", "missing"])
    if not at:
        q = (session.query(func.count(func.distinct(Asset.id))).outerjoin(Feature, Feature.asset_id == Asset.id)
             .outerjoin(ClipFeature, ClipFeature.asset_id == Asset.id)
             .filter(active, or_(Feature.analyzed_at.isnot(None), ClipFeature.analyzed_at.isnot(None))))
        return q.scalar() or 0
    ts = datetime.fromisoformat(at)
    q = (session.query(func.count(func.distinct(Asset.id))).outerjoin(Feature, Feature.asset_id == Asset.id)
         .outerjoin(ClipFeature, ClipFeature.asset_id == Asset.id)
         .filter(active, or_(Feature.analyzed_at > ts, ClipFeature.analyzed_at > ts)))
    return q.scalar() or 0


def record(session, before: Set[int], counts: Dict[str, int], skipped: bool = False) -> Dict[str, Any]:
    after = candidate_set(session)
    result = {
        "skipped": skipped,
        "added": 0 if skipped else len(after - before),
        "removed": 0 if skipped else len(before - after),
        "total": len(after),
        "counts": counts,
        "at": datetime.utcnow().isoformat(timespec="seconds"),
    }
    set_state(session, KEY_FINGERPRINT, fingerprint(session))
    if not skipped:
        set_state(session, KEY_AT, datetime.utcnow().isoformat())
    set_state(session, KEY_RESULT, result)
    return result


def last_result(session) -> Optional[Dict[str, Any]]:
    return get_state(session, KEY_RESULT)
