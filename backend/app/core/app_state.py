"""app_state 表的讀寫工具（JSON 值）。"""
import json
from typing import Any

from app.models.schema import AppState


def get_state(session, key: str, default: Any = None) -> Any:
    row = session.get(AppState, key)
    if row is None:
        return default
    try:
        return json.loads(row.value)
    except Exception:
        return default


def set_state(session, key: str, value: Any) -> None:
    s = json.dumps(value, ensure_ascii=False, default=str)
    row = session.get(AppState, key)
    if row is None:
        session.add(AppState(key=key, value=s))
    else:
        row.value = s
