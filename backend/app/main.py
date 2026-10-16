from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.core.config import settings
from app.core.database import init_db
from app.api import roots, scan, thumbnails, dashboard, candidates, process, history, settings as app_settings
from app.api import jobs, analysis, system, workflow, videos
from app.services.cuda_manager import cuda_manager

ALLOWED_ORIGINS = ["http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:8000", "http://localhost:8000"]
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from app.services import video_transcode
    video_transcode.cleanup_partials()      # 上次異常結束留下的轉檔半成品
    yield
    video_transcode.shutdown()              # 結束時殺掉還在跑的 ffmpeg，不留孤兒行程


app = FastAPI(title=settings.APP_NAME, description="相片清理助手後端 API（綁定 127.0.0.1）",
              version=settings.APP_VERSION, lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_credentials=True, allow_methods=["*"],
                   allow_headers=["*"])


@app.middleware("http")
async def origin_guard(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and origin not in ALLOWED_ORIGINS:
            return JSONResponse({"detail": "forbidden origin"}, status_code=403)
        site = request.headers.get("sec-fetch-site")
        if not origin and site and site not in ("same-origin", "same-site", "none"):
            return JSONResponse({"detail": "forbidden cross-site request"}, status_code=403)
    return await call_next(request)


for r in (roots, scan, thumbnails, dashboard, candidates, process, history, app_settings, jobs, analysis, system, workflow, videos):
    app.include_router(r.router)


@app.get("/api/health")
def health_check():
    g = cuda_manager.get_cuda_status()
    return {"status": "ok", "app": settings.APP_NAME, "version": settings.APP_VERSION, "host": settings.HOST,
            "port": settings.PORT, "gpu_mode": "cuda" if g.get("torch_cuda_available") else "cpu",
            "gpu": {"name": g.get("gpu_name"), "has_nvidia": g.get("has_nvidia_gpu"),
                    "cuda_available": g.get("torch_cuda_available"), "torch_version": g.get("torch_version")}}
