from fastapi import APIRouter
from typing import Dict, Any

from app.core.database import SyncSessionLocal
from app.services.stats_service import stats_service

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

@router.get("/stats")
async def get_dashboard_stats() -> Dict[str, Any]:
    with SyncSessionLocal() as session:
        return stats_service.get_dashboard_stats(session)
