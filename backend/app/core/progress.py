import time
import asyncio
from typing import Optional, Dict, Any, List
from pydantic import BaseModel

class ProgressState(BaseModel):
    is_scanning: bool = False
    stage: str = "idle"  # idle | discovering | processing | evaluating_rules | completed | error
    stage_label: str = "閒置中"
    total_files: int = 0
    processed_files: int = 0
    percentage: float = 0.0
    current_file: str = ""
    speed_fps: float = 0.0
    eta_seconds: Optional[int] = None
    new_assets_count: int = 0
    new_candidates_count: int = 0
    error_message: Optional[str] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    duration_seconds: float = 0.0

class ProgressTracker:
    def __init__(self):
        self.state = ProgressState()
        self._subscribers: List[asyncio.Queue] = []
        self._cancel_requested = False

    def start_scan(self):
        self._cancel_requested = False
        self.state = ProgressState(
            is_scanning=True,
            stage="discovering",
            stage_label="正在遍歷與檢查相片目錄...",
            started_at=time.time(),
        )
        self.broadcast()

    def set_discovered_total(self, total: int):
        self.state.total_files = total
        self.state.stage = "processing"
        self.state.stage_label = f"正在分析特徵與生成縮圖 (0/{total})..."
        self.broadcast()

    def update_progress(self, processed: int, current_file: str = "", new_assets: int = 0):
        if self._cancel_requested:
            raise InterruptedError("使用者已取消掃描任務")

        self.state.processed_files = processed
        self.state.current_file = current_file
        self.state.new_assets_count = new_assets

        total = max(self.state.total_files, 1)
        self.state.percentage = min(round((processed / total) * 100, 1), 99.0)

        # 計算處理速度與預估剩餘時間
        now = time.time()
        elapsed = now - (self.state.started_at or now)
        if elapsed > 0 and processed > 0:
            speed = processed / elapsed
            self.state.speed_fps = round(speed, 1)
            remaining = max(self.state.total_files - processed, 0)
            if speed > 0:
                self.state.eta_seconds = int(remaining / speed)
        
        self.state.stage_label = f"正在分析相片特徵 ({processed}/{self.state.total_files})..."
        self.broadcast()

    def set_evaluating_rules(self):
        self.state.stage = "evaluating_rules"
        self.state.stage_label = "正在執行 AI 規則引擎（識別截圖/文件/重複/廢片）..."
        self.state.percentage = 99.0
        self.broadcast()

    def complete_scan(self, new_assets: int, new_candidates: int):
        now = time.time()
        start = self.state.started_at or now
        self.state.is_scanning = False
        self.state.stage = "completed"
        self.state.stage_label = "掃描與 AI 分析全部完成！"
        self.state.percentage = 100.0
        self.state.processed_files = self.state.total_files
        self.state.new_assets_count = new_assets
        self.state.new_candidates_count = new_candidates
        self.state.finished_at = now
        self.state.duration_seconds = round(now - start, 1)
        self.state.current_file = ""
        self.broadcast()

    def fail_scan(self, error: str):
        self.state.is_scanning = False
        self.state.stage = "error"
        self.state.stage_label = "掃描過程發生錯誤"
        self.state.error_message = error
        self.broadcast()

    def request_cancel(self):
        self._cancel_requested = True
        self.state.is_scanning = False
        self.state.stage = "idle"
        self.state.stage_label = "掃描已取消"
        self.broadcast()

    def is_cancel_requested(self) -> bool:
        return self._cancel_requested

    def get_state(self) -> Dict[str, Any]:
        return self.state.model_dump()

    def broadcast(self):
        # Notify SSE subscribers
        data = self.get_state()
        for q in list(self._subscribers):
            try:
                q.put_nowait(data)
            except Exception:
                pass

    async def subscribe(self):
        q = asyncio.Queue()
        self._subscribers.append(q)
        try:
            # Send initial state immediately
            yield self.get_state()
            while True:
                data = await q.get()
                yield data
        finally:
            if q in self._subscribers:
                self._subscribers.remove(q)

progress_tracker = ProgressTracker()
