"""
分析任務編排模組。
協調四個獨立分析階段（hash、basic、clip、video）的執行，
支援暫停/取消、retry 失敗項目、以及「全部分析」時的合併解碼（A-3）。
"""

import logging
from typing import Optional
from sqlalchemy.orm import Session

from app.models.enums import AnalysisStage, ComputeDevice

logger = logging.getLogger(__name__)

# 合法的 stage 順序
ALL_STAGES = [
    AnalysisStage.HASH,
    AnalysisStage.BASIC,
    AnalysisStage.CLIP,
    AnalysisStage.VIDEO,
]


def _resolve_device(session: Session, device: str) -> str:
    """解析運算裝置，若設定為 'auto' 則嘗試 CUDA，否則退回 CPU。"""
    if device == ComputeDevice.AUTO:
        try:
            from app.stubs.clip_stub import clip_extractor
            cuda_ok = getattr(clip_extractor, "cuda_available", False)
            resolved = "cuda" if cuda_ok else "cpu"
        except Exception:
            resolved = "cpu"
        logger.info(f"compute_device=auto，實際使用：{resolved}")
        return resolved

    if device == ComputeDevice.CUDA:
        try:
            from app.stubs.clip_stub import clip_extractor
            cuda_ok = getattr(clip_extractor, "cuda_available", False)
        except Exception:
            cuda_ok = False
        if not cuda_ok:
            from app.services.cuda_manager import cuda_manager
            hw = cuda_manager.get_cuda_status()
            if hw.get("has_nvidia_gpu"):
                raise RuntimeError(
                    f"偵測到您的電腦配備 {hw.get('gpu_name')}，但目前環境為 CPU 版 PyTorch。"
                    "請在介面點擊「一鍵啟用 GPU 加速」下載安裝 CUDA 套件，或改選「自動」/「CPU」模式。"
                )
            raise RuntimeError("本機未檢測到相容的 NVIDIA GPU，無法強制使用 CUDA 模式。請選擇 CPU 或自動模式。")
        return "cuda"

    return "cpu"


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
    """
    依序執行指定的分析階段。

    Args:
        session: SQLAlchemy sync session
        stages: 要執行的階段列表（預設全部）
        device: 運算裝置 'auto' | 'cuda' | 'cpu'
        retry_failed: 是否重試失敗的項目
        progress_tracker: ProgressTracker 實例（可 None）
        hash_runners:  hash  階段並行 worker 數（None = 使用 config 預設）
        basic_runners: basic 階段並行 worker 數（None = 使用 config 預設）
        clip_runners:  clip  階段並行 worker 數（None = 使用 config 預設）
        video_runners: video 階段並行 worker 數（None = 使用 config 預設）

    Returns:
        各階段結果的字典，例如：
        {
            'hash': {'processed': 10, 'done': 8, 'not_needed': 2, 'failed': 0},
            'basic': {'processed': 8, 'done': 7, 'failed': 1},
            'clip': {'processed': 8, 'done': 7, 'stale_found': 0, 'failed': 1, 'device_used': 'cpu'},
            'video': {'processed': 2, 'done': 2, 'failed': 0},
        }
    """
    if stages is None:
        stages = [s.value for s in ALL_STAGES]

    # 驗證 stage 名稱合法性
    valid_names = {s.value for s in ALL_STAGES}
    for s in stages:
        if s not in valid_names:
            raise ValueError(f"不合法的分析階段：'{s}'，合法值：{valid_names}")

    # 裝置決策（只對 clip 有效，但在開始時統一驗證，快速失敗）
    if "clip" in stages:
        resolved_device = _resolve_device(session, device)
    else:
        resolved_device = "cpu"  # 其他階段不使用 GPU

    results = {}
    running_stages = [s for s in ALL_STAGES if s.value in stages]

    # 若同時執行 basic 與 clip，記錄此資訊供各 stage 合併解碼（A-3）
    merge_decode = (
        AnalysisStage.BASIC in running_stages and
        AnalysisStage.CLIP in running_stages
    )

    # 從 Settings 讀取預設 runner 設定（若外部呼叫未覆寫）
    from app.core.config import settings as app_settings
    from app.models.schema import Setting
    import json

    def _get_setting_runner(key: str, fallback: int) -> int:
        try:
            s = session.query(Setting).get(key)
            if s:
                val = json.loads(s.value)
                if isinstance(val, int) and val > 0:
                    return val
        except Exception:
            pass
        return fallback

    if hash_runners is None:
        hash_runners = _get_setting_runner("hash_runners", app_settings.HASH_RUNNERS)
    if basic_runners is None:
        basic_runners = _get_setting_runner("basic_runners", app_settings.BASIC_RUNNERS)
    if clip_runners is None:
        clip_runners = _get_setting_runner("clip_runners", app_settings.CLIP_RUNNERS)
    if video_runners is None:
        video_runners = _get_setting_runner("video_runners", app_settings.VIDEO_RUNNERS)

    stage_runners_map = {
        AnalysisStage.HASH: hash_runners,
        AnalysisStage.BASIC: basic_runners,
        AnalysisStage.CLIP: clip_runners,
        AnalysisStage.VIDEO: video_runners,
    }

    for stage in running_stages:
        if progress_tracker and progress_tracker.is_cancel_requested():
            logger.info(f"使用者已取消，停止於 {stage.value} 階段前")
            break

        stage_name = stage.value
        stage_runner_count = stage_runners_map.get(stage, 1)
        logger.info(f"開始執行分析階段：{stage_name}，runner={stage_runner_count}")

        if progress_tracker:
            progress_tracker.set_current_stage(stage_name, runners=stage_runner_count)

        try:
            if stage == AnalysisStage.HASH:
                from app.services.hash_stage import run_hash_stage
                result = run_hash_stage(
                    session, retry_failed=retry_failed,
                    progress_tracker=progress_tracker,
                    runners=hash_runners,
                )

            elif stage == AnalysisStage.BASIC:
                from app.services.basic_stage import run_basic_stage
                result = run_basic_stage(
                    session, retry_failed=retry_failed,
                    merge_with_clip=merge_decode,
                    progress_tracker=progress_tracker,
                    runners=basic_runners,
                )

            elif stage == AnalysisStage.CLIP:
                from app.services.clip_stage import run_clip_stage
                result = run_clip_stage(
                    session, device=resolved_device,
                    retry_failed=retry_failed,
                    merge_with_basic=merge_decode,
                    progress_tracker=progress_tracker,
                    runners=clip_runners,
                )
                result["device_used"] = resolved_device

            elif stage == AnalysisStage.VIDEO:
                from app.services.video_stage import run_video_stage
                result = run_video_stage(
                    session, retry_failed=retry_failed,
                    progress_tracker=progress_tracker,
                    runners=video_runners,
                )


            results[stage_name] = result
            logger.info(f"階段 {stage_name} 完成：{result}")

        except RuntimeError as e:
            # GPU 不可用等嚴重錯誤 → 中止整個 job
            logger.error(f"階段 {stage_name} 發生嚴重錯誤：{e}")
            raise
        except Exception as e:
            logger.error(f"階段 {stage_name} 發生未預期錯誤：{e}", exc_info=True)
            results[stage_name] = {"error": str(e)}

    return results


def rebuild_thumbs_job(
    session: Session,
    progress_tracker=None,
) -> dict:
    """
    只補算缺少縮圖的相片，不重跑其他分析。
    對應 A-20 需求。
    """
    from app.services.basic_stage import run_rebuild_thumbs_only
    return run_rebuild_thumbs_only(session, progress_tracker=progress_tracker)
