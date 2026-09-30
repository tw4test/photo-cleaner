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
    # 基礎規則開關
    "rule_exact_dup_enabled": True,
    "rule_near_dup_enabled": True,
    "rule_burst_enabled": True,
    "rule_blur_enabled": True,
    "rule_exposure_enabled": True,
    "rule_video_same_size_enabled": True,
    "rule_video_tiny_enabled": False,

    # AI 內容語義分類開關 (CLIP Zero-Shot)
    "rule_screenshot_enabled": True,         # 螢幕截圖辨識
    "rule_document_enabled": True,           # 文件/收據/白板辨識
    "rule_meme_enabled": True,               # 網路梗圖/表情包辨識
    "rule_mistouch_enabled": True,           # 誤拍/口袋/地板廢片辨識

    # AI 信心度門檻 (0.0 ~ 1.0)
    "screenshot_threshold": 0.60,            # 截圖信心度門檻
    "document_threshold": 0.55,              # 文件收據信心度門檻
    "meme_threshold": 0.65,                  # 梗圖迷因信心度門檻
    "mistouch_threshold": 0.55,              # 誤拍廢片信心度門檻

    # 傳統特徵門檻
    "phash_threshold": 6,                    # pHash 漢明距離門檻 (0-20)
    "blur_threshold": 100.0,                 # Laplacian 方差模糊門檻
    "under_exposure_threshold": 30.0,        # 過暗亮度門檻
    "over_exposure_threshold": 225.0,        # 過曝亮度門檻
    "burst_time_window_seconds": 2.0,        # 連拍時間差 (秒)
    "video_tiny_threshold_kb": 100,          # 極小影片門檻 (KB)

    # 重複保留策略
    "keeper_strategy": "priority,resolution,size,oldest",
    "cross_path_duplicates": True,

    # 刪除與安全
    "deletion_mode": "trash",
    "auto_evaluate_on_save": True,

    # CLIP 模型設定
    "clip_model": "ViT-B-16",           # 模型名稱
    "clip_pretrained": "openai",        # 預訓練權重
    "compute_device": "auto",           # 全域運算裝置預設：auto | cuda | cpu
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
