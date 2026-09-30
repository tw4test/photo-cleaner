import time
import asyncio
import threading
from typing import Optional, Dict, Any, List
from pydantic import BaseModel


class StageStats(BaseModel):
    pending: int = 0
    done: int = 0
    failed: int = 0
    stale: int = 0   # clip 階段特有：過期（模型不符）


class ProgressState(BaseModel):
    is_running: bool = False
    job_type: str = "idle"   # idle | scan | analyze | rebuild_thumbs | evaluate
    stage: str = "idle"
    # idle | discovering | processing | evaluating_rules | completed | error
    # scan sub-stages: discovering, syncing
    # analyze sub-stages: hash, basic, clip, video
    stage_label: str = "閒置中"
    current_stage: str = ""  # 正在執行的分析階段名稱
    device_used: str = ""    # 實際使用的運算裝置
    total_files: int = 0
    processed_files: int = 0
    percentage: float = 0.0
    current_file: str = ""
    speed_fps: float = 0.0
    eta_seconds: Optional[int] = None
    new_assets_count: int = 0
    new_candidates_count: int = 0
    scan_summary: Dict[str, int] = {}   # {added, modified, missing, unchanged}
    stage_stats: Dict[str, Any] = {}   # {hash: StageStats, basic: ..., clip: ..., video: ...}
    error_message: Optional[str] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    duration_seconds: float = 0.0

    # 向下相容（舊前端讀取）
    @property
    def is_scanning(self) -> bool:
        return self.is_running

    class Config:
        # 讓 is_scanning property 可序列化
        pass


