"""
review_service.py（v0.6.3）— 審核頁的資料存取：分頁、統計、批次狀態變更、復原。

設計重點
- 以「資產」為單位（同一張相片命中多個規則只算一張）。
- 分頁在資料庫端完成（GROUP BY asset + ORDER BY + LIMIT/OFFSET），一次只取一頁的詳細資料。
- 狀態變更以 CHUNK 分塊（避免 SQLite 變數上限），每塊先寫入再檢查下一塊，
  所以 Keeper 保護看得到前面已加入待刪的成員。
- 螢幕截圖 / 文件的保護豁免（WAIVABLE_RULES）：只有請求明確帶 waive_protection_for 才生效，
  且只對「真的有該規則候選記錄」的資產生效。豁免的資產先寫入，其餘再交給 rules_engine 檢查，
  所以非豁免成員仍然會被擋下「最後一張」。
"""
import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.models.schema import Asset, Candidate, Feature, VideoInfo
from app.services.review_logic import (
    CHUNK, WAIVABLE_RULES, chunked, rules_for_category, summarise, undo_store,
)
from app.services.rules_engine import rules_engine
from app.services.thumbnail import thumb_key_args

_ACTIVE = Asset.status.notin_(["deleted", "missing"])


# ─────────────────────────────────────────────
# 條件
# ─────────────────────────────────────────────
def build_conditions(state: str, rules: Optional[Sequence[str]], min_score: Optional[float], keepers: str) -> list:
    conds = [Candidate.state == state, _ACTIVE]
    if rules:
        conds.append(Candidate.rule.in_(list(rules)))
    if min_score is not None:
        conds.append(Candidate.score >= min_score)
    if keepers == "hide":
        conds.append(Candidate.is_keeper == False)  # noqa: E712
    elif keepers == "only":
        conds.append(Candidate.is_keeper == True)  # noqa: E712
    return conds


# ─────────────────────────────────────────────
# 統計
# ─────────────────────────────────────────────
def summary(session: Session, state: str) -> Dict[str, Any]:
    base = (session.query(Candidate.asset_id, Candidate.rule, Candidate.state, Asset.size_total)
            .join(Asset, Asset.id == Candidate.asset_id).filter(_ACTIVE))
    rows = base.filter(Candidate.is_keeper == False).distinct().all()  # noqa: E712
    keeper_rows = base.filter(Candidate.is_keeper == True, Candidate.state == state).distinct().all()  # noqa: E712
    return summarise(rows, keeper_rows, state)


# ─────────────────────────────────────────────
# 分頁
# ─────────────────────────────────────────────
def _row_matches(c: Candidate, rules: Optional[Sequence[str]], min_score: Optional[float], keepers: str) -> bool:
    if rules and c.rule not in rules:
        return False
    if min_score is not None and c.score < min_score:
        return False
    if keepers == "hide" and c.is_keeper:
        return False
    if keepers == "only" and not c.is_keeper:
        return False
    return True


