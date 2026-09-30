"""
掃描 API（向下相容層）。

新的主要端點已移至 /api/jobs（app/api/jobs.py）。
此模組保留 /api/scan/* 路由以維持向下相容，
內部委託給新的 scan_job 服務。

POST /api/scan/all       → 完整掃描（等同 POST /api/jobs/scan with mode=full）
POST /api/scan/{root_id} → 指定目錄完整掃描
GET  /api/scan/status    → 進度查詢（與 /api/jobs/status 共用 progress_tracker）
GET  /api/scan/events    → SSE 進度推送
POST /api/scan/cancel    → 取消

注意：舊版 /api/scan/* 觸發的是「掃描+分析+評估」一體流程。
      新版只做掃描，不觸發分析。若需要完整流程，請改用 /api/jobs/*。
"""

import json
import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.core.database import get_db, SyncSessionLocal
from app.core.progress import progress_tracker
from app.models.schema import Root
from app.services.scan_job import run_scan_job

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scan", tags=["scan"])


def _run_scan_bg(root_id: Optional[int], mode: str = "full"):
    """內部背景掃描任務（scan only，不觸發分析）。"""
    progress_tracker.start_scan(mode=mode)
    with SyncSessionLocal() as session:
        try:
            root_ids = [root_id] if root_id is not None else None
            summary = run_scan_job(
                session,
                root_ids=root_ids,
                mode=mode,
                progress_tracker=progress_tracker,
            )
            session.commit()
            progress_tracker.complete_scan(
                summary={
                    "added": summary.added,
                    "modified": summary.modified,
                    "missing": summary.missing,
                    "unchanged": summary.unchanged,
                }
            )
        except InterruptedError:
            session.rollback()
            logger.info("掃描被取消")
        except Exception as e:
            session.rollback()
            logger.error(f"掃描錯誤：{e}", exc_info=True)
            progress_tracker.fail_job(str(e))


# ─── 舊版相容的 run_sync_scan（供現有測試使用） ───────────────────────────────────

def run_sync_scan(root_id: Optional[int] = None):
    """
    【向下相容】背景掃描任務。
    新代碼請改用 /api/jobs/scan 端點或直接呼叫 run_scan_job()。
    """
    _run_scan_bg(root_id, mode="full")


# ─── API 端點 ──────────────────────────────────────────────────────────────────

@router.get("/status")
async def get_scan_status():
    """取得當前任務進度狀態（與 /api/jobs/status 共用）。"""
    return progress_tracker.get_state()


@router.get("/events")
async def scan_events(request: Request):
    """Server-Sent Events (SSE) 即時推送掃描進度。"""
    async def event_generator():
        async for state in progress_tracker.subscribe():
            if await request.is_disconnected():
                break
            yield f"data: {json.dumps(state, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/cancel")
async def cancel_scan():
    """取消當前正在進行的任務。"""
    progress_tracker.request_cancel()
    return {"status": "ok", "message": "已發送取消請求"}


@router.post("/all")
async def trigger_scan_all(background_tasks: BackgroundTasks):
    """完整掃描所有已設定的路徑（scan only）。"""
    if progress_tracker.state.is_running:
        raise HTTPException(status_code=400, detail="目前已有任務進行中")
    background_tasks.add_task(_run_scan_bg, None, "full")
    return {"status": "ok", "message": "已在背景啟動全庫掃描"}


@router.post("/{root_id}")
async def trigger_scan(
    root_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """掃描指定的路徑（scan only）。"""
    if progress_tracker.state.is_running:
        raise HTTPException(status_code=400, detail="目前已有任務進行中")

    root = await db.get(Root, root_id)
    if not root:
        raise HTTPException(status_code=404, detail="找不到指定的路徑")

    background_tasks.add_task(_run_scan_bg, root_id, "full")
    return {"status": "ok", "message": f"已在背景啟動目錄掃描：{root.path}"}
