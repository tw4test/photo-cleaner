import json
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.database import SyncSessionLocal
from app.core.defaults import DEFAULT_SETTINGS, load_effective_settings, validate_setting
from app.core.settings_meta import changed_settings, keys_differing_from_default, needs_evaluation, settings_meta
from app.models.schema import Setting

__all__ = ["router", "DEFAULT_SETTINGS"]

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsUpdate(BaseModel):
    settings: Dict[str, Any]
    re_evaluate: bool = True


@router.get("", response_model=Dict[str, Any])
def get_settings():
    with SyncSessionLocal() as session:
        return load_effective_settings(session)


@router.get("/meta")
def get_settings_meta():
    """預設值、數值範圍、選項、以及「不影響評估」的設定鍵（設定頁用）。"""
    return settings_meta()


@router.put("")
def update_settings(payload: SettingsUpdate):
    """
    儲存設定。v0.6.1：只寫入「與目前值不同」的項目，且只有在變更了會影響評估結果的設定時，
    才（在 re_evaluate=true 時）佔用任務鎖並重新評估。

    - 沒有任何變更 → 直接回傳，不寫入、不評估、不檢查任務。
    - 只改效能類設定（runners、批次大小…）→ 只寫入；分析進行中也可以儲存。
    - 改了會影響評估的設定且 re_evaluate=true → 先佔住任務鎖（有任務進行中回 409，不寫入任何設定），
      寫入後重新評估。
    - 改了會影響評估的設定但 re_evaluate=false → 只寫入；評估狀態會變成「過期」，之後再手動評估。
    """
    cleaned: Dict[str, Any] = {}
    errors = []
    for key, val in payload.settings.items():
        try:
            cleaned[key] = validate_setting(key, val)
        except ValueError as e:
            errors.append(str(e))
    if errors:
        raise HTTPException(status_code=422, detail="；".join(errors))

    with SyncSessionLocal() as session:
        current = load_effective_settings(session)
    changed = changed_settings(current, cleaned)
    if not changed:
        return {"status": "ok", "message": "沒有需要儲存的變更", "changed": [], "evaluated": False}

    affects_evaluation = needs_evaluation(changed.keys())
    do_evaluate = payload.re_evaluate and affects_evaluation

    # F-13：需要重新評估時，先確認沒有其他任務、並佔住任務鎖，才寫入設定（避免寫了一半才回 409）
    from app.api.jobs import run_evaluate_now, start_evaluate_or_409
    if do_evaluate:
        start_evaluate_or_409()
    try:
        with SyncSessionLocal() as session:
            for key, val in changed.items():
                s = json.dumps(val, ensure_ascii=False)
                row = session.get(Setting, key)
                if row:
                    row.value = s
                else:
                    session.add(Setting(key=key, value=s))
            session.commit()
    except Exception as e:
        if do_evaluate:
            from app.core.progress import progress_tracker
            progress_tracker.fail_job(f"儲存設定失敗：{e}")
        raise

    msg = "設定已成功儲存"
    if do_evaluate:
        run_evaluate_now(force=True, already_started=True)
        msg += "並已重新評估所有規則！"
    elif affects_evaluation:
        msg += "（尚未重新評估，候選清單可能已過期）"
    return {"status": "ok", "message": msg, "changed": sorted(changed), "evaluated": do_evaluate}


@router.post("/reset")
def reset_settings():
    """還原所有設定（含未顯示的）為預設值。只有當被還原的設定會影響評估時，才重新評估。"""
    from app.api.jobs import run_evaluate_now, start_evaluate_or_409
    with SyncSessionLocal() as session:
        differing = keys_differing_from_default(load_effective_settings(session))
    do_evaluate = needs_evaluation(differing)
    if do_evaluate:
        start_evaluate_or_409()   # F-13：先確認沒有任務在跑，才清除設定
    try:
        with SyncSessionLocal() as session:
            session.query(Setting).delete()
            session.commit()
    except Exception as e:
        if do_evaluate:
            from app.core.progress import progress_tracker
            progress_tracker.fail_job(f"重設設定失敗：{e}")
        raise
    if do_evaluate:
        run_evaluate_now(force=True, already_started=True)
        return {"status": "ok", "message": "已還原為預設設定並重新評估規則", "evaluated": True}
    return {"status": "ok", "message": "已還原為預設設定（不需要重新評估）", "evaluated": False}
