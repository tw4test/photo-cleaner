"""
分析任務編排模組（v0.5）。
依序執行 hash → basic → clip → video 中被指定的階段。
- 使用者取消（InterruptedError）會立即中止整個工作並往上拋，不再被當成某階段的一般錯誤。
- GPU/模型等嚴重錯誤（RuntimeError）中止整個工作。
"""
import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.core.defaults import get_setting
from app.models.enums import AnalysisStage

logger = logging.getLogger(__name__)

ALL_STAGES = [AnalysisStage.HASH, AnalysisStage.BASIC, AnalysisStage.CLIP, AnalysisStage.VIDEO]


def _resolve_device(session: Session, device: str) -> str:
    """'auto' 有 GPU 用 GPU；'cuda' 不可用時直接報錯並說明原因；'cpu' 一律 CPU。"""
    from app.stubs.clip_stub import clip_extractor
    if device == "cuda" and not clip_extractor.cuda_available:
        from app.services.cuda_manager import cuda_manager
        hw = cuda_manager.get_cuda_status()
        if hw.get("has_nvidia_gpu"):
            raise RuntimeError(
                f"偵測到您的電腦配備 {hw.get('gpu_name')}，但目前環境為 CPU 版 PyTorch。"
                "請在介面點擊「一鍵啟用 GPU 加速」安裝 CUDA 套件，或改選「自動」/「CPU」模式。"
            )
        raise RuntimeError("本機未檢測到相容的 NVIDIA GPU，無法強制使用 CUDA 模式。請選擇 CPU 或自動模式。")
    resolved = clip_extractor.resolve_device(device)
    logger.info("compute_device=%s，實際使用：%s", device, resolved)
    return resolved


def run_analyze_job(
    session: Session,
    stages: Optional[list] = None,
    device: str = "auto",
    retry_failed: bool = False,
    progress_tracker=None,
    hash_runners: Optional[int] = None,
    basic_runners: Optional[int] = None,
    clip_runners: Optional[int] = None,
    video_runners: Optional[int] = None,
) -> dict:
    if stages is None:
        stages = [s.value for s in ALL_STAGES]
    valid_names = {s.value for s in ALL_STAGES}
    for s in stages:
        if s not in valid_names:
            raise ValueError(f"不合法的分析階段：'{s}'，合法值：{valid_names}")

    resolved_device = _resolve_device(session, device) if "clip" in stages else "cpu"
    runners = {
        AnalysisStage.HASH: hash_runners or get_setting(session, "hash_runners"),
        AnalysisStage.BASIC: basic_runners or get_setting(session, "basic_runners"),
        AnalysisStage.CLIP: clip_runners or get_setting(session, "clip_runners"),
        AnalysisStage.VIDEO: video_runners or get_setting(session, "video_runners"),
    }
    results = {}
    for stage in [s for s in ALL_STAGES if s.value in stages]:
        if progress_tracker and progress_tracker.is_cancel_requested():
            raise InterruptedError("使用者已取消分析任務")
        name = stage.value
        logger.info("開始執行分析階段：%s，runner=%s", name, runners[stage])
        try:
            if stage == AnalysisStage.HASH:
                from app.services.hash_stage import run_hash_stage
                result = run_hash_stage(session, retry_failed=retry_failed,
                                        progress_tracker=progress_tracker, runners=runners[stage])
            elif stage == AnalysisStage.BASIC:
                from app.services.basic_stage import run_basic_stage
                result = run_basic_stage(session, retry_failed=retry_failed,
                                         progress_tracker=progress_tracker, runners=runners[stage])
            elif stage == AnalysisStage.CLIP:
                from app.services.clip_stage import run_clip_stage
                result = run_clip_stage(session, device=resolved_device, retry_failed=retry_failed,
                                        progress_tracker=progress_tracker, runners=runners[stage])
            else:
                from app.services.video_stage import run_video_stage
                result = run_video_stage(session, retry_failed=retry_failed,
                                         progress_tracker=progress_tracker, runners=runners[stage])
            results[name] = result
            logger.info("階段 %s 完成：%s", name, result)
        except (InterruptedError, RuntimeError):
            raise
        except Exception as e:
            logger.error("階段 %s 發生未預期錯誤：%s", name, e, exc_info=True)
            results[name] = {"error": str(e)}
    return results


def rebuild_thumbs_job(session: Session, progress_tracker=None) -> dict:
    from app.services.basic_stage import run_rebuild_thumbs_only
    return run_rebuild_thumbs_only(session, progress_tracker=progress_tracker)
