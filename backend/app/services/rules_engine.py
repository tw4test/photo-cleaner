"""
偵測規則引擎（v0.5.1）

規則：
- 完全重複（SHA-256；相片/RAW 為 exact_dup，影片為 video_same_size）
- 近似重複（CLIP 餘弦相似度 及/或 pHash 漢明距離，向量化 + union-find）
- 連拍（拍攝時間在視窗內且 CLIP 相似度 ≥ 門檻）
- 模糊、過暗、過曝、極小影片
- AI 語義：螢幕截圖、文件/收據、梗圖、誤拍

所有規則一律排除 deleted / missing 資產（missing 的檔案不可能是 Keeper）。
評估後會保留使用者的 queued / kept 決定，並確保每個群組至少有一張不在待刪清單。
"""
import json
import logging
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import numpy as np
from sqlalchemy.orm import Session

from app.core.defaults import DEFAULT_SETTINGS, load_effective_settings
from app.models.enums import CandidateRule, CandidateState, GroupType, INACTIVE_ASSET_STATUSES
from app.models.schema import Asset, Candidate, ClipFeature, Feature, File, Group, Root
from app.services import similarity

logger = logging.getLogger(__name__)

# 向下相容：舊程式碼 import DEFAULT_CONFIG
DEFAULT_CONFIG: Dict[str, Any] = DEFAULT_SETTINGS

_ACTIVE = Asset.status.notin_(list(INACTIVE_ASSET_STATUSES))

# v0.7.0（VD-30）：影片候選的分數**不是 AI 信心度**，是規則固定值，不隨內容、門檻或設定改變。
# 前端不可把它顯示成百分比，也不可當成「分數門檻」或「依分數排序」的依據。
VIDEO_FIXED_SCORES = {"video_same_size": 1.0, "video_tiny": 0.7}

_AI_RULES = [
    # (rule, setting enable key, threshold key, zs keys, reason template)
    (CandidateRule.SCREENSHOT, "rule_screenshot_enabled", "screenshot_threshold",
     ("screenshot", "螢幕截圖", "截圖"), "AI 識別為螢幕截圖"),
    (CandidateRule.DOCUMENT, "rule_document_enabled", "document_threshold",
     ("document", "文件", "收據", "發票"), "AI 識別為文件/收據/筆記"),
    (CandidateRule.MEME, "rule_meme_enabled", "meme_threshold",
     ("meme", "梗圖", "迷因", "表情包"), "AI 識別為網路梗圖/表情包"),
    (CandidateRule.MISTOUCH, "rule_mistouch_enabled", "mistouch_threshold",
     ("mistouch", "誤拍", "廢片"), "AI 識別為誤拍/口袋黑畫面"),
]


def _emb(blob: Optional[bytes]) -> Optional[np.ndarray]:
    if not blob:
        return None
    v = np.frombuffer(blob, dtype=np.float32)
    n = float(np.linalg.norm(v))
    if n == 0 or not np.isfinite(n):
        return None
    return v / n


