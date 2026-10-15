from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.core.config import settings
from app.core.database import init_db
from app.api import roots, scan, thumbnails, dashboard, candidates, process, history, settings as app_settings
from app.api import jobs, analysis, system
from app.services.cuda_manager import cuda_manager

ALLOWED_ORIGINS = [
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:8000",
    "http://localhost:8000",
]
# testserver：FastAPI TestClient 使用的 Host
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title=settings.APP_NAME,
    description="本地相片瘦身工具後端 API（綁定 127.0.0.1）",
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

# 防 DNS rebinding：只接受本機 Host
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def origin_guard(request: Request, call_next):
    """
    防 CSRF：瀏覽器中其他網站送來的寫入請求一律拒絕。
    CORS 只擋「讀取回應」，擋不住沒有 body 的 POST，因此需要這一層。
    """
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and origin not in ALLOWED_ORIGINS:
            return JSONResponse({"detail": "forbidden origin"}, status_code=403)
        site = request.headers.get("sec-fetch-site")
        if not origin and site and site not in ("same-origin", "same-site", "none"):
            return JSONResponse({"detail": "forbidden cross-site request"}, status_code=403)
    return await call_next(request)


app.include_router(roots.router)
app.include_router(scan.router)
app.include_router(thumbnails.router)
app.include_router(dashboard.router)
app.include_router(candidates.router)
app.include_router(process.router)
app.include_router(history.router)
app.include_router(app_settings.router)
app.include_router(jobs.router)
app.include_router(analysis.router)
app.include_router(system.router)


@app.get("/api/health")
def health_check():
    gpu_info = cuda_manager.get_cuda_status()
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "host": settings.HOST,
        "port": settings.PORT,
        "gpu_mode": "cuda" if gpu_info.get("torch_cuda_available") else "cpu",
        "gpu": {
            "name": gpu_info.get("gpu_name"),
            "has_nvidia": gpu_info.get("has_nvidia_gpu"),
            "cuda_available": gpu_info.get("torch_cuda_available"),
            "torch_version": gpu_info.get("torch_version"),
        },
    }
