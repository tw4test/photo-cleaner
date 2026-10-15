from fastapi import APIRouter

from app.core.database import SyncSessionLocal
from app.services.workflow_service import workflow_status

router = APIRouter(prefix="/api/workflow", tags=["workflow"])


@router.get("/status")
def get_workflow_status():
    """首頁 / 資料庫頁使用的流程狀態：掃描 → 分析 → 評估 → 審核 → 刪除。"""
    with SyncSessionLocal() as session:
        return workflow_status(session)