def build_items(session: Session, ids: Sequence[int], state: str, rules: Optional[Sequence[str]],
                min_score: Optional[float], keepers: str) -> List[Dict[str, Any]]:
    """依 ids 的順序組出卡片資料（最多一頁的量）。"""
    if not ids:
        return []
    ids = list(ids)
    assets = {a.id: a for a in session.query(Asset).options(selectinload(Asset.files)).filter(Asset.id.in_(ids))}
    by_asset: Dict[int, List[Candidate]] = defaultdict(list)
    for c in session.query(Candidate).filter(Candidate.asset_id.in_(ids), Candidate.state == state):
        by_asset[c.asset_id].append(c)
    feats = {f.asset_id: f for f in session.query(Feature).filter(Feature.asset_id.in_(ids))}
    vids = {v.asset_id: v for v in session.query(VideoInfo).filter(VideoInfo.asset_id.in_(ids))}
    gids = {c.group_id for cs in by_asset.values() for c in cs if c.group_id}
    gsize: Dict[int, int] = {}
    if gids:
        gsize = {g: n for g, n in session.query(Candidate.group_id, func.count(func.distinct(Candidate.asset_id)))
                 .filter(Candidate.group_id.in_(list(gids))).group_by(Candidate.group_id)}

    out: List[Dict[str, Any]] = []
    for aid in ids:
        asset, cs = assets.get(aid), by_asset.get(aid)
        if asset is None or not cs:
            continue   # 剛好被其他操作改掉了
        shown = [c for c in cs if _row_matches(c, rules, min_score, keepers)] or cs
        primary = max(shown, key=lambda c: (c.score, -c.id))
        ordered = sorted(cs, key=lambda c: (-c.score, c.id))
        rules_all: List[str] = []
        reasons_all: List[str] = []
        for c in ordered:
            if c.rule not in rules_all:
                rules_all.append(c.rule)
            if c.reason not in reasons_all:
                reasons_all.append(c.reason)
        gid = primary.group_id or next((c.group_id for c in ordered if c.group_id), None)
        mtime = thumb_key_args(asset)["mtime"]
        feat, vid = feats.get(aid), vids.get(aid)
        width = (feat.width if feat and feat.width else None) or (vid.width if vid else None)
        height = (feat.height if feat and feat.height else None) or (vid.height if vid else None)
        out.append({
            "id": primary.id,
            "asset_id": asset.id,
            "filename": asset.primary_path.replace("\\", "/").rsplit("/", 1)[-1],
            "primary_path": asset.primary_path,
            "media_type": asset.media_type,
            "size_total": asset.size_total,
            "rule": primary.rule,
            "rules": rules_all,
            "reason": primary.reason,
            "reasons": reasons_all,
            "score": primary.score,
            "group_id": gid,
            "group_size": gsize.get(gid) if gid else None,
            "is_keeper": any(c.is_keeper for c in shown),
            "state": primary.state,
            "thumbnail_url": f"/api/thumbnails/{asset.id}" + (f"?v={int(mtime)}" if mtime else ""),
            "width": width,
            "height": height,
            "taken_at": feat.taken_at.isoformat() if feat and feat.taken_at else None,
        })
    return out


def fetch_page(session: Session, *, state: str, category: str, sort: str, min_score: Optional[float],
               keepers: str, offset: int, limit: int) -> Dict[str, Any]:
    rules = rules_for_category(category)
    conds = build_conditions(state, rules, min_score, keepers)

    total = (session.query(func.count(func.distinct(Candidate.asset_id)))
             .select_from(Candidate).join(Asset, Asset.id == Candidate.asset_id)
             .filter(*conds).scalar()) or 0

    best = func.max(Candidate.score)
    grp = func.max(Candidate.group_id)
    q = (session.query(Candidate.asset_id.label("aid"))
         .join(Asset, Asset.id == Candidate.asset_id).filter(*conds)
         .group_by(Candidate.asset_id, Asset.size_total, Asset.primary_path))
    if sort == "size":
        q = q.order_by(Asset.size_total.desc(), Candidate.asset_id)
    elif sort == "path":
        q = q.order_by(Asset.primary_path, Candidate.asset_id)
    elif sort == "group":
        q = q.order_by(grp.desc().nullslast(), best.desc(), Candidate.asset_id)
    else:  # score
        q = q.order_by(best.desc(), Candidate.asset_id)
    ids = [r.aid for r in q.offset(offset).limit(limit)]

    items = build_items(session, ids, state, rules, min_score, keepers)
    return {"items": items, "total": total, "offset": offset, "limit": limit,
            "has_more": offset + len(ids) < total}


# ─────────────────────────────────────────────
# 批次（依條件）
# ─────────────────────────────────────────────
def matching_assets(session: Session, *, state: str, category: str, min_score: Optional[float]) -> Dict[int, int]:
    """符合條件的「非 Keeper」資產：{asset_id: size_total}。批次動作永遠不碰 Keeper。"""
    rules = rules_for_category(category)
    conds = build_conditions(state, rules, min_score, "hide")
    rows = (session.query(Candidate.asset_id, Asset.size_total)
            .join(Asset, Asset.id == Candidate.asset_id).filter(*conds).distinct().all())
    return {aid: (size or 0) for aid, size in rows}


def sample_items(session: Session, ids: Iterable[int], *, state: str, category: str, min_score: Optional[float],
                 k: int = 12) -> List[Dict[str, Any]]:
    pool = sorted(ids)
    picked = random.sample(pool, min(k, len(pool))) if pool else []
    return build_items(session, picked, state, rules_for_category(category), min_score, "hide")


