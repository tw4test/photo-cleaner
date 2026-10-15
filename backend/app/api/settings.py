import json
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.database import SyncSessionLocal
from app.core.defaults import DEFAULT_SETTINGS, load_effective_settings, validate_setting
from app.models.schema import Setting

# 向下相容：其他模組仍可 from app.api.settings import DEFAULT_SETTINGS
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
    """先驗證全部欄位（未知鍵、型別、範圍），全部通過才寫入。"""
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
        for key, val in cleaned.items():
            str_val = json.dumps(val, ensure_ascii=False)
            row = session.get(Setting, key)
            if row:
                row.value = str_val
            else:
                session.add(Setting(key=key, value=str_val))
        session.commit()
    msg = "設定已成功儲存"
    if payload.re_evaluate:
        from app.api.jobs import run_evaluate_now
        run_evaluate_now()
        msg += "並已重新評估所有規則！"
    return {"status": "ok", "message": msg}


@router.post("/reset")
def reset_settings():
    with SyncSessionLocal() as session:
        session.query(Setting).delete()
        session.commit()
    from app.api.jobs import run_evaluate_now
    run_evaluate_now()
    return {"status": "ok", "message": "已還原為預設設定並重新評估規則"}
