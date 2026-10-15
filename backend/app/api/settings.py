import json
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.database import SyncSessionLocal
from app.core.defaults import DEFAULT_SETTINGS, load_effective_settings, validate_setting
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


@router.put("")
def update_settings(payload: SettingsUpdate):
    cleaned: Dict[str, Any] = {}
    errors = []
    for key, val in payload.settings.items():
        try:
            cleaned[key] = validate_setting(key, val)
        except ValueError as e:
            errors.append(str(e))
    if errors:
        raise HTTPException(status_code=422, detail="；".join(errors))
    # F-13：需要重新評估時，先確認沒有其他任務、並佔住任務鎖，才寫入設定（避免寫了一半才回 409）
    from app.api.jobs import run_evaluate_now, start_evaluate_or_409
    if payload.re_evaluate:
        start_evaluate_or_409()
    try:
        with SyncSessionLocal() as session:
            for key, val in cleaned.items():
                s = json.dumps(val, ensure_ascii=False)
                row = session.get(Setting, key)
                if row:
                    row.value = s
                else:
                    session.add(Setting(key=key, value=s))
            session.commit()
    except Exception as e:
        if payload.re_evaluate:
            from app.core.progress import progress_tracker
            progress_tracker.fail_job(f"儲存設定失敗：{e}")
        raise
    msg = "設定已成功儲存"
    if payload.re_evaluate:
        run_evaluate_now(force=True, already_started=True)
        msg += "並已重新評估所有規則！"
    return {"status": "ok", "message": msg}


@router.post("/reset")
def reset_settings():
    from app.api.jobs import run_evaluate_now, start_evaluate_or_409
    start_evaluate_or_409()   # F-13：先確認沒有任務在跑，才清除設定
    try:
        with SyncSessionLocal() as session:
            session.query(Setting).delete()
            session.commit()
    except Exception as e:
        from app.core.progress import progress_tracker
        progress_tracker.fail_job(f"重設設定失敗：{e}")
        raise
    run_evaluate_now(force=True, already_started=True)
    return {"status": "ok", "message": "已還原為預設設定並重新評估規則"}
