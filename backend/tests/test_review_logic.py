"""v0.6.3：審核頁的純邏輯（不需要資料庫）。"""
import pytest

from app.services.review_logic import (
    CATEGORY_ORDER, CATEGORY_RULES, CHUNK, WAIVABLE_RULES, UndoStore, chunked, rules_for_category, summarise,
)


def test_every_rule_maps_to_exactly_one_category_and_all_is_first():
    assert CATEGORY_ORDER[0] == "all"
    seen = [r for rules in CATEGORY_RULES.values() for r in rules]
    assert len(seen) == len(set(seen))
    # 規則引擎產生的所有規則名稱都必須有對應的分類
    engine_rules = {"exact_dup", "near_dup", "burst", "blur", "over_exposure", "under_exposure",
                    "screenshot", "document", "meme", "mistouch", "video_same_size", "video_tiny"}
    assert engine_rules == set(seen)


def test_rules_for_category():
    assert rules_for_category("all") is None
    assert rules_for_category("exposure") == ("over_exposure", "under_exposure")
    with pytest.raises(ValueError):
        rules_for_category("nope")


def test_only_screenshot_and_document_can_be_waived():
    assert set(WAIVABLE_RULES) == {"screenshot", "document"}


def test_summarise_counts_distinct_assets_and_ignores_other_states_for_categories():
    rows = [
        (1, "screenshot", "candidate", 100), (1, "near_dup", "candidate", 100),     # 同一張命中兩個規則
        (2, "blur", "candidate", 50),
        (3, "over_exposure", "candidate", 10), (4, "under_exposure", "candidate", 20),
        (5, "screenshot", "queued", 999),                                           # 其他狀態
        (6, "bogus_rule", "candidate", 7),                                          # 未知規則：算在「全部」，不屬於任何分類
    ]
    keepers = [(9, "near_dup", "candidate", 1), (8, "near_dup", "queued", 1)]
    s = summarise(rows, keepers, "candidate")
    assert s["states"]["candidate"] == {"count": 5, "size_bytes": 100 + 50 + 10 + 20 + 7}
    assert s["states"]["queued"] == {"count": 1, "size_bytes": 999} and s["states"]["kept"]["count"] == 0
    cats = {c["key"]: c for c in s["categories"]}
    assert [c["key"] for c in s["categories"]] == CATEGORY_ORDER
    assert cats["all"]["count"] == 5
    assert cats["screenshot"]["count"] == 1 and cats["near_dup"]["count"] == 1
    assert cats["exposure"] == {"key": "exposure", "count": 2, "size_bytes": 30, "keepers": 0}
    assert cats["near_dup"]["keepers"] == 1 and cats["all"]["keepers"] == 1     # 只算目前狀態的 Keeper
    assert cats["meme"]["count"] == 0


def test_summarise_handles_missing_sizes():
    s = summarise([(1, "blur", "candidate", None)], [], "candidate")
    assert s["states"]["candidate"] == {"count": 1, "size_bytes": 0}


def test_chunked():
    assert [len(c) for c in chunked(list(range(CHUNK * 2 + 3)))] == [CHUNK, CHUNK, 3]
    assert list(chunked([], 5)) == []


def test_undo_store_pop_once_and_capacity():
    u = UndoStore(capacity=2)
    t1 = u.push("queued", {"candidate": [1]})
    t2 = u.push("queued", {"candidate": [2]})
    t3 = u.push("kept", {"candidate": [3]})
    assert len(u) == 2 and u.pop(t1) is None          # 最舊的被擠掉
    assert u.pop(t2) == {"target": "queued", "prev": {"candidate": [2]}}
    assert u.pop(t2) is None                          # 只能取一次
    assert u.pop(t3)["target"] == "kept"