class RulesEngine:
    # ─────────────────────────────────────────────
    # 入口
    # ─────────────────────────────────────────────
    def evaluate_all_rules(self, session: Session) -> Dict[str, int]:
        cfg = load_effective_settings(session)

        preserved = {
            aid: state for aid, state in session.query(Candidate.asset_id, Candidate.state)
            .filter(Candidate.state.in_([CandidateState.QUEUED.value, CandidateState.KEPT.value]))
            .all()
        }
        # "fetch"：同時把已刪除的列從 session 的 identity map 移除，
        # 避免新建的候選重用相同 ID 時出現 "Identity map already had an identity" 警告
        session.query(Candidate).delete(synchronize_session="fetch")
        session.query(Group).delete(synchronize_session="fetch")
        session.flush()

        counts: Dict[str, int] = defaultdict(int)
        grouped: Set[int] = set()          # 已在 exact 群組中的資產（不再參與近似/連拍，每個資產只屬於一個群組）
        near_members: Set[int] = set()

        exact_members = self._evaluate_exact_duplicates(session, cfg, counts)
        grouped |= exact_members
        if cfg.get("rule_near_dup_enabled", True):
            near_members = self._evaluate_near_duplicates(session, cfg, grouped, counts)
        if cfg.get("rule_burst_enabled", True):
            self._evaluate_bursts(session, cfg, grouped | near_members, counts)
        self._evaluate_quality_rules(session, cfg, counts)
        self._evaluate_video_rules(session, cfg, counts)
        self._evaluate_ai_semantic_rules(session, cfg, counts)
        session.flush()

        for aid, state in preserved.items():
            session.query(Candidate).filter(Candidate.asset_id == aid).update(
                {"state": state}, synchronize_session=False
            )
        session.flush()
        restored = self._reconcile_keepers(session)
        session.commit()
        logger.info("規則評估完成：%s（Keeper 還原 %d）", dict(counts), restored)
        return dict(counts)

    # ─────────────────────────────────────────────
    # 群組工具
    # ─────────────────────────────────────────────
    def _add_group(self, session: Session, gtype: str, rule: str, members: List[int], keeper: int,
                   reasons: Dict[int, str], scores: Dict[int, float], keeper_reason: str,
                   counts: Dict[str, int], keeper_score: float = 0.9):
        group = Group(type=gtype, keeper_asset_id=keeper)
        session.add(group)
        session.flush()
        for aid in members:
            is_keeper = aid == keeper
            session.add(Candidate(
                asset_id=aid, rule=rule,
                reason=keeper_reason if is_keeper else reasons.get(aid, ""),
                score=keeper_score if is_keeper else scores.get(aid, 0.85),
                group_id=group.id, is_keeper=is_keeper, state=CandidateState.CANDIDATE.value,
            ))
            if not is_keeper:
                counts[rule] += 1
        counts[f"{rule}_groups"] += 1

    # ─────────────────────────────────────────────
    # 1. 完全重複
    # ─────────────────────────────────────────────
    def _evaluate_exact_duplicates(self, session: Session, cfg: Dict[str, Any], counts) -> Set[int]:
        """回傳所有被放進完全重複群組的資產 ID。"""
        photo_on = cfg.get("rule_exact_dup_enabled", True)
        video_on = cfg.get("rule_video_same_size_enabled", True)
        if not (photo_on or video_on):
            return set()
        rows = (
            session.query(File.sha256, File.mtime, Asset.id, Asset.media_type, Asset.size_total, Root.priority)
            .join(Asset, File.asset_id == Asset.id)
            .join(Root, Asset.root_id == Root.id)
            .filter(File.role == "primary", File.sha256.isnot(None), _ACTIVE)
            .all()
        )
        by_sha: Dict[str, list] = defaultdict(list)
        for r in rows:
            by_sha[r.sha256].append(r)
        members_out: Set[int] = set()
        for sha, items in by_sha.items():
            if len(items) < 2:
                continue
            is_video = items[0].media_type == "video"
            if (is_video and not video_on) or (not is_video and not photo_on):
                continue
            items.sort(key=lambda r: (r.priority, -r.size_total, r.mtime, r.id))
            keeper = items[0].id
            rule = CandidateRule.VIDEO_SAME_SIZE.value if is_video else CandidateRule.EXACT_DUP.value
            gtype = GroupType.VIDEO_SIZE.value if is_video else GroupType.EXACT.value
            members = [r.id for r in items]
            reasons = {r.id: "SHA-256 完全相同（與保留版本重複）" for r in items}
            score = VIDEO_FIXED_SCORES[rule] if is_video else 1.0
            scores = {r.id: score for r in items}
            self._add_group(session, gtype, rule, members, keeper, reasons, scores,
                            "SHA-256 完全相同（建議保留此版本）", counts, keeper_score=score)
            members_out.update(members)
        return members_out

    # ─────────────────────────────────────────────
    # 2. 近似重複
    # ─────────────────────────────────────────────
    def _load_photo_vectors(self, session: Session, cfg: Dict[str, Any], exclude: Set[int]):
        """回傳 (資產資訊 list, emb 或 None 的 list, phash list)。"""
        model, pretrained = cfg.get("clip_model"), cfg.get("clip_pretrained")
        rows = (
            session.query(Asset.id, Asset.size_total, Root.priority, Feature.phash, Feature.blur_score,
                          Feature.width, Feature.height, Feature.taken_at, Feature.exif, Feature.decode_status,
                          ClipFeature.embedding, ClipFeature.model, ClipFeature.pretrained)
            .join(Root, Asset.root_id == Root.id)
            .outerjoin(Feature, Feature.asset_id == Asset.id)
            .outerjoin(ClipFeature, ClipFeature.asset_id == Asset.id)
            .filter(Asset.media_type.in_(["photo", "raw"]), _ACTIVE)
            .all()
        )
        infos, embs, phashes = [], [], []
        stale = 0
        for r in rows:
            if r.id in exclude:
                continue
            emb = None
            if r.embedding is not None:
                if r.model == model and r.pretrained == pretrained:
                    emb = _emb(r.embedding)
                else:
                    stale += 1
            ph = r.phash if (r.decode_status == "ok" and r.phash) else None
            if emb is None and ph is None:
                continue
            infos.append(r)
            embs.append(emb)
            phashes.append(ph)
        if stale:
            logger.warning("有 %d 張相片的 CLIP 向量來自舊模型，評估時已排除（請重跑 CLIP 階段）", stale)
        return infos, embs, phashes

    def _edges(self, embs: List[Optional[np.ndarray]], phashes: List[Optional[str]], method: str,
               clip_thr: float, ph_thr: int):
        """回傳 (ei, ej, sim, dist, E, has_c, has_p, H)；sim 無資料為 nan、dist 無資料為 -1。"""
        n = len(embs)
        dim = next((e.shape[0] for e in embs if e is not None), 0)
        has_c = np.array([e is not None for e in embs], dtype=bool)
        has_p = np.array([p is not None for p in phashes], dtype=bool)
        E = np.zeros((n, max(dim, 1)), dtype=np.float32)
        for k, e in enumerate(embs):
            if e is not None:
                E[k] = e
        H = np.zeros(n, dtype=np.uint64)
        if has_p.any():
            H[has_p] = similarity.phash_hex_to_u64([p for p in phashes if p is not None])

        parts_i, parts_j = [], []
        if method in ("clip", "both", "either") and has_c.sum() >= 2:
            idx = np.flatnonzero(has_c)
            i, j, _ = similarity.clip_pairs(E[idx], clip_thr)
            parts_i.append(idx[i]); parts_j.append(idx[j])
        if method in ("phash", "either") and has_p.sum() >= 2:
            idx = np.flatnonzero(has_p)
            i, j, _ = similarity.phash_pairs(H[idx], ph_thr)
            parts_i.append(idx[i]); parts_j.append(idx[j])
        if not parts_i:
            e = np.empty(0, np.int64)
            return e, e, np.empty(0, np.float32), np.empty(0, np.int16), E, has_c, has_p, H
        ei = np.concatenate(parts_i).astype(np.int64)
        ej = np.concatenate(parts_j).astype(np.int64)
        a, b = np.minimum(ei, ej), np.maximum(ei, ej)
        _, first = np.unique(a * n + b, return_index=True)
        ei, ej = a[first], b[first]
        both_c = has_c[ei] & has_c[ej]
        sim = np.full(len(ei), np.nan, dtype=np.float32)
        if both_c.any():
            sim[both_c] = similarity.pair_cosine(E, ei[both_c], ej[both_c])
        both_p = has_p[ei] & has_p[ej]
        dist = np.full(len(ei), -1, dtype=np.int16)
        if both_p.any():
            dist[both_p] = similarity.popcount64(H[ei[both_p]] ^ H[ej[both_p]])
        return ei, ej, sim, dist, E, has_c, has_p, H

    @staticmethod
    def _edge_mask(method: str, sim: np.ndarray, dist: np.ndarray, clip_thr: float, ph_thr: int) -> np.ndarray:
        with np.errstate(invalid="ignore"):
            pc = np.nan_to_num(sim, nan=-2.0) >= clip_thr
        pp = (dist >= 0) & (dist <= ph_thr)
        if method == "clip":
            return pc
        if method == "phash":
            return pp
        if method == "both":
            return pc & pp
        return pc | pp

    def _evaluate_near_duplicates(self, session: Session, cfg: Dict[str, Any], exclude: Set[int],
                                  counts) -> Set[int]:
        method = cfg.get("near_dup_method", "either")
        clip_thr = float(cfg.get("clip_dup_threshold", 0.95))
        ph_thr = int(cfg.get("phash_threshold", 6))
        cap = int(cfg.get("near_dup_max_group", 50))
        infos, embs, phashes = self._load_photo_vectors(session, cfg, exclude)
        if len(infos) < 2:
            return set()
        ei, ej, sim, dist, E, has_c, has_p, H = self._edges(embs, phashes, method, clip_thr, ph_thr)
        base = self._edge_mask(method, sim, dist, clip_thr, ph_thr)
        ei, ej, sim, dist = ei[base], ej[base], sim[base], dist[base]
        comps = similarity.components(range(len(infos)), ei, ej)

        def tighter(level: int) -> np.ndarray:
            return self._edge_mask(method, sim, dist, min(0.999, clip_thr + 0.01 * level),
                                   max(0, ph_thr - level))

        final: List[List[int]] = []
        for c in comps:
            final.extend(similarity.split_large_component(c, ei, ej, tighter, cap))

        members_out: Set[int] = set()
        for comp in final:
            def keeper_key(k):
                r = infos[k]
                return (r.priority, -(r.blur_score or 0.0), -((r.width or 0) * (r.height or 0)),
                        -r.size_total, r.id)
            ordered = sorted(comp, key=keeper_key)
            kk = ordered[0]
            keeper = infos[kk].id
            reasons, scores = {}, {}
            for k in ordered[1:]:
                aid = infos[k].id
                if has_c[k] and has_c[kk]:
                    s = float(E[k] @ E[kk])
                    reasons[aid] = f"與保留版本外觀相似（CLIP 相似度 {s:.3f}）"
                    scores[aid] = round(s, 3)
                elif has_p[k] and has_p[kk]:
                    d = int(similarity.popcount64(np.array([H[k] ^ H[kk]], dtype=np.uint64))[0])
                    reasons[aid] = f"與保留版本外觀相似（pHash 距離 {d}）"
                    scores[aid] = round(1 - d / 64, 3)
                else:
                    reasons[aid] = "與群組內其他相片外觀相似"
                    scores[aid] = 0.85
            self._add_group(session, GroupType.NEAR.value, CandidateRule.NEAR_DUP.value,
                            [infos[k].id for k in ordered], keeper, reasons, scores,
                            "近似相片中畫質最佳（建議保留）", counts)
            members_out.update(infos[k].id for k in comp)
        return members_out

    # ─────────────────────────────────────────────
    # 3. 連拍
    # ─────────────────────────────────────────────
    def _evaluate_bursts(self, session: Session, cfg: Dict[str, Any], exclude: Set[int], counts):
        window = float(cfg.get("burst_time_window_seconds", 2.0))
        thr = float(cfg.get("burst_similarity_threshold", 0.85))
        cap = int(cfg.get("near_dup_max_group", 50))
        infos, embs, _ = self._load_photo_vectors(session, cfg, exclude)
        items = [
            (k, r) for k, r in enumerate(infos)
            if embs[k] is not None and r.taken_at is not None
            and not ((r.exif or {}).get("_taken_at_source") == "mtime")
        ]
        if len(items) < 2:
            return
        items.sort(key=lambda kr: kr[1].taken_at)
        ts = np.array([r.taken_at.timestamp() for _, r in items])
        ks = [k for k, _ in items]
        ei, ej, sims = [], [], []
        for a in range(len(items)):
            b = a + 1
            while b < len(items) and ts[b] - ts[a] <= window:
                s = float(embs[ks[a]] @ embs[ks[b]])
                if s >= thr:
                    ei.append(ks[a]); ej.append(ks[b]); sims.append(s)
                b += 1
        if not ei:
            return
        ei_a, ej_a, sim_a = np.array(ei), np.array(ej), np.array(sims, dtype=np.float32)
        comps = similarity.components(range(len(infos)), ei_a, ej_a)

        def tighter(level: int) -> np.ndarray:
            return sim_a >= min(0.999, thr + 0.02 * level)

        final: List[List[int]] = []
        for c in comps:
            final.extend(similarity.split_large_component(c, ei_a, ej_a, tighter, cap))
        for comp in final:
            ordered = sorted(comp, key=lambda k: (-(infos[k].blur_score or 0.0), infos[k].priority,
                                                  -infos[k].size_total, infos[k].id))
            kk = ordered[0]
            keeper = infos[kk].id
            reasons, scores = {}, {}
            for k in ordered[1:]:
                s = float(embs[k] @ embs[kk])
                reasons[infos[k].id] = f"連拍多餘（清晰度較低，與最佳張相似度 {s:.3f}）"
                scores[infos[k].id] = round(s, 3)
            self._add_group(session, GroupType.BURST.value, CandidateRule.BURST.value,
                            [infos[k].id for k in ordered], keeper, reasons, scores,
                            "連拍中最清晰（建議保留）", counts)

    # ─────────────────────────────────────────────
    # 4. 畫質
    # ─────────────────────────────────────────────
    def _evaluate_quality_rules(self, session: Session, cfg: Dict[str, Any], counts):
        blur_on = cfg.get("rule_blur_enabled", True)
        exp_on = cfg.get("rule_exposure_enabled", True)
        if not (blur_on or exp_on):
            return
        blur_thr = float(cfg.get("blur_threshold", 100.0))
        under = float(cfg.get("under_exposure_threshold", 30.0))
        over = float(cfg.get("over_exposure_threshold", 225.0))
        rows = (
            session.query(Feature.asset_id, Feature.blur_score, Feature.brightness)
            .join(Asset, Feature.asset_id == Asset.id)
            .filter(_ACTIVE, Feature.decode_status == "ok")
            .all()
        )
        for aid, blur, bright in rows:
            if blur_on and blur is not None and blur_thr > 0 and blur < blur_thr:
                session.add(Candidate(
                    asset_id=aid, rule=CandidateRule.BLUR.value,
                    reason=f"相片模糊（清晰度分數 {blur:.1f} < 門檻 {blur_thr:.0f}）",
                    score=round(1.0 - (blur / blur_thr), 2), state=CandidateState.CANDIDATE.value))
                counts["blur"] += 1
            if exp_on and bright is not None:
                if under > 0 and bright < under:
                    session.add(Candidate(
                        asset_id=aid, rule=CandidateRule.UNDER_EXPOSURE.value,
                        reason=f"相片過暗（平均亮度 {bright:.1f} < 門檻 {under:.0f}）",
                        score=round(1.0 - (bright / under), 2), state=CandidateState.CANDIDATE.value))
                    counts["under_exposure"] += 1
                elif bright > over and over < 255:
                    session.add(Candidate(
                        asset_id=aid, rule=CandidateRule.OVER_EXPOSURE.value,
                        reason=f"相片過曝（平均亮度 {bright:.1f} > 門檻 {over:.0f}）",
                        score=round((bright - over) / (255 - over), 2), state=CandidateState.CANDIDATE.value))
                    counts["over_exposure"] += 1

    # ─────────────────────────────────────────────
    # 5. 影片
    # ─────────────────────────────────────────────
    def _evaluate_video_rules(self, session: Session, cfg: Dict[str, Any], counts):
        if not cfg.get("rule_video_tiny_enabled", False):
            return
        limit = int(cfg.get("video_tiny_threshold_kb", 100)) * 1024
        rows = session.query(Asset.id, Asset.size_total).filter(
            Asset.media_type == "video", _ACTIVE, Asset.size_total < limit).all()
        for aid, size in rows:
            session.add(Candidate(
                asset_id=aid, rule=CandidateRule.VIDEO_TINY.value,
                reason=f"影片極小（{size / 1024:.0f} KB < {limit // 1024} KB），可能是誤錄",
                score=VIDEO_FIXED_SCORES["video_tiny"], state=CandidateState.CANDIDATE.value))
            counts["video_tiny"] += 1

    # ─────────────────────────────────────────────
    # 6. AI 語義
    # ─────────────────────────────────────────────
    def _evaluate_ai_semantic_rules(self, session: Session, cfg: Dict[str, Any], counts):
        enabled = [(r, cfg.get(th, DEFAULT_SETTINGS[th]), keys, text) for r, en, th, keys, text in _AI_RULES
                   if cfg.get(en, True)]
        if not enabled:
            return
        model, pretrained = cfg.get("clip_model"), cfg.get("clip_pretrained")
        rows = (
            session.query(ClipFeature.asset_id, ClipFeature.zs)
            .join(Asset, ClipFeature.asset_id == Asset.id)
            .filter(_ACTIVE, ClipFeature.model == model, ClipFeature.pretrained == pretrained,
                    ClipFeature.zs.isnot(None))
            .all()
        )
        for aid, raw in rows:
            zs = raw
            if isinstance(raw, str):
                try:
                    zs = json.loads(raw)
                except Exception:
                    zs = {}
            if not isinstance(zs, dict):
                continue
            normal = float(zs.get("normal_photo", 0.0))
            for rule, thr, keys, text in enabled:
                score = next((float(zs[k]) for k in keys if k in zs), None)
                if score is None:
                    continue
                if score >= float(thr) and score > normal:
                    session.add(Candidate(
                        asset_id=aid, rule=rule.value,
                        reason=f"{text}（信心度 {int(score * 100)}% ≥ 門檻 {int(float(thr) * 100)}%）",
                        score=score, state=CandidateState.CANDIDATE.value))
                    counts[rule.value] += 1

    # ─────────────────────────────────────────────
    # Keeper 保護
    # ─────────────────────────────────────────────
    def _group_members(self, session: Session, group_ids: Iterable[int]) -> Dict[int, List[Tuple[int, bool, str]]]:
        group_ids = list(set(group_ids))
        out: Dict[int, List[Tuple[int, bool, str]]] = defaultdict(list)
        if not group_ids:
            return out
        seen = set()
        rows = (
            session.query(Candidate.group_id, Candidate.asset_id, Candidate.is_keeper, Asset.status)
            .join(Asset, Candidate.asset_id == Asset.id)
            .filter(Candidate.group_id.in_(group_ids))
            .all()
        )
        for gid, aid, is_keeper, status in rows:
            if (gid, aid) in seen:
                continue
            seen.add((gid, aid))
            out[gid].append((aid, bool(is_keeper), status))
        return out

    def _groups_of_assets(self, session: Session, asset_ids: Iterable[int]) -> Set[int]:
        asset_ids = list(set(asset_ids))
        if not asset_ids:
            return set()
        return {g for (g,) in session.query(Candidate.group_id)
                .filter(Candidate.asset_id.in_(asset_ids), Candidate.group_id.isnot(None)).distinct()}

    def filter_queue_requests(self, session: Session, asset_ids: Iterable[int]) -> Tuple[Set[int], Set[int]]:
        """
        【鐵則 3】使用者要把 asset_ids 加入待刪時呼叫。
        以「資產」為單位檢查它所屬的每一個群組：若加入後群組內沒有任何一張仍保留（未待刪、未刪除、
        未遺失），就把其中一張（優先 Keeper）擋下。回傳 (允許, 被保護) 的資產 ID 集合。
        """
        requested = set(asset_ids)
        groups = self._group_members(session, self._groups_of_assets(session, requested))
        blocked: Set[int] = set()
        changed = True
        while changed:
            changed = False
            for gid, members in groups.items():
                alive = [m for m in members if m[2] not in INACTIVE_ASSET_STATUSES]
                if not alive:
                    continue
                survivors = [m for m in alive
                             if (m[0] not in requested or m[0] in blocked) and m[2] != "queued"]
                if survivors:
                    continue
                pickable = [m for m in alive if m[0] in requested and m[0] not in blocked]
                if not pickable:
                    continue
                keeper = next((m for m in pickable if m[1]), None)
                pick = keeper or min(pickable, key=lambda m: m[0])
                blocked.add(pick[0])
                changed = True
        return requested - blocked, blocked

    def ensure_keeper_protection(self, session: Session, candidate_ids_to_queue: List[int]) -> List[int]:
        """【向下相容】輸入候選 ID，回傳允許加入待刪的候選 ID。"""
        rows = session.query(Candidate.id, Candidate.asset_id).filter(Candidate.id.in_(candidate_ids_to_queue)).all()
        allowed, _ = self.filter_queue_requests(session, [aid for _, aid in rows])
        return [cid for cid, aid in rows if aid in allowed]

    def protect_deletion_batch(self, session: Session, asset_ids: Iterable[int]) -> Set[int]:
        """
        刪除前最後一道防線：若這批刪除會讓某個群組一張都不剩（只計算仍存在於磁碟上的成員），
        從這批中移除一張（優先 Keeper）。回傳被擋下的資產 ID。
        """
        batch = set(asset_ids)
        groups = self._group_members(session, self._groups_of_assets(session, batch))
        blocked: Set[int] = set()
        changed = True
        while changed:
            changed = False
            for gid, members in groups.items():
                alive = [m for m in members if m[2] not in INACTIVE_ASSET_STATUSES]
                if not alive:
                    continue
                if any(m[0] not in batch or m[0] in blocked for m in alive):
                    continue
                pickable = [m for m in alive if m[0] not in blocked]
                keeper = next((m for m in pickable if m[1]), None)
                pick = keeper or min(pickable, key=lambda m: m[0])
                blocked.add(pick[0])
                changed = True
        return blocked

    def _reconcile_keepers(self, session: Session) -> int:
        """評估後：若某群組所有現存成員都在待刪，把 Keeper 退回候選。"""
        gids = [g for (g,) in session.query(Group.id).all()]
        groups = self._group_members(session, gids)
        restored = 0
        for gid, members in groups.items():
            alive = [m for m in members if m[2] not in INACTIVE_ASSET_STATUSES]
            if not alive:
                continue
            states = {aid: st for aid, st in session.query(Candidate.asset_id, Candidate.state)
                      .filter(Candidate.group_id == gid)}
            if all(states.get(m[0]) == CandidateState.QUEUED.value for m in alive):
                keeper = next((m for m in alive if m[1]), alive[0])
                session.query(Candidate).filter(Candidate.asset_id == keeper[0]).update(
                    {"state": CandidateState.CANDIDATE.value}, synchronize_session=False)
                session.query(Asset).filter(Asset.id == keeper[0]).update(
                    {"status": "candidate"}, synchronize_session=False)
                restored += 1
        return restored


rules_engine = RulesEngine()
