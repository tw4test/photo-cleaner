import json
import logging
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.services.cuda_manager import cuda_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/system", tags=["system"])


class InstallCudaRequest(BaseModel):
    variant: str = "cu121"  # cu121 | cu118


@router.get("/gpu-status")
async def get_gpu_status(refresh: bool = False):
    """
    查詢本機 NVIDIA GPU 狀態、PyTorch CUDA 支援資訊與安裝進度。
    """
    return cuda_manager.get_cuda_status(force_refresh=refresh)


@router.post("/install-cuda")
async def trigger_install_cuda(payload: InstallCudaRequest = InstallCudaRequest()):
    """
    觸發在背景下載並安裝 CUDA 版 PyTorch。
    """
    res = cuda_manager.start_cuda_install(variant=payload.variant)
    if res.get("status") == "busy":
        raise HTTPException(status_code=400, detail="已有安裝任務正在進行中")
    return res


@router.post("/install-cuda/cancel")
async def cancel_install_cuda():
    """
    取消正在進行中的 CUDA 版 PyTorch 安裝任務。
    """
    cuda_manager.cancel_cuda_install()
    return {"status": "ok", "message": "已發送取消請求"}


@router.get("/install-cuda/events")
async def install_cuda_events(request: Request):
    """
    Server-Sent Events (SSE) 即時推送 PyTorch CUDA 下載與安裝進度、即時日誌流。
    """
    async def event_generator():
        async for state in cuda_manager.subscribe():
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
