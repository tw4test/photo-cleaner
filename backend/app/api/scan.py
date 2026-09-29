from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime
from typing import Dict, Any

from app.core.database import get_db, SyncSessionLocal
from app.models.schema import Root, Asset, File, Feature, VideoInfo
from app.services.scanner import scan_directory
from app.services.pairing import pairing_engine
from app.services.thumbnail import thumbnail_service
from app.services.feature_pipeline import feature_pipeline

router = APIRouter(prefix="/api/scan", tags=["scan"])

def run_sync_scan(root_id: int):
    """Background task to perform scanning and fake feature extraction."""
    with SyncSessionLocal() as session:
        root = session.get(Root, root_id)
        if not root:
            return

        # 1. 遍歷目錄
        scanned = list(scan_directory(root.path, root.recursive, root.exclude_patterns))

        # 2. 配對
        paired_assets = pairing_engine.pair_files(scanned)

        # 3. 寫入資料庫
        for item in paired_assets:
            primary_file = item["primary_file"]
            paired_files = item["paired_files"]

            # 檢查是否已存在
            existing_asset = session.query(Asset).filter(Asset.primary_path == primary_file["path"]).first()
            if existing_asset:
                continue

            asset = Asset(
                root_id=root.id,
                media_type=item["media_type"],
                primary_path=primary_file["path"],
                size_total=item["size_total"],
                paired_paths=[p["path"] for p in paired_files],
                status="analysed",
                created_at=datetime.utcnow()
            )
            session.add(asset)
            session.flush()

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

            # 生成縮圖
            thumbnail_service.generate_thumbnail(primary_file["path"])

            # 提取特徵 (包含假 blur_score / clip_embedding)
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

        root.last_scan_at = datetime.utcnow()
        session.commit()

@router.post("/{root_id}")
async def trigger_scan(
    root_id: int, 
    background_tasks: BackgroundTasks, 
    db: AsyncSession = Depends(get_db)
):
    root = await db.get(Root, root_id)
    if not root:
        raise HTTPException(status_code=404, detail="找不到指定的路徑")

    background_tasks.add_task(run_sync_scan, root_id)
    return {"status": "ok", "message": f"已在背景啟動目錄掃描：{root.path}"}