# ─────────────────────────────────────────────
# 狀態變更（含保護豁免）
# ─────────────────────────────────────────────
@dataclass
class ChangeResult:
    updated: List[int] = field(default_factory=list)
    blocked: Set[int] = field(default_factory=set)
    prev: Dict[str, List[int]] = field(default_factory=dict)   # 變更前狀態 → 資產 id（復原用）


def _existing_active(session: Session, ids: Iterable[int]) -> Set[int]:
    ids = list(ids)
    if not ids:
        return set()
    return {aid for (aid,) in session.query(Asset.id).filter(Asset.id.in_(ids), _ACTIVE)}


def _prev_states(session: Session, ids: Iterable[int]) -> Dict[int, str]:
    out: Dict[int, str] = {}
    for aid, st in session.query(Candidate.asset_id, Candidate.state).filter(Candidate.asset_id.in_(list(ids))).distinct():
        out.setdefault(aid, st)
    return out


def _write_state(session: Session, ids: Iterable[int], state: str) -> None:
    ids = list(ids)
    if not ids:
        return
    session.query(Candidate).filter(Candidate.asset_id.in_(ids)).update({"state": state}, synchronize_session=False)
    session.query(Asset).filter(Asset.id.in_(ids)).update({"status": state}, synchronize_session=False)


def apply_state_change(session: Session, asset_ids: Iterable[int], state: str,
                       waive_rule: Optional[str] = None) -> ChangeResult:
    """
    變更候選狀態（只改資料庫；不 commit）。

    state == 'queued' 時：
      1. waive_rule（'screenshot' / 'document'）有值 → 具有該規則候選記錄的資產不受 Keeper 保護，先寫入。
      2. 其餘資產交給 rules_engine.filter_queue_requests（此時豁免的資產已是 queued，
         所以非豁免成員仍然會被擋下「最後一張」）。
    """
    if waive_rule is not None and waive_rule not in WAIVABLE_RULES:
        raise ValueError(f"不能豁免的規則：{waive_rule!r}")
    result = ChangeResult()
    for chunk in chunked(sorted(set(asset_ids)), CHUNK):
        chunk_set = _existing_active(session, chunk)
        if not chunk_set:
            continue
        prev = _prev_states(session, chunk_set)
        done: Set[int] = set()
        if state == "queued":
            waived: Set[int] = set()
            if waive_rule:
                waived = {aid for (aid,) in session.query(Candidate.asset_id).filter(
                    Candidate.asset_id.in_(list(chunk_set)), Candidate.rule == waive_rule).distinct()}
            if waived:
                _write_state(session, waived, state)
            rest = chunk_set - waived
            allowed_rest: Set[int] = set()
            if rest:
                allowed, blocked = rules_engine.filter_queue_requests(session, rest)
                allowed_rest, blk = set(allowed), set(blocked)
                result.blocked |= blk
            _write_state(session, allowed_rest, state)
            done = waived | allowed_rest
        else:
            _write_state(session, chunk_set, state)
            done = chunk_set
        for aid in done:
            result.prev.setdefault(prev.get(aid, "candidate"), []).append(aid)
        result.updated.extend(sorted(done))
    return result


def record_undo(state: str, result: ChangeResult) -> Optional[str]:
    if not result.updated:
        return None
    return undo_store.push(state, result.prev)


def undo(session: Session, token: str) -> Optional[int]:
    """還原一次狀態變更。只還原「目前仍處於變更後狀態」的資產，不會蓋掉之後的其他決定。回傳還原張數；token 不存在回傳 None。"""
    rec = undo_store.pop(token)
    if rec is None:
        return None
    target = rec["target"]
    restored = 0
    for prev_state, ids in rec["prev"].items():
        for chunk in chunked(sorted(set(ids)), CHUNK):
            cur = {aid for (aid,) in session.query(Candidate.asset_id).filter(
                Candidate.asset_id.in_(list(chunk)), Candidate.state == target).distinct()}
            if not cur:
                continue
            _write_state(session, cur, prev_state)
            restored += len(cur)
    return restored
