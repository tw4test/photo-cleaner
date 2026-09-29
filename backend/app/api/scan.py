import json
import asyncio
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import datetime
from typing import Dict, Any, Optional

from app.core.database import get_db, SyncSessionLocal
from app.core.progress import progress_tracker
from app.models.schema import Root, Asset, File, Feature, VideoInfo, Candidate
from app.services.scanner import scan_directory
from app.services.pairing import pairing_engine
from app.services.thumbnail import thumbnail_service
from app.services.feature_pipeline import feature_pipeline
from app.services.rules_engine import rules_engine

router = APIRouter(prefix="/api/scan", tags=["scan"])

def run_sync_scan(root_id: Optional[int] = None):
    """Background task to perform scanning, feature extraction, and candidate generation with live progress."""
    progress_tracker.start_scan()
    
    with SyncSessionLocal() as session:
        try:
            if root_id is not None:
                roots = [session.get(Root, root_id)]
            else:
                roots = session.query(Root).filter(Root.available == True).order_by(Root.priority.asc()).all()

            # 1. 遍歷目錄統計檔案
            all_scanned_files = []
            for root in roots:
                if not root:
                    continue
                files = list(scan_directory(root.path, root.recursive, root.exclude_patterns))
                for f in files:
                    f["root_id"] = root.id
                all_scanned_files.extend(files)

            total_discovered = len(all_scanned_files)
            progress_tracker.set_discovered_total(total_discovered)

            # 2. 配對
            paired_assets = pairing_engine.pair_files(all_scanned_files)

            # 3. 逐一處理特徵分析與縮圖
            processed_files_count = 0
            new_assets_count = 0

            for idx, item in enumerate(paired_assets):
                if progress_tracker.is_cancel_requested():
                    break

                primary_file = item["primary_file"]
                paired_files = item["paired_files"]
                root_id_for_asset = primary_file.get("root_id", roots[0].id if roots else 1)

                # 檢查是否已存在
                existing_asset = session.query(Asset).filter(Asset.primary_path == primary_file["path"]).first()
                if existing_asset:
                    processed_files_count += 1 + len(paired_files)
                    progress_tracker.update_progress(
                        processed=processed_files_count,
                        current_file=primary_file["path"],
                        new_assets=new_assets_count
                    )
                    continue

                asset = Asset(
                    root_id=root_id_for_asset,
                    media_type=item["media_type"],
                    primary_path=primary_file["path"],
                    size_total=item["size_total"],
                    paired_paths=[p["path"] for p in paired_files],
                    status="analysed",
                    created_at=datetime.utcnow()
                )
                session.add(asset)
                session.flush()
                new_assets_count += 1

                # 寫入主檔案記錄
                primary_record = File(
                    asset_id=asset.id,
                    path=primary_file["path"],
                    size=primary_file["size"],
                    mtime=primary_file["mtime"],
                    format=primary_file["ext"].lstrip('.'),
                    role="primary"
                )
                session.add(primary_record)

                # 寫入附屬配對檔案記錄
                for p in paired_files:
                    pair_record = File(
                        asset_id=asset.id,
                        path=p["path"],
                        size=p["size"],
                        mtime=p["mtime"],
                        format=p["ext"].lstrip('.'),
                        role="pair"
                    )
                    session.add(pair_record)

                # 生成縮圖快取
                thumbnail_service.generate_thumbnail(primary_file["path"])

                # 提取特徵 (包含 AI 語義分類)
                feats = feature_pipeline.extract_features(
                    primary_file["path"],
                    is_raw=(item["media_type"] == "raw"),
                    is_video=(item["media_type"] == "video")
                )
                primary_record.sha256 = feats["sha256"]

                if item["media_type"] == "video":
                    v_info = VideoInfo(
                        asset_id=asset.id,
                        duration=None,
                        width=None,
                        height=None,
                        codec=None
                    )
                    session.add(v_info)
                else:
                    feat_record = Feature(
                        asset_id=asset.id,
                        phash=feats["phash"],
                        blur_score=feats["blur_score"],
                        brightness=feats["brightness"],
                        width=feats["width"],
                        height=feats["height"],
                        taken_at=feats["taken_at"],
                        exif=feats["exif"],
                        clip_embedding=feats["clip_embedding"],
                        zs_scores=feats["zs_scores"],
                        decode_status=feats["decode_status"]
                    )
                    session.add(feat_record)

                processed_files_count += 1 + len(paired_files)
                progress_tracker.update_progress(
                    processed=processed_files_count,
                    current_file=primary_file["path"],
                    new_assets=new_assets_count
                )

            for r in roots:
                if r:
                    r.last_scan_at = datetime.utcnow()
            session.commit()

            # 4. 執行規則引擎產出候選
            progress_tracker.set_evaluating_rules()
            rules_engine.evaluate_all_rules(session)

            # 統計產出候選數 (計算不重複之待清理相片資產總數)
            candidates_count = session.query(
                func.count(func.distinct(Candidate.asset_id))
            ).filter(Candidate.is_keeper == False).scalar() or 0

            # 5. 完成廣播
            progress_tracker.complete_scan(
                new_assets=new_assets_count,
                new_candidates=candidates_count
            )
        except Exception as e:
            session.rollback()
            progress_tracker.fail_scan(str(e))

@router.get("/status")
async def get_scan_status():
    """取得當前掃描進度狀態"""
    return progress_tracker.get_state()

@router.get("/events")
async def scan_events(request: Request):
    """Server-Sent Events (SSE) 即時推送掃描進度"""
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
        }
    )

@router.post("/cancel")
async def cancel_scan():
    """取消當前正在進行的掃描任務"""
    progress_tracker.request_cancel()
    return {"status": "ok", "message": "已發送取消掃描請求"}

@router.post("/all")
async def trigger_scan_all(background_tasks: BackgroundTasks):
    if progress_tracker.state.is_scanning:
        raise HTTPException(status_code=400, detail="目前已有掃描任務進行中")

    background_tasks.add_task(run_sync_scan, None)
    return {"status": "ok", "message": "已在背景啟動全庫掃描與規則分析"}

@router.post("/{root_id}")
async def trigger_scan(
    root_id: int, 
    background_tasks: BackgroundTasks, 
    db: AsyncSession = Depends(get_db)
):
    if progress_tracker.state.is_scanning:
        raise HTTPException(status_code=400, detail="目前已有掃描任務進行中")

    root = await db.get(Root, root_id)
    if not root:
        raise HTTPException(status_code=404, detail="找不到指定的路徑")

    background_tasks.add_task(run_sync_scan, root_id)
    return {"status": "ok", "message": f"已在背景啟動目錄掃描：{root.path}"}
