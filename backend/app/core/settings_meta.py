"""
settings_meta.py（v0.6.1）— 設定頁用的輔助資訊。

- NON_EVALUATION_KEYS：只影響「分析效能」、不影響評估結果的設定。
  只改這些設定時，不需要重新評估，也不需要佔用任務鎖（分析進行中也能儲存）。
  其餘所有設定一律視為「會影響評估」。把某個設定漏列在這裡只會多做一次評估（安全方向）；
  把會影響評估的設定誤列進來才會出錯，所以新增設定時預設不要放進這個集合。
- changed_settings()：只留下與目前生效值不同的項目。
- needs_evaluation()：這批變更是否需要重新評估。
- settings_meta()：預設值、範圍、選項，給前端使用（預設值仍只定義在 core/defaults.py）。
"""
from typing import Any, Dict, Iterable, List

from app.core.defaults import DEFAULT_SETTINGS, SETTING_CHOICES, SETTING_RANGES

NON_EVALUATION_KEYS = frozenset({
    "hash_runners",
    "basic_runners",
    "clip_runners",
    "video_runners",
    "clip_batch_size",
    "deletion_mode",          # 後端刪除一律由 /api/process/execute 的 mode 參數決定，此設定不參與評估
    "auto_evaluate_on_save",  # 已由「依需要才評估」取代，前端不再顯示
})


def changed_settings(current: Dict[str, Any], cleaned: Dict[str, Any]) -> Dict[str, Any]:
    """cleaned 中與 current 不同的項目（cleaned 的值必須已通過 validate_setting）。"""
    return {k: v for k, v in cleaned.items() if current.get(k, DEFAULT_SETTINGS.get(k)) != v}


def needs_evaluation(keys: Iterable[str]) -> bool:
    """這些設定鍵之中，是否有任何一個會影響評估結果。"""
    return any(k not in NON_EVALUATION_KEYS for k in keys)


def keys_differing_from_default(current: Dict[str, Any]) -> List[str]:
    return [k for k, v in DEFAULT_SETTINGS.items() if current.get(k, v) != v]


def settings_meta() -> Dict[str, Any]:
    return {
        "defaults": dict(DEFAULT_SETTINGS),
        "ranges": {k: [lo, hi] for k, (lo, hi) in SETTING_RANGES.items()},
        "choices": {k: list(v) for k, v in SETTING_CHOICES.items()},
        "non_evaluation_keys": sorted(NON_EVALUATION_KEYS),
    }
