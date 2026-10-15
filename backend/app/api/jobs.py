"""Jobs API：掃描、分析、補算縮圖、評估規則（v0.5）。"""
import json
import logging
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.database import SyncSessionLocal
from app.core.progress import progress_tracker

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/jobs", tags=["jobs"])

BUSY = "目前已有任務進行中，請等待完成後再試"


class ScanRequest(BaseModel):
    mode: str = "full"
    root_id: Optional[int] = None


class AnalyzeRequest(BaseModel):
    stages: List[str] = ["hash", "basic", "clip", "video"]
    device: str = "auto"
    retry_failed: bool = False


def run_scan_job_bg(mode: str, root_id: Optional[int]):
    from app.services.scan_job import run_scan_job
    with SyncSessionLocal() as session:
        try:
            s = run_scan_job(session, root_ids=[root_id] if root_id is not None else None, mode=mode,
                             progress_tracker=progress_tracker)
            session.commit()
            progress_tracker.complete_scan(summary={"added": s.added, "modified": s.modified,
                                                    "missing": s.missing, "unchanged": s.unchanged})
        except InterruptedError:
            session.rollback()
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
            r = run_rebuild_thumbs_only(session, progress_tracker=progress_tracker)
            session.commit()
            progress_tracker.complete_rebuild_thumbs(done=r.get("done", 0))
        except Exception as e:
            session.rollback()
            progress_tracker.fail_job(str(e))
        finally:
            progress_tracker.ensure_finished()


def _evaluate_in_session(session, force: bool) -> dict:
    """在已開始的任務中執行評估（無變更時略過），回傳 {skipped, added, removed, total, counts}。"""
    from app.services import evaluation_state as es
    from app.services.rules_engine import rules_engine
    if not force and es.last_evaluated_at(session) and not es.is_stale(session):
        result = es.record(session, es.candidate_set(session), {}, skipped=True)
    else:
        before = es.candidate_set(session)
        counts = rules_engine.evaluate_all_rules(session)
        result = es.record(session, before, counts)
    session.commit()
    return result


def _evaluate_body(force: bool = False) -> dict:
    """呼叫前 progress_tracker 必須已 try_start_evaluate 成功。"""
    with SyncSessionLocal() as session:
        try:
            result = _evaluate_in_session(session, force)
            progress_tracker.complete_evaluate(new_candidates=result["total"], result=result)
            return result
        except Exception as e:
            session.rollback()
            logger.error(f"評估錯誤：{e}", exc_info=True)
            progress_tracker.fail_job(str(e))
            raise
        finally:
            progress_tracker.ensure_finished()


def run_evaluate_bg(force: bool = False):
    try:
        _evaluate_body(force)
    except Exception:
        pass


def start_evaluate_or_409():
    if not progress_tracker.try_start_evaluate():
        raise HTTPException(status_code=409, detail=BUSY)


def run_evaluate_now(force: bool = True, already_started: bool = False) -> dict:
    """同步評估（設定頁儲存、重設時使用）；有其他任務進行中則回傳 409。"""
    if not already_started:
        start_evaluate_or_409()
    try:
        return _evaluate_body(force).get("counts", {})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"規則評估失敗：{e}")


class EvaluateRequest(BaseModel):
    force: bool = False


class PipelineRequest(BaseModel):
    mode: str = "full"
    device: str = "auto"


def run_pipeline_bg(mode: str, device: str):
    """一鍵流水線：掃描 → 分析（全部階段）→ 評估，單一任務、可在掃描與分析時取消。"""
    from app.services.analyze_job import run_analyze_job
    from app.services.scan_job import run_scan_job
    with SyncSessionLocal() as session:
        try:
            s = run_scan_job(session, mode=mode, progress_tracker=progress_tracker)
            session.commit()
            summary = {"added": s.added, "modified": s.modified, "missing": s.missing, "unchanged": s.unchanged}
            if progress_tracker.is_cancel_requested():
                raise InterruptedError("使用者已取消")
            progress_tracker.set_stage_info(scan_summary=summary)
            progress_tracker.set_pipeline_step("analyze", "一鍵流水線：正在分析...")
            stats = run_analyze_job(session, device=device, progress_tracker=progress_tracker)
            session.commit()
            if progress_tracker.is_cancel_requested():
                raise InterruptedError("使用者已取消")
            progress_tracker.set_pipeline_step("evaluate", "一鍵流水線：正在評估規則...")
            progress_tracker.set_evaluating_rules()
            result = _evaluate_in_session(session, force=False)
            progress_tracker.complete_pipeline(summary, stats, result["total"], result)
        except InterruptedError:
            session.rollback()
        except Exception as e:
            session.rollback()
            logger.error(f"流水線發生錯誤：{e}", exc_info=True)
            progress_tracker.fail_job(str(e))
        finally:
            progress_tracker.ensure_finished()


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
    invalid = [s for s in payload.stages if s not in {"hash", "basic", "clip", "video"}]
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
def trigger_evaluate(background_tasks: BackgroundTasks, payload: Optional[EvaluateRequest] = None):
    """評估規則。預設在輸入沒有變更時略過；force=true 一律重新評估。"""
    force = bool(payload and payload.force)
    if not progress_tracker.try_start_evaluate():
        raise HTTPException(status_code=400, detail=BUSY)
    background_tasks.add_task(run_evaluate_bg, force)
    return {"status": "ok", "message": "已啟動規則評估任務"}


@router.post("/pipeline")
def trigger_pipeline(payload: PipelineRequest, background_tasks: BackgroundTasks):
    if payload.mode not in ("full", "new_only"):
        raise HTTPException(status_code=422, detail=f"不合法的掃描模式：{payload.mode!r}")
    if payload.device not in ("auto", "cuda", "cpu"):
        raise HTTPException(status_code=422, detail=f"不合法的裝置選擇：{payload.device!r}")
    if not progress_tracker.try_start_pipeline():
        raise HTTPException(status_code=400, detail=BUSY)
    background_tasks.add_task(run_pipeline_bg, payload.mode, payload.device)
    return {"status": "ok", "message": "已啟動一鍵流水線（掃描 → 分析 → 評估）"}


@router.get("/status")
def get_job_status():
    return progress_tracker.get_state()


@router.get("/events")
async def job_events(request: Request):
    async def gen():
        async for state in progress_tracker.subscribe():
            if await request.is_disconnected():
                break
            yield f"data: {json.dumps(state, ensure_ascii=False)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})


@router.post("/cancel")
def cancel_job():
    if progress_tracker.state.is_running and not progress_tracker.state.cancellable:
        return {"status": "ignored", "message": "規則評估無法中途取消，通常數秒內完成"}
    progress_tracker.request_cancel()
    return {"status": "ok", "message": "已發送取消請求"}
