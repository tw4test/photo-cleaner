from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import init_db
from app.api import roots, scan, thumbnails, dashboard, candidates, process, history, settings as app_settings
from app.api import jobs, analysis, system
from app.services.cuda_manager import cuda_manager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize SQLite database and WAL mode on startup
    init_db()
    yield

app = FastAPI(
    title=settings.APP_NAME,
    description="本地相片瘦身工具後端 API（綁定 127.0.0.1）",
    version="0.3.0",
    lifespan=lifespan,
)

# CORS setup for local web frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API Routers
app.include_router(roots.router)
app.include_router(scan.router)
app.include_router(thumbnails.router)
app.include_router(dashboard.router)
app.include_router(candidates.router)
app.include_router(process.router)
app.include_router(history.router)
app.include_router(app_settings.router)
# New routers (post scan/analysis split)
app.include_router(jobs.router)
app.include_router(analysis.router)
app.include_router(system.router)

@app.get("/api/health")
async def health_check():
    gpu_info = cuda_manager.get_cuda_status()
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "version": "0.3.0",
        "host": settings.HOST,
        "port": settings.PORT,
        "gpu_mode": "mock_stub",
        "gpu": {
            "name": gpu_info.get("gpu_name"),
            "has_nvidia": gpu_info.get("has_nvidia_gpu"),
            "cuda_available": gpu_info.get("torch_cuda_available"),
            "torch_version": gpu_info.get("torch_version"),
        }
    }
