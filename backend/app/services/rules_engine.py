import json
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select, and_
import imagehash

from app.core.config import settings
from app.models.schema import Asset, File, Feature, Candidate, Group, Root, Setting
from app.models.enums import CandidateRule, CandidateState, GroupType

DEFAULT_CONFIG: Dict[str, Any] = {
    "rule_exact_dup_enabled": True,
    "rule_near_dup_enabled": True,
    "rule_burst_enabled": True,
    "rule_blur_enabled": True,
    "rule_exposure_enabled": True,
    "rule_screenshot_enabled": False,
    "rule_mistouch_enabled": True,
    "rule_video_same_size_enabled": True,
    "rule_video_tiny_enabled": False,
    "phash_threshold": 6,
    "blur_threshold": 100.0,
    "under_exposure_threshold": 30.0,
    "over_exposure_threshold": 225.0,
    "burst_time_window_seconds": 2.0,
    "burst_similarity_threshold": 0.85,
    "video_tiny_threshold_kb": 100,
    "cross_path_duplicates": True,
}

class RulesEngine:
    """
    偵測規則引擎：
    - 完全重複 (Exact Duplicate)
    - 近似重複 (Near Duplicate)
    - 模糊相片 (Blur)
    - 過曝 / 過暗 (Exposure)
    - 影片大小重複 (Video Same Size)
    - 保留最後一張防呆保護 (Keeper Protection)
    """

    def _get_active_config(self, session: Session) -> Dict[str, Any]:
        """從資料庫載入最新設定"""
        config = DEFAULT_CONFIG.copy()
        db_settings = session.query(Setting).all()
        for s in db_settings:
            try:
                config[s.key] = json.loads(s.value)
            except Exception:
                config[s.key] = s.value
        return config

    def evaluate_all_rules(self, session: Session):
        """執行所有規則並生成候選資料"""
        config = self._get_active_config(session)

        # 清除舊的候選記錄以利重新評估
        session.query(Candidate).delete()
        session.query(Group).delete()
        session.flush()

        # 1. 執行完全重複規則 (Exact Duplicates)
        if config.get("rule_exact_dup_enabled", True):
            self._evaluate_exact_duplicates(session, config)

        # 2. 執行近似重複規則 (Near Duplicates via pHash)
        if config.get("rule_near_dup_enabled", True):
            self._evaluate_near_duplicates(session, config)

        # 3. 執行單張質量缺陷規則 (Blur & Exposure)
        self._evaluate_quality_rules(session, config)

        session.commit()

    def _evaluate_exact_duplicates(self, session: Session, config: Dict[str, Any]):
        """尋找 SHA-256 完全相同的檔案"""
        files = (
            session.query(File, Asset, Root)
            .join(Asset, File.asset_id == Asset.id)
            .join(Root, Asset.root_id == Root.id)
            .filter(File.role == "primary", File.sha256.isnot(None), Asset.status != "deleted")
            .all()
        )

        hash_groups: Dict[str, List[tuple[File, Asset, Root]]] = {}
        for f, a, r in files:
            hash_groups.setdefault(f.sha256, []).append((f, a, r))

        for sha, group_items in hash_groups.items():
            if len(group_items) < 2:
                continue

            group = Group(type=GroupType.EXACT.value)
            session.add(group)
            session.flush()

            sorted_items = sorted(
                group_items,
                key=lambda x: (x[2].priority, -x[1].size_total, x[0].mtime)
            )

            keeper_file, keeper_asset, _ = sorted_items[0]
            group.keeper_asset_id = keeper_asset.id

            session.add(Candidate(
                asset_id=keeper_asset.id,
                rule=CandidateRule.EXACT_DUP.value,
                reason="SHA-256 完全相同（建議保留最佳版本）",
                score=1.0,
                group_id=group.id,
                is_keeper=True,
                state=CandidateState.CANDIDATE.value
            ))

            for f, a, r in sorted_items[1:]:
                session.add(Candidate(
                    asset_id=a.id,
                    rule=CandidateRule.EXACT_DUP.value,
                    reason=f"SHA-256 完全相同（與 {keeper_file.path} 重複）",
                    score=1.0,
                    group_id=group.id,
                    is_keeper=False,
                    state=CandidateState.CANDIDATE.value
                ))

    def _evaluate_near_duplicates(self, session: Session, config: Dict[str, Any]):
        """利用 pHash 感知雜湊距離分組尋找近似圖片"""
        phash_threshold = int(config.get("phash_threshold", 6))

        existing_candidate_asset_ids = {
            c.asset_id for c in session.query(Candidate.asset_id).all()
        }

        features = (
            session.query(Feature, Asset, Root)
            .join(Asset, Feature.asset_id == Asset.id)
            .join(Root, Asset.root_id == Root.id)
            .filter(
                Feature.phash.isnot(None),
                Asset.status != "deleted",
                ~Asset.id.in_(existing_candidate_asset_ids) if existing_candidate_asset_ids else True
            )
            .all()
        )

        matched_pairs: List[tuple[int, int, int]] = []
        for i in range(len(features)):
            feat1, a1, r1 = features[i]
            try:
                h1 = imagehash.hex_to_hash(feat1.phash)
            except Exception:
                continue

            for j in range(i + 1, len(features)):
                feat2, a2, r2 = features[j]
                try:
                    h2 = imagehash.hex_to_hash(feat2.phash)
                    dist = h1 - h2
                    if dist <= phash_threshold:
                        matched_pairs.append((i, j, dist))
                except Exception:
                    continue

        clusters: List[set[int]] = []
        for i, j, _ in matched_pairs:
            found = False
            for cl in clusters:
                if i in cl or j in cl:
                    cl.add(i)
                    cl.add(j)
                    found = True
                    break
            if not found:
                clusters.append({i, j})

        for cl in clusters:
            items = [features[idx] for idx in cl]
            if len(items) < 2:
                continue

            group = Group(type=GroupType.NEAR.value)
            session.add(group)
            session.flush()

            sorted_items = sorted(
                items,
                key=lambda x: (x[2].priority, -(x[0].blur_score or 0.0), -x[1].size_total)
            )

            keeper_feat, keeper_asset, _ = sorted_items[0]
            group.keeper_asset_id = keeper_asset.id

            session.add(Candidate(
                asset_id=keeper_asset.id,
                rule=CandidateRule.NEAR_DUP.value,
                reason="近似圖片（清晰度較佳，建議保留）",
                score=0.9,
                group_id=group.id,
                is_keeper=True,
                state=CandidateState.CANDIDATE.value
            ))

            for feat, a, r in sorted_items[1:]:
                session.add(Candidate(
                    asset_id=a.id,
                    rule=CandidateRule.NEAR_DUP.value,
                    reason="近似圖片（建議刪除較模糊副本）",
                    score=0.85,
                    group_id=group.id,
                    is_keeper=False,
                    state=CandidateState.CANDIDATE.value
                ))

    def _evaluate_quality_rules(self, session: Session, config: Dict[str, Any]):
        """評估單張圖片質量：模糊、過曝、過暗"""
        blur_enabled = config.get("rule_blur_enabled", True)
        exposure_enabled = config.get("rule_exposure_enabled", True)
        blur_threshold = float(config.get("blur_threshold", 100.0))
        under_exp_threshold = float(config.get("under_exposure_threshold", 30.0))
        over_exp_threshold = float(config.get("over_exposure_threshold", 225.0))

        features = (
            session.query(Feature, Asset)
            .join(Asset, Feature.asset_id == Asset.id)
            .filter(Asset.status != "deleted")
            .all()
        )

        for feat, asset in features:
            # 1. 模糊檢查
            if blur_enabled and feat.blur_score is not None and feat.blur_score < blur_threshold:
                session.add(Candidate(
                    asset_id=asset.id,
                    rule=CandidateRule.BLUR.value,
                    reason=f"相片模糊（清晰度分數 {feat.blur_score:.1f} < 門檻 {blur_threshold:.0f}）",
                    score=round(1.0 - (feat.blur_score / blur_threshold), 2),
                    group_id=None,
                    is_keeper=False,
                    state=CandidateState.CANDIDATE.value
                ))

            # 2. 過暗檢查
            if exposure_enabled and feat.brightness is not None and feat.brightness < under_exp_threshold:
                session.add(Candidate(
                    asset_id=asset.id,
                    rule=CandidateRule.UNDER_EXPOSURE.value,
                    reason=f"相片過暗（平均亮度 {feat.brightness:.1f} < 門檻 {under_exp_threshold:.0f}）",
                    score=round(1.0 - (feat.brightness / under_exp_threshold), 2),
                    group_id=None,
                    is_keeper=False,
                    state=CandidateState.CANDIDATE.value
                ))
            # 3. 過曝檢查
            elif exposure_enabled and feat.brightness is not None and feat.brightness > over_exp_threshold:
                session.add(Candidate(
                    asset_id=asset.id,
                    rule=CandidateRule.OVER_EXPOSURE.value,
                    reason=f"相片過曝（平均亮度 {feat.brightness:.1f} > 門檻 {over_exp_threshold:.0f}）",
                    score=round((feat.brightness - over_exp_threshold) / (255 - over_exp_threshold), 2),
                    group_id=None,
                    is_keeper=False,
                    state=CandidateState.CANDIDATE.value
                ))

    def ensure_keeper_protection(self, session: Session, candidate_ids_to_queue: List[int]) -> List[int]:
        """
        【鐵則 3：保留最後一張保護】
        若用戶嘗試將同一個重複組內的「所有」資產都標記為待刪/刪除，
        系統強制攔截並保留原先的 Keeper 不予加入待刪。
        """
        candidates = session.query(Candidate).filter(Candidate.id.in_(candidate_ids_to_queue)).all()
        safe_candidate_ids = []
        for c in candidates:
            if c.is_keeper:
                other_members = session.query(Candidate).filter(
                    Candidate.group_id == c.group_id,
                    Candidate.id != c.id
                ).all()
                all_others_queued = all(m.id in candidate_ids_to_queue for m in other_members)
                if all_others_queued:
                    continue
            safe_candidate_ids.append(c.id)

        return safe_candidate_ids

rules_engine = RulesEngine()
