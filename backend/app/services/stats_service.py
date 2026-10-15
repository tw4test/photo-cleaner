"""
stats_service.py
================
Single Source of Truth 統計計算服務。
- Total Active = Photo + Raw + Video
- Stage Total = Pending + Done + (Not Needed / Stale) + Failed
- missing/deleted 一律排除。

v0.5：
- 可釋放容量改為「不重複資產」的總大小（舊版 SUM(DISTINCT size) 會把同大小的完全重複只算一次）。
- 累計已清理容量改為依資產計算（舊版依 actions_log 逐檔 join，配對檔會重複計算）。
- 讀取 clip_model 設定時正確解析 JSON。
"""
from typing import Dict, Any, Optional

from sqlalchemy.orm import Session
from sqlalchemy import select, func, distinct, and_, or_

from app.core.defaults import get_setting
from app.models.schema import (
    Asset, File, Feature, ClipFeature, VideoInfo, Candidate, ActionLog, AnalysisFailure
)
from app.models.enums import MediaType, CandidateState, ActionType


class StatsService:
    @staticmethod
    def get_dashboard_stats(session: Session) -> Dict[str, Any]:
        active_filter = Asset.status.notin_(["deleted", "missing"])
        total_assets = session.query(func.count(Asset.id)).filter(active_filter).scalar() or 0
        photo_count = session.query(func.count(Asset.id)).filter(active_filter, Asset.media_type == MediaType.PHOTO.value).scalar() or 0
        raw_count = session.query(func.count(Asset.id)).filter(active_filter, Asset.media_type == MediaType.RAW.value).scalar() or 0
        video_count = session.query(func.count(Asset.id)).filter(active_filter, Asset.media_type == MediaType.VIDEO.value).scalar() or 0
        total_size = session.query(func.sum(Asset.size_total)).filter(active_filter).scalar() or 0

        def distinct_assets(*conds):
            return (
                session.query(func.count(distinct(Candidate.asset_id)))
                .join(Asset, Candidate.asset_id == Asset.id)
                .filter(Candidate.is_keeper == False, active_filter, *conds)  # noqa: E712
                .scalar() or 0
            )

        open_state = Candidate.state == CandidateState.CANDIDATE.value
        candidates_count = distinct_assets(open_state)
        per_rule = {
            name: distinct_assets(open_state, Candidate.rule.in_(rules))
            for name, rules in {
                "screenshot": ["screenshot"], "document": ["document"], "meme": ["meme"],
                "mistouch": ["mistouch"], "exact_dup": ["exact_dup"], "near_dup": ["near_dup"],
                "burst": ["burst"], "blur": ["blur"], "exposure": ["over_exposure", "under_exposure"],
            }.items()
        }

        releasable_ids = (
            select(Candidate.asset_id)
            .join(Asset, Candidate.asset_id == Asset.id)
            .where(
                Candidate.is_keeper == False,  # noqa: E712
                Candidate.state.in_([CandidateState.CANDIDATE.value, CandidateState.QUEUED.value]),
                active_filter,
            )
            .distinct()
        )
        releasable_size = session.query(func.sum(Asset.size_total)).filter(Asset.id.in_(releasable_ids)).scalar() or 0

        deleted_ids = select(ActionLog.asset_id).where(ActionLog.action == ActionType.DELETE.value).distinct()
        deleted_size = session.query(func.sum(Asset.size_total)).filter(Asset.id.in_(deleted_ids)).scalar() or 0
        deleted_batches = session.query(func.count(distinct(ActionLog.batch_id))).scalar() or 0

        return {
            "assets": {
                "total": total_assets,
                "photo_count": photo_count,
                "raw_count": raw_count,
                "video_count": video_count,
                "total_size_bytes": total_size,
            },
            "candidates": {"total": candidates_count, **per_rule, "releasable_size_bytes": releasable_size},
            "history": {"deleted_size_bytes": deleted_size, "deleted_batches": deleted_batches},
        }

    @staticmethod
    def get_analysis_stage_stats(
        session: Session,
        clip_model: Optional[str] = None,
        clip_pretrained: Optional[str] = None,
    ) -> Dict[str, Dict[str, int]]:
        if clip_model is None:
            clip_model = get_setting(session, "clip_model")
        if clip_pretrained is None:
            clip_pretrained = get_setting(session, "clip_pretrained")
        active_filter = Asset.status.notin_(["deleted", "missing"])

        # 1. Hash
        hash_map = dict(
            session.query(File.hash_state, func.count(File.id))
            .join(Asset, File.asset_id == Asset.id)
            .filter(File.role == "primary", active_filter)
            .group_by(File.hash_state)
            .all()
        )
        hash_stats = {
            "pending": hash_map.get("pending", 0),
            "done": hash_map.get("done", 0),
            "not_needed": hash_map.get("not_needed", 0),
            "failed": hash_map.get("error", 0),
        }
        hash_stats["total"] = sum(hash_stats.values())

        # 2. Basic
        photo_raw_filter = and_(active_filter, Asset.media_type.in_(["photo", "raw"]))
        total_photo_raw = session.query(func.count(Asset.id)).filter(photo_raw_filter).scalar() or 0
        basic_done = (
            session.query(func.count(Feature.asset_id))
            .join(Asset, Feature.asset_id == Asset.id)
            .filter(Feature.decode_status == "ok", Feature.analyzed_at.isnot(None), photo_raw_filter)
            .scalar() or 0
        )
        basic_failed = (
            session.query(func.count(Feature.asset_id))
            .join(Asset, Feature.asset_id == Asset.id)
            .filter(Feature.decode_status == "error", photo_raw_filter)
            .scalar() or 0
        )
        basic_stats = {
            "total": total_photo_raw,
            "pending": max(total_photo_raw - basic_done - basic_failed, 0),
            "done": basic_done,
            "failed": basic_failed,
        }

        # 3. CLIP
        current = and_(ClipFeature.model == clip_model, ClipFeature.pretrained == clip_pretrained)
        clip_done = (
            session.query(func.count(ClipFeature.asset_id))
            .join(Asset, ClipFeature.asset_id == Asset.id)
            .filter(current, photo_raw_filter).scalar() or 0
        )
        clip_stale = (
            session.query(func.count(ClipFeature.asset_id))
            .join(Asset, ClipFeature.asset_id == Asset.id)
            .filter(or_(ClipFeature.model != clip_model, ClipFeature.pretrained != clip_pretrained),
                    photo_raw_filter).scalar() or 0
        )
        any_clip = select(ClipFeature.asset_id)
        clip_failed = (
            session.query(func.count(distinct(AnalysisFailure.asset_id)))
            .join(Asset, AnalysisFailure.asset_id == Asset.id)
            .filter(AnalysisFailure.stage == "clip", photo_raw_filter,
                    AnalysisFailure.asset_id.notin_(any_clip))
            .scalar() or 0
        )
        clip_stats = {
            "total": total_photo_raw,
            "pending": max(total_photo_raw - clip_done - clip_stale - clip_failed, 0),
            "done": clip_done,
            "stale": clip_stale,
            "failed": clip_failed,
        }

        # 4. Video
        video_filter = and_(active_filter, Asset.media_type == "video")
        total_video = session.query(func.count(Asset.id)).filter(video_filter).scalar() or 0
        video_done = (
            session.query(func.count(VideoInfo.asset_id))
            .join(Asset, VideoInfo.asset_id == Asset.id)
            .filter(video_filter).scalar() or 0
        )
        video_failed = (
            session.query(func.count(distinct(AnalysisFailure.asset_id)))
            .join(Asset, AnalysisFailure.asset_id == Asset.id)
            .filter(AnalysisFailure.stage == "video", video_filter,
                    AnalysisFailure.asset_id.notin_(select(VideoInfo.asset_id)))
            .scalar() or 0
        )
        video_stats = {
            "total": total_video,
            "pending": max(total_video - video_done - video_failed, 0),
            "done": video_done,
            "failed": video_failed,
        }
        return {"hash": hash_stats, "basic": basic_stats, "clip": clip_stats, "video": video_stats}


stats_service = StatsService()
get_dashboard_stats = stats_service.get_dashboard_stats
get_analysis_stage_stats = stats_service.get_analysis_stage_stats