class ProgressTracker:
    def __init__(self):
        self.state = ProgressState()
        self._subscribers: List[asyncio.Queue] = []
        self._cancel_requested = False
        self._lock = threading.RLock()

    # ─── 通用 job 控制 ───────────────────────────────────────────────

    def _start_job(self, job_type: str, stage: str, label: str):
        with self._lock:
            self._cancel_requested = False
            self.state = ProgressState(
                is_running=True,
                job_type=job_type,
                stage=stage,
                stage_label=label,
                started_at=time.time(),
            )
            self._broadcast_internal()

    def _complete_job(self, label: str = "完成！", **extra):
        with self._lock:
            now = time.time()
            start = self.state.started_at or now
            self.state.is_running = False
            self.state.stage = "completed"
            self.state.stage_label = label
            self.state.percentage = 100.0
            self.state.finished_at = now
            self.state.duration_seconds = round(now - start, 1)
            for k, v in extra.items():
                if hasattr(self.state, k):
                    setattr(self.state, k, v)
            self._broadcast_internal()

    def fail_job(self, error: str):
        with self._lock:
            self.state.is_running = False
            self.state.stage = "error"
            self.state.stage_label = "發生錯誤"
            self.state.error_message = error
            self._broadcast_internal()

    # ─── Scan job ────────────────────────────────────────────────────

    def start_scan(self, mode: str = "full"):
        label = "正在遍歷與比對相片目錄..." if mode == "full" else "正在掃描新增檔案..."
        self._start_job("scan", "discovering", label)

    def set_scan_syncing(self, total: int):
        with self._lock:
            self.state.total_files = total
            self.state.stage = "processing"
            self.state.stage_label = f"正在同步資料庫（0/{total}）..."
            self._broadcast_internal()

    def update_scan_progress(self, processed: int, current_file: str = "", new_assets: int = 0):
        if self._cancel_requested:
            raise InterruptedError("使用者已取消掃描任務")
        with self._lock:
            self.state.processed_files = processed
            self.state.current_file = current_file
            self.state.new_assets_count = new_assets
            total = max(self.state.total_files, 1)
            self.state.percentage = min(round((processed / total) * 100, 1), 99.0)
            now = time.time()
            elapsed = now - (self.state.started_at or now)
            if elapsed > 0 and processed > 0:
                speed = processed / elapsed
                self.state.speed_fps = round(speed, 1)
                remaining = max(self.state.total_files - processed, 0)
                if speed > 0:
                    self.state.eta_seconds = int(remaining / speed)
            self.state.stage_label = f"正在同步資料庫 ({processed}/{self.state.total_files})..."
            self._broadcast_internal()

    def complete_scan(self, summary: dict):
        """summary: {added, modified, missing, unchanged}"""
        added = summary.get("added", 0)
        total = sum(summary.values())
        self._complete_job(
            label=f"掃描完成！新增 {added} 個資產",
            scan_summary=summary,
            new_assets_count=added,
            processed_files=total,
            total_files=total,
        )

    # ─── Analyze job ─────────────────────────────────────────────────

    def start_analyze(self, stages: list, device: str = "auto"):
        label = f"準備分析（階段：{', '.join(stages)}）..."
        self._start_job("analyze", "pending", label)
        with self._lock:
            self.state.device_used = device

    def set_current_stage(self, stage_name: str, label: str = ""):
        with self._lock:
            self.state.current_stage = stage_name
            self.state.stage = stage_name
            self.state.stage_label = label or f"正在執行 {stage_name} 階段..."
            self.state.processed_files = 0
            self.state.total_files = 0
            self.state.percentage = 0.0
            self._broadcast_internal()

    def update_analyze_progress(self, processed: int, total: int, current_file: str = ""):
        if self._cancel_requested:
            raise InterruptedError("使用者已取消分析任務")
        with self._lock:
            self.state.processed_files = processed
            self.state.total_files = total
            self.state.current_file = current_file
            self.state.percentage = min(round((processed / max(total, 1)) * 100, 1), 99.0)
            now = time.time()
            elapsed = now - (self.state.started_at or now)
            if elapsed > 0 and processed > 0:
                speed = processed / elapsed
                self.state.speed_fps = round(speed, 1)
                remaining = max(total - processed, 0)
                if speed > 0:
                    self.state.eta_seconds = int(remaining / speed)
            self._broadcast_internal()

    def complete_analyze(self, results: dict, device_used: str = ""):
        self._complete_job(
            label="分析完成！",
            stage_stats=results,
            device_used=device_used,
        )

    # ─── Rebuild thumbs job ───────────────────────────────────────────

    def start_rebuild_thumbs(self):
        self._start_job("rebuild_thumbs", "processing", "正在補算缺少的縮圖...")

    def complete_rebuild_thumbs(self, done: int):
        self._complete_job(label=f"縮圖補算完成！共補算 {done} 張")

    # ─── Evaluate (rules engine) ─────────────────────────────────────

    def set_evaluating_rules(self):
        with self._lock:
            self.state.job_type = "evaluate"
            self.state.is_running = True
            self.state.stage = "evaluating_rules"
            self.state.stage_label = "正在執行規則引擎（識別截圖/文件/重複/廢片）..."
            self.state.percentage = 99.0
            self._broadcast_internal()

    def complete_evaluate(self, new_candidates: int):
        self._complete_job(
            label=f"評估完成！找到 {new_candidates} 個候選資產",
            new_candidates_count=new_candidates,
        )

    # ─── 向下相容（舊 scan API 使用）────────────────────────────────────

    def set_discovered_total(self, total: int):
        self.set_scan_syncing(total)

    def update_progress(self, processed: int, current_file: str = "", new_assets: int = 0):
        self.update_scan_progress(processed, current_file, new_assets)

    def complete_scan_legacy(self, new_assets: int, new_candidates: int):
        """舊版相容：完整掃描+分析+評估一體流程。"""
        self._complete_job(
            label="掃描與 AI 分析全部完成！",
            new_assets_count=new_assets,
            new_candidates_count=new_candidates,
            processed_files=self.state.total_files,
        )

    def fail_scan(self, error: str):
        self.fail_job(error)

    # ─── 取消 ────────────────────────────────────────────────────────

    def request_cancel(self):
        with self._lock:
            self._cancel_requested = True
            self.state.is_running = False
            self.state.stage = "idle"
            self.state.stage_label = "已取消"
            self._broadcast_internal()

    def is_cancel_requested(self) -> bool:
        return self._cancel_requested

    # ─── 狀態查詢 ────────────────────────────────────────────────────

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            d = self.state.model_dump()
            # 向下相容：把 is_running 也作為 is_scanning 輸出
            d["is_scanning"] = d["is_running"]
            return d

    # ─── 訂閱/廣播 ────────────────────────────────────────────────────

    def _broadcast_internal(self):
        data = self.get_state()
        for q in list(self._subscribers):
            try:
                q.put_nowait(data)
            except Exception:
                pass

    def broadcast(self):
        with self._lock:
            self._broadcast_internal()

    async def subscribe(self):
        q = asyncio.Queue()
        self._subscribers.append(q)
        try:
            yield self.get_state()
            while True:
                data = await q.get()
                yield data
        finally:
            if q in self._subscribers:
                self._subscribers.remove(q)


progress_tracker = ProgressTracker()
