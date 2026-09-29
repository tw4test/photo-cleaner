from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional
import json

from app.core.database import get_db, SyncSessionLocal
from app.models.schema import Setting
from app.services.rules_engine import rules_engine

router = APIRouter(prefix="/api/settings", tags=["settings"])

DEFAULT_SETTINGS: Dict[str, Any] = {
    # 規則開關
    "rule_exact_dup_enabled": True,
    "rule_near_dup_enabled": True,
    "rule_burst_enabled": True,
    "rule_blur_enabled": True,
    "rule_exposure_enabled": True,
    "rule_screenshot_enabled": False,
    "rule_mistouch_enabled": True,
    "rule_video_same_size_enabled": True,
    "rule_video_tiny_enabled": False,

    # 門檻參數
    "phash_threshold": 6,                    # pHash 漢明距離門檻 (0-20, 越小越嚴格)
    "blur_threshold": 100.0,                 # Laplacian 方差模糊門檻 (低於視為模糊)
    "under_exposure_threshold": 30.0,        # 過暗亮度門檻 (0-255)
    "over_exposure_threshold": 225.0,        # 過曝亮度門檻 (0-255)
    "burst_time_window_seconds": 2.0,        # 連拍時間差判定 (秒)
    "burst_similarity_threshold": 0.85,      # 連拍相似度門檻
    "video_tiny_threshold_kb": 100,          # 極小影片門檻 (KB)

    # 重複保留策略
    "keeper_strategy": "priority,resolution,size,oldest",  # 優先順序: 路徑優先 > 解析度 > 檔案大小 > 最舊
    "cross_path_duplicates": True,           # 跨路徑比對重複

    # 刪除與安全
    "deletion_mode": "trash",                # "trash" (資源回收筒) | "quarantine" (隔離) | "permanent" (永久)
    "auto_evaluate_on_save": True,           # 儲存設定後自動重新評估規則
}

class SettingsUpdate(BaseModel):
    settings: Dict[str, Any]
    re_evaluate: bool = True

@router.get("", response_model=Dict[str, Any])
async def get_settings(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Setting))
    db_settings = result.scalars().all()

    current_settings = DEFAULT_SETTINGS.copy()
    for s in db_settings:
        try:
            current_settings[s.key] = json.loads(s.value)
        except Exception:
            current_settings[s.key] = s.value

    return current_settings

@router.put("")
async def update_settings(
    payload: SettingsUpdate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    with SyncSessionLocal() as session:
        for key, val in payload.settings.items():
            str_val = json.dumps(val, ensure_ascii=False)
            setting_obj = session.get(Setting, key)
            if setting_obj:
                setting_obj.value = str_val
            else:
                session.add(Setting(key=key, value=str_val))
        session.commit()

        # 自動重新評估規則
        if payload.re_evaluate:
            rules_engine.evaluate_all_rules(session)

    return {"status": "ok", "message": "設定已成功儲存" + ("並已重新評估所有規則！" if payload.re_evaluate else "。")}

@router.post("/reset")
async def reset_settings(background_tasks: BackgroundTasks):
    with SyncSessionLocal() as session:
        session.query(Setting).delete()
        for key, val in DEFAULT_SETTINGS.items():
            session.add(Setting(key=key, value=json.dumps(val, ensure_ascii=False)))
        session.commit()
        rules_engine.evaluate_all_rules(session)

    return {"status": "ok", "message": "已還原為預設設定並重新評估規則"}
