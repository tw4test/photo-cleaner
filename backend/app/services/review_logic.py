"""
review_logic.py（v0.6.3）— 審核頁用的純邏輯（不依賴資料庫），方便單獨測試。

- 分類 ↔ 規則對照
- summarise()：分類 / 狀態統計（以「不重複資產」計）
- 小工具：chunked()、UndoStore（記憶體內的復原紀錄）
"""
import threading
import uuid
from collections import OrderedDict
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

CATEGORY_RULES: Dict[str, Tuple[str, ...]] = {
    "screenshot": ("screenshot",),
    "document": ("document",),
    "meme": ("meme",),
    "mistouch": ("mistouch",),
    "exact_dup": ("exact_dup",),
    "near_dup": ("near_dup",),
    "burst": ("burst",),
    "blur": ("blur",),
    "exposure": ("over_exposure", "under_exposure"),
    "video": ("video_same_size", "video_tiny"),
}
CATEGORY_ORDER: List[str] = ["all"] + list(CATEGORY_RULES.keys())
RULE_TO_CATEGORY: Dict[str, str] = {r: c for c, rules in CATEGORY_RULES.items() for r in rules}

AI_CATEGORIES = ("screenshot", "document", "meme", "mistouch")

# 使用者決定：螢幕截圖、文件 / 收據不受「重複群組至少保留一張」的保護
# （只在請求明確帶 waive_protection_for 時才生效，見 review_service.apply_state_change）
WAIVABLE_RULES = ("screenshot", "document")

STATES = ("candidate", "queued", "kept")
SORTS = ("score", "size", "path", "group")
KEEPER_MODES = ("show", "hide", "only")

CHUNK = 500   # SQLite 變數上限（舊版 999）以下的安全分塊大小


def rules_for_category(category: str) -> Optional[Tuple[str, ...]]:
    """'all' → None（不限規則）；未知分類 → ValueError。"""
    if category == "all":
        return None
    if category not in CATEGORY_RULES:
        raise ValueError(f"不合法的分類：{category!r}")
    return CATEGORY_RULES[category]


def chunked(seq: Sequence[Any], n: int = CHUNK) -> Iterator[Sequence[Any]]:
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def summarise(rows: Iterable[Tuple[int, str, str, Optional[int]]],
              keeper_rows: Iterable[Tuple[int, str, str, Optional[int]]],
              state: str) -> Dict[str, Any]:
    """
    rows        : 非 Keeper 候選列 (asset_id, rule, state, size_total)，可重複
    keeper_rows : Keeper 候選列（同格式；只需要 state 這個狀態的）
    回傳：
      states     各審核狀態的不重複資產數與容量（不含 Keeper）
      categories 指定 state 下，各分類的不重複資產數、容量、Keeper 數
    一張相片命中多個規則時，會同時算在多個分類裡；「全部」是不重複總數。
    """
    per_state: Dict[str, Dict[int, int]] = {s: {} for s in STATES}
    per_cat: Dict[str, Dict[int, int]] = {c: {} for c in CATEGORY_ORDER}
    for aid, rule, st, size in rows:
        size = size or 0
        if st in per_state:
            per_state[st][aid] = size
        if st != state:
            continue
        per_cat["all"][aid] = size
        cat = RULE_TO_CATEGORY.get(rule)
        if cat:
            per_cat[cat][aid] = size
    keepers: Dict[str, set] = {c: set() for c in CATEGORY_ORDER}
    for aid, rule, st, _size in keeper_rows:
        if st != state:
            continue
        keepers["all"].add(aid)
        cat = RULE_TO_CATEGORY.get(rule)
        if cat:
            keepers[cat].add(aid)
    return {
        "state": state,
        "states": {s: {"count": len(d), "size_bytes": sum(d.values())} for s, d in per_state.items()},
        "categories": [
            {"key": c, "count": len(per_cat[c]), "size_bytes": sum(per_cat[c].values()), "keepers": len(keepers[c])}
            for c in CATEGORY_ORDER
        ],
    }


class UndoStore:
    """記憶體內的復原紀錄（本機單一程序）。重新啟動後會消失；只保留最近 capacity 筆。"""

    def __init__(self, capacity: int = 20):
        self.capacity = capacity
        self._items: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._lock = threading.Lock()

    def push(self, target_state: str, prev: Dict[str, List[int]]) -> str:
        token = uuid.uuid4().hex
        with self._lock:
            self._items[token] = {"target": target_state, "prev": prev}
            while len(self._items) > self.capacity:
                self._items.popitem(last=False)
        return token

    def pop(self, token: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._items.pop(token, None)

    def __len__(self) -> int:
        return len(self._items)


undo_store = UndoStore()
