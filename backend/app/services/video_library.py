"""
video_library.py（v0.7.0，VD-01 至 VD-05）— 「所有影片」頁的資料：資料庫裡**所有** media_type='video' 的資產，
不論有沒有命中任何規則；排除已刪除 / 遺失。**只讀**：不改任何狀態、不動檔案。

- 搜尋：每個以空白分隔的關鍵字都要出現在完整路徑裡（不分大小寫、忽略 / 與 \\ 的差別），所以可以搜資料夾名稱。
- 排序：name（檔名）/ date（檔案修改時間）/ size / duration / resolution（寬 × 高）。
  沒有值的（還沒做 video 階段、或 OpenCV 讀不到）一律排最後（不論升降冪）；同值以資產 id 排序，分頁穩定。
- 分頁在資料庫端完成（LIMIT / OFFSET），一次只組一頁的詳細資料；總數與總容量用一個聚合查詢。
- 檔名排序用 SQL 取路徑最後一段（SQLite 沒有 basename），不必把整個影片清單載進記憶體。
"""
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.models.schema import Asset, Candidate, File, VideoInfo

SORT_KEYS = ("name", "date", "size", "duration", "resolution")
DEFAULT_ORDER = {"name": "asc", "date": "desc", "size": "desc", "duration": "desc", "resolution": "desc"}
MAX_LIMIT = 500

_ACTIVE = Asset.status.notin_(["deleted", "missing"])
_NORM_PATH = func.replace(Asset.primary_path, "\\", "/")
# 路徑最後一段：rtrim 掉結尾「不是 /」的字元 → 剩目錄部分（含最後的 /），再從路徑中移除它
_BASENAME = func.replace(_NORM_PATH, func.rtrim(_NORM_PATH, func.replace(_NORM_PATH, "/", "")), "")


def _base_query(session: Session, *cols):
    return (session.query(*cols)
            .select_from(Asset)
            .outerjoin(File, and_(File.asset_id == Asset.id, File.role == "primary"))
            .outerjoin(VideoInfo, VideoInfo.asset_id == Asset.id)
            .filter(Asset.media_type == "video", _ACTIVE))


def _search_conditions(q: Optional[str]) -> list:
    conds = []
    for term in (q or "").replace("\\", "/").split():
        pat = "%" + term.replace("%", "\\%").replace("_", "\\_") + "%"
        conds.append(_NORM_PATH.like(pat, escape="\\"))     # SQLite 的 LIKE 對 ASCII 不分大小寫
    return conds


def _order_by(sort: str, order: str) -> list:
    desc = order == "desc"
    if sort == "name":
        col = func.lower(_BASENAME)
    elif sort == "date":
        col = File.mtime
    elif sort == "size":
        col = Asset.size_total
    elif sort == "duration":
        col = VideoInfo.duration
    else:  # resolution
        col = VideoInfo.width * VideoInfo.height
    return [col.is_(None).asc(), col.desc() if desc else col.asc(), Asset.id.asc()]


def summary(session: Session) -> Dict[str, Any]:
    n, size = _base_query(session, func.count(Asset.id), func.coalesce(func.sum(Asset.size_total), 0)).one()
    return {"count": int(n or 0), "total_size": int(size or 0)}


def _filename(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return None


def _items(session: Session, ids: Sequence[int]) -> List[Dict[str, Any]]:
    if not ids:
        return []
    rows = {r.id: r for r in _base_query(
        session, Asset.id.label("id"), Asset.primary_path, Asset.size_total, Asset.status,
        File.mtime.label("mtime"), VideoInfo.duration, VideoInfo.width, VideoInfo.height, VideoInfo.codec,
    ).filter(Asset.id.in_(list(ids)))}
    rules: Dict[int, List[str]] = defaultdict(list)
    keeper: set = set()
    for aid, rule, is_keeper in session.query(Candidate.asset_id, Candidate.rule, Candidate.is_keeper)\
            .filter(Candidate.asset_id.in_(list(ids))).order_by(Candidate.score.desc(), Candidate.id):
        if rule not in rules[aid]:
            rules[aid].append(rule)
        if is_keeper:
            keeper.add(aid)
    out: List[Dict[str, Any]] = []
    for aid in ids:
        r = rows.get(aid)
        if r is None:
            continue
        out.append({
            "asset_id": r.id,
            "filename": _filename(r.primary_path),
            "primary_path": r.primary_path,
            "size_total": r.size_total,
            "duration": r.duration,
            "width": r.width,
            "height": r.height,
            "codec": r.codec,
            "modified_at": _iso(r.mtime),
            "status": r.status,                          # unanalysed / analysed / candidate / queued / kept
            "rules": rules.get(r.id, []),                # 命中的規則（沒有命中就是空的）
            "is_keeper": r.id in keeper,
            "thumbnail_url": f"/api/thumbnails/{r.id}" + (f"?v={int(r.mtime)}" if r.mtime else ""),
        })
    return out


def list_videos(session: Session, *, q: Optional[str] = None, sort: str = "name", order: Optional[str] = None,
                offset: int = 0, limit: int = 100) -> Dict[str, Any]:
    if sort not in SORT_KEYS:
        raise ValueError(f"不合法的排序：{sort!r}")
    order = order or DEFAULT_ORDER[sort]
    limit = max(1, min(int(limit), MAX_LIMIT))
    offset = max(0, int(offset))
    conds = _search_conditions(q)

    total, total_size = _base_query(session, func.count(Asset.id),
                                    func.coalesce(func.sum(Asset.size_total), 0)).filter(*conds).one()
    ids = [r.id for r in _base_query(session, Asset.id.label("id")).filter(*conds)
           .order_by(*_order_by(sort, order)).offset(offset).limit(limit)]
    return {
        "items": _items(session, ids),
        "total": int(total or 0), "total_size": int(total_size or 0),
        "offset": offset, "limit": limit, "has_more": offset + len(ids) < int(total or 0),
        "sort": sort, "order": order, "q": q or "",
    }
