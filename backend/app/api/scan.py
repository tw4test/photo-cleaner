"""
掃描 API（向下相容層）。新的主要端點在 /api/jobs。
POST /api/scan/all       → 完整掃描所有路徑
POST /api/scan/{root_id} → 指定目錄完整掃描（v0.5 修正：與前端 api.ts 一致使用路徑參數）
GET  /api/scan/status    → 進度（與 /api/jobs/status 共用）
GET  /api/scan/events    → SSE
POST /api/scan/cancel    → 取消
"""
import json
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.api.jobs import BUSY, run_scan_job_bg
from app.core.progress import progress_tracker

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/scan", tags=["scan"])


def run_sync_scan(root_id: Optional[int] = None):
    """【向下相容 / 測試用】同步執行完整掃描。"""
    if not progress_tracker.try_start_scan("full"):
        raise RuntimeError(BUSY)
    run_scan_job_bg("full", root_id)


@router.get("/status")
def get_scan_status():
    return progress_tracker.get_state()


@router.get("/events")
async def scan_events(request: Request):
    async def event_generator():
        async for state in progress_tracker.subscribe():
            if await request.is_disconnected():
                break
            yield f"data: {json.dumps(state, ensure_ascii=False)}\n\n"
    return StreamingResponse(event_generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                                      "X-Accel-Buffering": "no"})


@router.post("/cancel")
def cancel_scan():
    progress_tracker.request_cancel()
    return {"status": "ok", "message": "已發送取消請求"}


@router.post("/all")
def trigger_scan_all(background_tasks: BackgroundTasks):
    if not progress_tracker.try_start_scan("full"):
        raise HTTPException(status_code=400, detail=BUSY)
    background_tasks.add_task(run_scan_job_bg, "full", None)
    return {"status": "ok", "message": "已在背景啟動全庫掃描"}


@router.post("/{root_id}")
def trigger_scan(root_id: int, background_tasks: BackgroundTasks):
    if not progress_tracker.try_start_scan("full"):
        raise HTTPException(status_code=400, detail=BUSY)
    background_tasks.add_task(run_scan_job_bg, "full", root_id)
    return {"status": "ok", "message": f"已在背景啟動目錄掃描（root_id={root_id}）"}
