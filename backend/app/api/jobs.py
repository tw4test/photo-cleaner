"""
Jobs API：掃描、分析、補算縮圖、評估規則的觸發端點。
取代舊版 scan.py 中的一體化流程。
"""

import json
import asyncio
import logging
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List

from app.core.database import SyncSessionLocal
from app.core.progress import progress_tracker
from app.models.schema import Candidate
from sqlalchemy import func

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


# ─── Request schemas ──────────────────────────────────────────────────────────

class ScanRequest(BaseModel):
    mode: str = "full"          # 'full' | 'new_only'
    root_id: Optional[int] = None


class AnalyzeRequest(BaseModel):
    stages: List[str] = ["hash", "basic", "clip", "video"]
    device: str = "auto"        # 'auto' | 'cuda' | 'cpu'
    retry_failed: bool = False


# ─── Background workers ────────────────────────────────────────────────────────

def _run_scan_bg(mode: str, root_id: Optional[int]):
    """掃描背景任務：只做目錄同步，不做特徵分析。"""
    from app.services.scan_job import run_scan_job

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
            logger.info(f"掃描完成：{summary}")
        except InterruptedError:
            session.rollback()
            logger.info("掃描被使用者取消")
        except Exception as e:
            session.rollback()
            logger.error(f"掃描發生錯誤：{e}", exc_info=True)
            progress_tracker.fail_job(str(e))


def _run_analyze_bg(stages: List[str], device: str, retry_failed: bool):
    """分析背景任務：執行指定的分析階段。"""
    from app.services.analyze_job import run_analyze_job

    progress_tracker.start_analyze(stages=stages, device=device)

    with SyncSessionLocal() as session:
        try:
            results = run_analyze_job(
                session,
                stages=stages,
                device=device,
                retry_failed=retry_failed,
                progress_tracker=progress_tracker,
            )
            session.commit()

            device_used = "cpu"
            for r in results.values():
                if isinstance(r, dict) and r.get("device_used"):
                    device_used = r["device_used"]
                    break

            progress_tracker.complete_analyze(results=results, device_used=device_used)
            logger.info(f"分析完成：{results}")
        except InterruptedError:
            session.rollback()
            logger.info("分析被使用者取消")
        except RuntimeError as e:
            # GPU 不可用等嚴重錯誤
            session.rollback()
            logger.error(f"分析嚴重錯誤（可能是 GPU 問題）：{e}")
            progress_tracker.fail_job(str(e))
        except Exception as e:
            session.rollback()
            logger.error(f"分析發生錯誤：{e}", exc_info=True)
            progress_tracker.fail_job(str(e))


def _run_rebuild_thumbs_bg():
    """補算縮圖背景任務：只補缺少的縮圖，不重跑其他分析。"""
    from app.services.basic_stage import run_rebuild_thumbs_only

    progress_tracker.start_rebuild_thumbs()

    with SyncSessionLocal() as session:
        try:
            result = run_rebuild_thumbs_only(session, progress_tracker=progress_tracker)
            session.commit()
            progress_tracker.complete_rebuild_thumbs(done=result.get("done", 0))
            logger.info(f"縮圖補算完成：{result}")
        except Exception as e:
            session.rollback()
            logger.error(f"縮圖補算錯誤：{e}", exc_info=True)
            progress_tracker.fail_job(str(e))


def _run_evaluate_bg():
    """評估背景任務：執行規則引擎，產出候選清單。"""
    from app.services.rules_engine import rules_engine

    progress_tracker.set_evaluating_rules()

    with SyncSessionLocal() as session:
        try:
            rules_engine.evaluate_all_rules(session)
            candidates_count = session.query(
                func.count(func.distinct(Candidate.asset_id))
            ).filter(Candidate.is_keeper == False).scalar() or 0
            session.commit()
            progress_tracker.complete_evaluate(new_candidates=candidates_count)
            logger.info(f"評估完成，候選資產：{candidates_count}")
        except Exception as e:
            session.rollback()
            logger.error(f"評估錯誤：{e}", exc_info=True)
            progress_tracker.fail_job(str(e))


# ─── API 端點 ──────────────────────────────────────────────────────────────────

@router.post("/scan")
async def trigger_scan(payload: ScanRequest, background_tasks: BackgroundTasks):
    """
    觸發掃描任務。

    - mode='full'（預設）：完整掃描 — 新增、偵測修改（清除分析結果）、偵測消失（標為 missing）
    - mode='new_only'：只加入新檔案，不動已有資產
    """
    if progress_tracker.state.is_running:
        raise HTTPException(status_code=400, detail="目前已有任務進行中，請等待完成後再試")

    if payload.mode not in ("full", "new_only"):
        raise HTTPException(status_code=422, detail=f"不合法的掃描模式：{payload.mode!r}，合法值：full, new_only")

    background_tasks.add_task(_run_scan_bg, payload.mode, payload.root_id)
    return {"status": "ok", "message": f"已啟動掃描任務（mode={payload.mode}）"}


@router.post("/analyze")
async def trigger_analyze(payload: AnalyzeRequest, background_tasks: BackgroundTasks):
    """
    觸發分析任務。

    - stages: 要執行的階段列表（hash、basic、clip、video 的子集）
    - device: 運算裝置（auto、cuda、cpu），只影響 clip 階段
    - retry_failed: 是否重試之前失敗的項目
    """
    if progress_tracker.state.is_running:
        raise HTTPException(status_code=400, detail="目前已有任務進行中，請等待完成後再試")

    valid_stages = {"hash", "basic", "clip", "video"}
    invalid = [s for s in payload.stages if s not in valid_stages]
    if invalid:
        raise HTTPException(status_code=422, detail=f"不合法的分析階段：{invalid}")

    if payload.device not in ("auto", "cuda", "cpu"):
        raise HTTPException(status_code=422, detail=f"不合法的裝置選擇：{payload.device!r}")

    if not payload.stages:
        raise HTTPException(status_code=422, detail="至少需指定一個分析階段")

    background_tasks.add_task(_run_analyze_bg, payload.stages, payload.device, payload.retry_failed)
    return {"status": "ok", "message": f"已啟動分析任務（stages={payload.stages}, device={payload.device}）"}


@router.post("/rebuild_thumbs")
async def trigger_rebuild_thumbs(background_tasks: BackgroundTasks):
    """補算缺少的縮圖，不重跑其他分析（對應 A-20）。"""
    if progress_tracker.state.is_running:
        raise HTTPException(status_code=400, detail="目前已有任務進行中，請等待完成後再試")

    background_tasks.add_task(_run_rebuild_thumbs_bg)
    return {"status": "ok", "message": "已啟動縮圖補算任務"}


@router.post("/evaluate")
async def trigger_evaluate(background_tasks: BackgroundTasks):
    """重新執行規則引擎，產出候選清單。"""
    if progress_tracker.state.is_running:
        raise HTTPException(status_code=400, detail="目前已有任務進行中，請等待完成後再試")

    background_tasks.add_task(_run_evaluate_bg)
    return {"status": "ok", "message": "已啟動規則評估任務"}


@router.get("/status")
async def get_job_status():
    """取得當前任務進度狀態（掃描/分析/補算/評估皆使用同一個 progress tracker）。"""
    return progress_tracker.get_state()


@router.get("/events")
async def job_events(request: Request):
    """Server-Sent Events (SSE) 即時推送任務進度。"""
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
async def cancel_job():
    """取消目前正在進行的任務。"""
    progress_tracker.request_cancel()
    return {"status": "ok", "message": "已發送取消請求"}
