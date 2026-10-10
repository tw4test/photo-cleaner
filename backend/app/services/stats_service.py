"""
stats_service.py
================
Single Source of Truth (單一真相來源) 統計計算服務。

職責：
1. 為 Dashboard、Analysis 工作台、審核工作台提供統一無矛盾的數據統計。
2. 嚴格維護數學恆等式：
   - Total Active = Photo + Raw + Video
   - Stage Total = Pending + Done + (Not Needed / Stale) + Failed
3. 確保 missing/deleted 狀態在全域有一致的過濾語義。
"""

from typing import Dict, Any, Tuple, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select, func, distinct, and_, or_

from app.models.schema import (
    Asset, File, Feature, ClipFeature, VideoInfo, Candidate, ActionLog, AnalysisFailure, Setting
)
from app.models.enums import MediaType, CandidateState, ActionType


class StatsService:
    @staticmethod
    def get_dashboard_stats(session: Session) -> Dict[str, Any]:
        """
        計算儀表板核心統計。
        嚴格定義：
        - 活躍資產（Active）：status 既非 deleted 亦非 missing。
        - 總資產數 = 照片 + RAW + 影片。
        """
        # 1. 資產總數統計（過濾 deleted 與 missing，真實反映當前可用庫藏）
        active_filter = Asset.status.notin_(["deleted", "missing"])

        total_assets = session.query(func.count(Asset.id)).filter(active_filter).scalar() or 0
        photo_count = session.query(func.count(Asset.id)).filter(active_filter, Asset.media_type == MediaType.PHOTO.value).scalar() or 0
        raw_count = session.query(func.count(Asset.id)).filter(active_filter, Asset.media_type == MediaType.RAW.value).scalar() or 0
        video_count = session.query(func.count(Asset.id)).filter(active_filter, Asset.media_type == MediaType.VIDEO.value).scalar() or 0
        total_size = session.query(func.sum(Asset.size_total)).filter(active_filter).scalar() or 0

        # 2. 候選清理項目統計 (依據不重複且建議刪除的 Asset 計算，排除建議保留的 Keeper)
        candidates_count_query = (
            session.query(func.count(distinct(Candidate.asset_id)))
            .join(Asset, Candidate.asset_id == Asset.id)
            .filter(
                Candidate.is_keeper == False,
                Candidate.state == CandidateState.CANDIDATE.value,
                active_filter
            )
        )
        candidates_count = candidates_count_query.scalar() or 0

        def get_rule_distinct_count(rule_name_or_list):
            if isinstance(rule_name_or_list, list):
                condition = Candidate.rule.in_(rule_name_or_list)
            else:
                condition = (Candidate.rule == rule_name_or_list)

            q = (
                session.query(func.count(distinct(Candidate.asset_id)))
                .join(Asset, Candidate.asset_id == Asset.id)
                .filter(
                    condition,
                    Candidate.is_keeper == False,
                    Candidate.state == CandidateState.CANDIDATE.value,
                    active_filter
                )
            )
            return q.scalar() or 0

        screenshot_count = get_rule_distinct_count("screenshot")
        document_count = get_rule_distinct_count("document")
        meme_count = get_rule_distinct_count("meme")
        mistouch_count = get_rule_distinct_count("mistouch")
        exact_dup_count = get_rule_distinct_count("exact_dup")
        near_dup_count = get_rule_distinct_count("near_dup")
        blur_count = get_rule_distinct_count("blur")
        exposure_count = get_rule_distinct_count(["over_exposure", "under_exposure"])

        # 3. 預計可釋放容量 (只計算非 Keeper 且處於 candidate 或 queued 的資產總容量)
        releasable_size_query = (
            session.query(func.sum(distinct(Asset.size_total)))
            .join(Candidate, Candidate.asset_id == Asset.id)
            .filter(
                Candidate.is_keeper == False,
                Candidate.state.in_([CandidateState.CANDIDATE.value, CandidateState.QUEUED.value]),
                active_filter
            )
        )
        releasable_size = releasable_size_query.scalar() or 0

        # 4. 歷史累計已刪除容量
        deleted_size_query = (
            session.query(func.sum(Asset.size_total))
            .join(ActionLog, ActionLog.asset_id == Asset.id)
            .filter(ActionLog.action == ActionType.DELETE.value)
        )
        deleted_size = deleted_size_query.scalar() or 0
        deleted_batches = session.query(func.count(distinct(ActionLog.batch_id))).scalar() or 0

        return {
            "assets": {
                "total": total_assets,
                "photo_count": photo_count,
                "raw_count": raw_count,
                "video_count": video_count,
                "total_size_bytes": total_size,
            },
            "candidates": {
                "total": candidates_count,
                "screenshot": screenshot_count,
                "document": document_count,
                "meme": meme_count,
                "mistouch": mistouch_count,
                "exact_dup": exact_dup_count,
                "near_dup": near_dup_count,
                "blur": blur_count,
                "exposure": exposure_count,
                "releasable_size_bytes": releasable_size,
            },
            "history": {
                "deleted_size_bytes": deleted_size,
                "deleted_batches": deleted_batches,
            }
        }

    @staticmethod
    def get_analysis_stage_stats(
        session: Session,
        clip_model: Optional[str] = None,
        clip_pretrained: Optional[str] = None,
    ) -> Dict[str, Dict[str, int]]:
        """
        精確計算各分析階段的狀態統計，徹底消除估算誤差。
        嚴格維持恆等式：
        Stage Total = Pending + Done + (Not Needed / Stale) + Failed
        """
        if clip_model is None:
            s_model = session.query(Setting).filter(Setting.key == "clip_model").first()
            clip_model = s_model.value if s_model else "ViT-B-16"
        if clip_pretrained is None:
            s_pre = session.query(Setting).filter(Setting.key == "clip_pretrained").first()
            clip_pretrained = s_pre.value if s_pre else "openai"

        active_filter = Asset.status.notin_(["deleted", "missing"])

        # ── 1. Hash 階段 ──────────────────────────────────────────
        # 統計所有活躍資產的主檔
        hash_rows = (
            session.query(File.hash_state, func.count(File.id))
            .join(Asset, File.asset_id == Asset.id)
            .filter(File.role == "primary", active_filter)
            .group_by(File.hash_state)
            .all()
        )
        hash_map = {row[0]: row[1] for row in hash_rows}

        hash_pending = hash_map.get("pending", 0)
        hash_done = hash_map.get("done", 0)
        hash_not_needed = hash_map.get("not_needed", 0)
        hash_failed = hash_map.get("error", 0)
        hash_total = hash_pending + hash_done + hash_not_needed + hash_failed

        hash_stats = {
            "total": hash_total,
            "pending": hash_pending,
            "done": hash_done,
            "not_needed": hash_not_needed,
            "failed": hash_failed,
        }

        # ── 2. Basic 階段 (適用對象：photo / raw) ─────────────────
        photo_raw_filter = and_(active_filter, Asset.media_type.in_(["photo", "raw"]))
        total_photo_raw = session.query(func.count(Asset.id)).filter(photo_raw_filter).scalar() or 0

        # Done: 特徵存在且解碼正常
        basic_done = (
            session.query(func.count(Feature.asset_id))
            .join(Asset, Feature.asset_id == Asset.id)
            .filter(Feature.decode_status == "ok", Feature.analyzed_at.isnot(None), photo_raw_filter)
            .scalar() or 0
        )

        # Failed: 有 basic 失敗記錄且未被修復
        # 若 Feature.decode_status == 'ok'，表示後來重試已成功，不應計入 failed
        done_asset_subquery = (
            session.query(Feature.asset_id)
            .filter(Feature.decode_status == "ok", Feature.analyzed_at.isnot(None))
            .subquery()
        )
        basic_failed = (
            session.query(func.count(distinct(AnalysisFailure.asset_id)))
            .join(Asset, AnalysisFailure.asset_id == Asset.id)
            .filter(
                AnalysisFailure.stage == "basic",
                photo_raw_filter,
                AnalysisFailure.asset_id.notin_(select(done_asset_subquery))
            )
            .scalar() or 0
        )

        basic_pending = max(total_photo_raw - basic_done - basic_failed, 0)
        basic_stats = {
            "total": total_photo_raw,
            "pending": basic_pending,
            "done": basic_done,
            "failed": basic_failed,
        }

        # ── 3. CLIP 階段 ──────────────────────────────────────────
        # Done: ClipFeature 存在且模型/權重相符
        clip_done = (
            session.query(func.count(ClipFeature.asset_id))
            .join(Asset, ClipFeature.asset_id == Asset.id)
            .filter(
                ClipFeature.model == clip_model,
                ClipFeature.pretrained == clip_pretrained,
                photo_raw_filter
            )
            .scalar() or 0
        )

        # Stale: ClipFeature 存在但模型/權重不相符
        clip_stale = (
            session.query(func.count(ClipFeature.asset_id))
            .join(Asset, ClipFeature.asset_id == Asset.id)
            .filter(
                or_(ClipFeature.model != clip_model, ClipFeature.pretrained != clip_pretrained),
                photo_raw_filter
            )
            .scalar() or 0
        )

        # Failed: 有 clip 失敗記錄且未重新分析成功
        clip_done_subquery = (
            session.query(ClipFeature.asset_id)
            .filter(ClipFeature.model == clip_model, ClipFeature.pretrained == clip_pretrained)
            .subquery()
        )
        clip_failed = (
            session.query(func.count(distinct(AnalysisFailure.asset_id)))
            .join(Asset, AnalysisFailure.asset_id == Asset.id)
            .filter(
                AnalysisFailure.stage == "clip",
                photo_raw_filter,
                AnalysisFailure.asset_id.notin_(select(clip_done_subquery))
            )
            .scalar() or 0
        )

        clip_pending = max(total_photo_raw - clip_done - clip_stale - clip_failed, 0)
        clip_stats = {
            "total": total_photo_raw,
            "pending": clip_pending,
            "done": clip_done,
            "stale": clip_stale,
            "failed": clip_failed,
        }

        # ── 4. Video 階段 ─────────────────────────────────────────
        video_filter = and_(active_filter, Asset.media_type == "video")
        total_video = session.query(func.count(Asset.id)).filter(video_filter).scalar() or 0

        video_done = (
            session.query(func.count(VideoInfo.asset_id))
            .join(Asset, VideoInfo.asset_id == Asset.id)
            .filter(video_filter)
            .scalar() or 0
        )

        video_done_subquery = session.query(VideoInfo.asset_id).subquery()
        video_failed = (
            session.query(func.count(distinct(AnalysisFailure.asset_id)))
            .join(Asset, AnalysisFailure.asset_id == Asset.id)
            .filter(
                AnalysisFailure.stage == "video",
                video_filter,
                AnalysisFailure.asset_id.notin_(select(video_done_subquery))
            )
            .scalar() or 0
        )

        video_pending = max(total_video - video_done - video_failed, 0)
        video_stats = {
            "total": total_video,
            "pending": video_pending,
            "done": video_done,
            "failed": video_failed,
        }

        return {
            "hash": hash_stats,
            "basic": basic_stats,
            "clip": clip_stats,
            "video": video_stats,
        }


stats_service = StatsService()
get_dashboard_stats = stats_service.get_dashboard_stats
get_analysis_stage_stats = stats_service.get_analysis_stage_stats
