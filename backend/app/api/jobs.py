"""
Jobs API：掃描、分析、補算縮圖、評估規則的觸發端點。

v0.5：
- 「檢查是否有任務 + 標記開始」在 endpoint 內原子完成（progress_tracker.try_start_*），
  連點兩次只會啟動一個任務。
- 背景任務一律在 finally 收尾，取消後要等工作真的停下來才能啟動下一個任務。
"""
import json
import logging
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func

from app.core.database import SyncSessionLocal
from app.core.progress import progress_tracker
from app.models.schema import Candidate

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/jobs", tags=["jobs"])

BUSY = "目前已有任務進行中，請等待完成後再試"


class ScanRequest(BaseModel):
    mode: str = "full"          # 'full' | 'new_only'
    root_id: Optional[int] = None


class AnalyzeRequest(BaseModel):
    stages: List[str] = ["hash", "basic", "clip", "video"]
    device: str = "auto"        # 'auto' | 'cuda' | 'cpu'
    retry_failed: bool = False


# ─── Background workers（呼叫前 progress_tracker 必須已 try_start 成功）──────────
def run_scan_job_bg(mode: str, root_id: Optional[int]):
    from app.services.scan_job import run_scan_job
    with SyncSessionLocal() as session:
        try:
            summary = run_scan_job(session, root_ids=[root_id] if root_id is not None else None,
                                   mode=mode, progress_tracker=progress_tracker)
            session.commit()
            progress_tracker.complete_scan(summary={
                "added": summary.added, "modified": summary.modified,
                "missing": summary.missing, "unchanged": summary.unchanged,
            })
        except InterruptedError:
            session.rollback()
            logger.info("掃描被使用者取消")
        except Exception as e:
            session.rollback()
            logger.error(f"掃描發生錯誤：{e}", exc_info=True)
            progress_tracker.fail_job(str(e))
        finally:
            progress_tracker.ensure_finished()


def run_analyze_job_bg(stages: List[str], device: str, retry_failed: bool):
    from app.services.analyze_job import run_analyze_job
    with SyncSessionLocal() as session:
        try:
            results = run_analyze_job(session, stages=stages, device=device, retry_failed=retry_failed,
                                      progress_tracker=progress_tracker)
            session.commit()
            device_used = next((r["device_used"] for r in results.values()
                                if isinstance(r, dict) and r.get("device_used")), "cpu")
            progress_tracker.complete_analyze(results=results, device_used=device_used)
        except InterruptedError:
            session.rollback()
            logger.info("分析被使用者取消")
        except Exception as e:
            session.rollback()
            logger.error(f"分析發生錯誤：{e}", exc_info=True)
            progress_tracker.fail_job(str(e))
        finally:
            progress_tracker.ensure_finished()


def run_rebuild_thumbs_bg():
    from app.services.basic_stage import run_rebuild_thumbs_only
    with SyncSessionLocal() as session:
        try:
            result = run_rebuild_thumbs_only(session, progress_tracker=progress_tracker)
            session.commit()
            progress_tracker.complete_rebuild_thumbs(done=result.get("done", 0))
        except Exception as e:
            session.rollback()
            logger.error(f"縮圖補算錯誤：{e}", exc_info=True)
            progress_tracker.fail_job(str(e))
        finally:
            progress_tracker.ensure_finished()


def _evaluate_body() -> dict:
    from app.services.rules_engine import rules_engine
    with SyncSessionLocal() as session:
        try:
            counts = rules_engine.evaluate_all_rules(session)
            n = session.query(func.count(func.distinct(Candidate.asset_id))).filter(
                Candidate.is_keeper == False).scalar() or 0  # noqa: E712
            session.commit()
            progress_tracker.complete_evaluate(new_candidates=n)
            return counts
        except Exception as e:
            session.rollback()
            logger.error(f"評估錯誤：{e}", exc_info=True)
            progress_tracker.fail_job(str(e))
            raise
        finally:
            progress_tracker.ensure_finished()


def run_evaluate_bg():
    try:
        _evaluate_body()
    except Exception:
        pass


def run_evaluate_now() -> dict:
    """同步評估（設定頁儲存、重設時使用）；有其他任務進行中則回傳 409。"""
    if not progress_tracker.try_start_evaluate():
        raise HTTPException(status_code=409, detail=BUSY)
    try:
        return _evaluate_body()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"規則評估失敗：{e}")


# ─── API 端點 ──────────────────────────────────────────────────────────────────
@router.post("/scan")
def trigger_scan(payload: ScanRequest, background_tasks: BackgroundTasks):
    if payload.mode not in ("full", "new_only"):
        raise HTTPException(status_code=422, detail=f"不合法的掃描模式：{payload.mode!r}，合法值：full, new_only")
    if not progress_tracker.try_start_scan(payload.mode):
        raise HTTPException(status_code=400, detail=BUSY)
    background_tasks.add_task(run_scan_job_bg, payload.mode, payload.root_id)
    return {"status": "ok", "message": f"已啟動掃描任務（mode={payload.mode}）"}


@router.post("/analyze")
def trigger_analyze(payload: AnalyzeRequest, background_tasks: BackgroundTasks):
    valid_stages = {"hash", "basic", "clip", "video"}
    invalid = [s for s in payload.stages if s not in valid_stages]
    if invalid:
        raise HTTPException(status_code=422, detail=f"不合法的分析階段：{invalid}")
    if payload.device not in ("auto", "cuda", "cpu"):
        raise HTTPException(status_code=422, detail=f"不合法的裝置選擇：{payload.device!r}")
    if not payload.stages:
        raise HTTPException(status_code=422, detail="至少需指定一個分析階段")
    if not progress_tracker.try_start_analyze(payload.stages, payload.device):
        raise HTTPException(status_code=400, detail=BUSY)
    background_tasks.add_task(run_analyze_job_bg, payload.stages, payload.device, payload.retry_failed)
    return {"status": "ok", "message": f"已啟動分析任務（stages={payload.stages}, device={payload.device}）"}


@router.post("/rebuild_thumbs")
def trigger_rebuild_thumbs(background_tasks: BackgroundTasks):
    if not progress_tracker.try_start_rebuild_thumbs():
        raise HTTPException(status_code=400, detail=BUSY)
    background_tasks.add_task(run_rebuild_thumbs_bg)
    return {"status": "ok", "message": "已啟動縮圖補算任務"}


@router.post("/evaluate")
def trigger_evaluate(background_tasks: BackgroundTasks):
    if not progress_tracker.try_start_evaluate():
        raise HTTPException(status_code=400, detail=BUSY)
    background_tasks.add_task(run_evaluate_bg)
    return {"status": "ok", "message": "已啟動規則評估任務"}


@router.get("/status")
def get_job_status():
    return progress_tracker.get_state()


@router.get("/events")
async def job_events(request: Request):
    """Server-Sent Events (SSE) 即時推送任務進度。"""
    async def event_generator():
        async for state in progress_tracker.subscribe():
            if await request.is_disconnected():
                break
            yield f"data: {json.dumps(state, ensure_ascii=False)}\n\n"
    return StreamingResponse(event_generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                                      "X-Accel-Buffering": "no"})


@router.post("/cancel")
def cancel_job():
    progress_tracker.request_cancel()
    return {"status": "ok", "message": "已發送取消請求"}
