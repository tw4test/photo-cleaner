from typing import Any, Dict

from fastapi import APIRouter

from app.core.database import SyncSessionLocal
from app.services.stats_service import stats_service

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats")
def get_dashboard_stats() -> Dict[str, Any]:
    # 同步查詢放在 threadpool 執行（def 而非 async def），不阻塞 event loop
    with SyncSessionLocal() as session:
        return stats_service.get_dashboard_stats(session)
