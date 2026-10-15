"""
系統設定的唯一預設值來源（Single Source of Truth）。

api/settings.py、rules_engine、clip_stage、analysis API 都從這裡讀取，
不再各自維護一份 DEFAULT_SETTINGS / DEFAULT_CONFIG。
"""
import json
from typing import Any, Dict

from app.core.config import settings as app_settings

DEFAULT_SETTINGS: Dict[str, Any] = {
    # ── 規則開關 ──────────────────────────────────────────
    "rule_exact_dup_enabled": True,
    "rule_near_dup_enabled": True,
    "rule_burst_enabled": True,
    "rule_blur_enabled": True,
    "rule_exposure_enabled": True,
    "rule_video_same_size_enabled": True,
    "rule_video_tiny_enabled": False,
    # AI 內容語義分類開關 (CLIP Zero-Shot)
    "rule_screenshot_enabled": True,
    "rule_document_enabled": True,
    "rule_meme_enabled": True,
    "rule_mistouch_enabled": True,
    # ── AI 信心度門檻 (0.0 ~ 1.0) ─────────────────────────
    "screenshot_threshold": 0.60,
    "document_threshold": 0.55,
    "meme_threshold": 0.65,
    "mistouch_threshold": 0.55,
    # ── 近似重複 / 連拍 ───────────────────────────────────
    # near_dup_method: clip | phash | both | either
    #   clip   = 只用 CLIP 餘弦相似度（能找出裁切、調色、不同解析度的版本）
    #   phash  = 只用 pHash 漢明距離（只找外觀幾乎一模一樣的）
    #   both   = 兩者都要符合（最嚴格）
    #   either = 任一符合即可（預設；沒有 CLIP 向量的相片仍可用 pHash 找）
    "near_dup_method": "either",
    "clip_dup_threshold": 0.95,
    "phash_threshold": 6,
    "near_dup_max_group": 50,          # 超過此數的群組會自動提高門檻拆小，避免串連成大群
    "burst_time_window_seconds": 2.0,
    "burst_similarity_threshold": 0.85,
    # ── 畫質門檻（以最長邊 1024px 的縮小版計算）──────────
    "blur_threshold": 100.0,
    "under_exposure_threshold": 30.0,
    "over_exposure_threshold": 225.0,
    "video_tiny_threshold_kb": 100,
    # ── 保留 / 刪除 ───────────────────────────────────────
    "keeper_strategy": "priority,resolution,size,oldest",
    "cross_path_duplicates": True,
    "deletion_mode": "trash",
    "auto_evaluate_on_save": True,
    # ── CLIP 模型與運算 ───────────────────────────────────
    "clip_model": "ViT-B-16",
    "clip_pretrained": "openai",
    "compute_device": "auto",          # auto | cuda | cpu
    "clip_batch_size": app_settings.CLIP_BATCH_SIZE,  # 0 = 依顯存自動
    # ── 並行 Runner ──────────────────────────────────────
    "hash_runners": app_settings.HASH_RUNNERS,
    "basic_runners": app_settings.BASIC_RUNNERS,
    "clip_runners": app_settings.CLIP_RUNNERS,
    "video_runners": app_settings.VIDEO_RUNNERS,
}

# 數值範圍限制（PUT /api/settings 會檢查）
SETTING_RANGES: Dict[str, tuple] = {
    "screenshot_threshold": (0.0, 1.0),
    "document_threshold": (0.0, 1.0),
    "meme_threshold": (0.0, 1.0),
    "mistouch_threshold": (0.0, 1.0),
    "clip_dup_threshold": (0.5, 1.0),
    "burst_similarity_threshold": (0.5, 1.0),
    "phash_threshold": (0, 20),
    "near_dup_max_group": (2, 10000),
    "burst_time_window_seconds": (0.0, 60.0),
    "blur_threshold": (0.0, 100000.0),
    "under_exposure_threshold": (0.0, 255.0),
    "over_exposure_threshold": (0.0, 255.0),
    "video_tiny_threshold_kb": (0, 10_000_000),
    "clip_batch_size": (0, 1024),
    "hash_runners": (1, 16),
    "basic_runners": (1, 16),
    "clip_runners": (1, 16),
    "video_runners": (1, 16),
}

SETTING_CHOICES: Dict[str, tuple] = {
    "near_dup_method": ("clip", "phash", "both", "either"),
    "compute_device": ("auto", "cuda", "cpu"),
    "deletion_mode": ("trash", "quarantine", "permanent"),
}


def validate_setting(key: str, value: Any) -> Any:
    """驗證並轉型單一設定值；不合法時拋出 ValueError。"""
    if key not in DEFAULT_SETTINGS:
        raise ValueError(f"未知的設定項目：{key}")
    default = DEFAULT_SETTINGS[key]
    try:
        if isinstance(default, bool):
            if isinstance(value, bool):
                coerced = value
            elif isinstance(value, str) and value.lower() in ("true", "false"):
                coerced = value.lower() == "true"
            else:
                raise ValueError
        elif isinstance(default, int):
            if isinstance(value, bool):
                raise ValueError
            coerced = int(value)
            if float(value) != coerced:
                raise ValueError
        elif isinstance(default, float):
            if isinstance(value, bool):
                raise ValueError
            coerced = float(value)
        else:
            coerced = str(value)
    except (TypeError, ValueError):
        raise ValueError(f"設定「{key}」的值型別不正確：{value!r}")
    if key in SETTING_RANGES:
        lo, hi = SETTING_RANGES[key]
        if not (lo <= coerced <= hi):
            raise ValueError(f"設定「{key}」必須介於 {lo} ~ {hi}，收到 {coerced}")
    if key in SETTING_CHOICES and coerced not in SETTING_CHOICES[key]:
        raise ValueError(f"設定「{key}」只能是 {SETTING_CHOICES[key]}，收到 {coerced!r}")
    return coerced


def load_effective_settings(session) -> Dict[str, Any]:
    """
    預設值 + 資料庫覆寫值（Sync Session）。
    只回傳已知的設定鍵；無效或已淘汰的值會被忽略並退回預設，
    確保前端「讀取 → 修改 → 整包送回」不會因舊資料被 PUT 驗證擋下。
    """
    from app.models.schema import Setting
    cfg = dict(DEFAULT_SETTINGS)
    for s in session.query(Setting).all():
        if s.key not in DEFAULT_SETTINGS:
            continue
        try:
            raw = json.loads(s.value)
        except Exception:
            raw = s.value
        try:
            cfg[s.key] = validate_setting(s.key, raw)
        except ValueError:
            pass
    return cfg


def get_setting(session, key: str) -> Any:
    from app.models.schema import Setting
    row = session.get(Setting, key)
    if row is None:
        return DEFAULT_SETTINGS.get(key)
    try:
        raw = json.loads(row.value)
    except Exception:
        raw = row.value
    try:
        return validate_setting(key, raw)
    except ValueError:
        return DEFAULT_SETTINGS.get(key)
