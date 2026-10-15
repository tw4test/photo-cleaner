"""
全域任務進度追蹤器（掃描 / 分析 / 補算縮圖 / 評估共用）。v0.5
"""
import asyncio
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel

_BROADCAST_MIN_INTERVAL = 0.2


class StageStats(BaseModel):
    pending: int = 0
    done: int = 0
    failed: int = 0
    stale: int = 0


class ProgressState(BaseModel):
    is_running: bool = False
    job_type: str = "idle"
    stage: str = "idle"
    stage_label: str = "閒置中"
    current_stage: str = ""
    device_used: str = ""
    current_runners: Optional[int] = None
    batch_size: Optional[int] = None
    total_files: int = 0
    processed_files: int = 0
    percentage: float = 0.0
    current_file: str = ""
    speed_fps: float = 0.0
    eta_seconds: Optional[int] = None
    new_assets_count: int = 0
    new_candidates_count: int = 0
    scan_summary: Dict[str, int] = {}
    stage_stats: Dict[str, Any] = {}
    error_message: Optional[str] = None
    cancel_requested: bool = False
    cancellable: bool = True          # 評估（規則引擎）一次完成、無法中途停止 → False
    pipeline_step: str = ""           # 一鍵流水線目前步驟：scan | analyze | evaluate
    evaluate_result: Dict[str, Any] = {}   # {skipped, added, removed, total}
    started_at: Optional[float] = None
    stage_started_at: Optional[float] = None
    finished_at: Optional[float] = None
    duration_seconds: float = 0.0


def _publish_latest(q: asyncio.Queue, data: Dict[str, Any]) -> None:
    try:
        while not q.empty():
            q.get_nowait()
        q.put_nowait(data)
    except Exception:
        pass


class ProgressTracker:
    def __init__(self):
        self.state = ProgressState()
        self._subscribers: List[Tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._cancel_requested = False
        self._lock = threading.RLock()
        self._last_broadcast = 0.0

    def try_start(self, job_type: str, stage: str, label: str, **fields) -> bool:
        with self._lock:
            if self.state.is_running:
                return False
            self._cancel_requested = False
            now = time.time()
            self.state = ProgressState(is_running=True, job_type=job_type, stage=stage, stage_label=label,
                                       started_at=now, stage_started_at=now)
            for k, v in fields.items():
                if hasattr(self.state, k):
                    setattr(self.state, k, v)
            self._broadcast_internal(force=True)
            return True

    def try_start_scan(self, mode: str = "full") -> bool:
        label = "正在遍歷與比對相片目錄..." if mode == "full" else "正在掃描新增檔案..."
        return self.try_start("scan", "discovering", label)

    def try_start_analyze(self, stages: list, device: str = "auto") -> bool:
        names = {"hash": "檔案雜湊", "basic": "基本特徵與縮圖", "clip": "CLIP 語義向量", "video": "影片資訊"}
        friendly = [names.get(s, s) for s in stages]
        return self.try_start("analyze", "pending", f"準備執行特徵分析（階段：{', '.join(friendly)}）...",
                              device_used=device)

    def try_start_rebuild_thumbs(self) -> bool:
        return self.try_start("rebuild_thumbs", "processing", "正在補算缺少的縮圖...")

    def try_start_evaluate(self) -> bool:
        return self.try_start("evaluate", "evaluating_rules", "正在執行規則引擎（識別截圖/文件/重複/廢片）...",
                              percentage=99.0, cancellable=False)

    def try_start_pipeline(self) -> bool:
        return self.try_start("pipeline", "discovering", "一鍵流水線：準備掃描...", pipeline_step="scan")

    def set_pipeline_step(self, step: str, label: str):
        with self._lock:
            self.state.pipeline_step = step
            self.state.stage_label = label
            self.state.cancellable = step != "evaluate"
            self._broadcast_internal(force=True)

    def _start_job(self, job_type: str, stage: str, label: str):
        if not self.try_start(job_type, stage, label):
            raise RuntimeError("目前已有任務進行中")

    def start_scan(self, mode: str = "full"):
        if not self.try_start_scan(mode):
            raise RuntimeError("目前已有任務進行中")

    def start_analyze(self, stages: list, device: str = "auto"):
        if not self.try_start_analyze(stages, device):
            raise RuntimeError("目前已有任務進行中")

    def start_rebuild_thumbs(self):
        if not self.try_start_rebuild_thumbs():
            raise RuntimeError("目前已有任務進行中")

    def set_evaluating_rules(self):
        with self._lock:
            self.state.cancellable = False
            self.state.stage = "evaluating_rules"
            self.state.stage_label = "正在執行規則引擎（識別截圖/文件/重複/廢片）..."
            self.state.percentage = 99.0
            self._broadcast_internal(force=True)

    def _complete_job(self, label: str = "完成！", **extra):
        with self._lock:
            now = time.time()
            start = self.state.started_at or now
            self.state.is_running = False
            if self._cancel_requested:
                self.state.stage = "idle"
                self.state.stage_label = "已取消"
            else:
                self.state.stage = "completed"
                self.state.stage_label = label
                self.state.percentage = 100.0
            self.state.finished_at = now
            self.state.duration_seconds = round(now - start, 1)
            for k, v in extra.items():
                if hasattr(self.state, k):
                    setattr(self.state, k, v)
            self._broadcast_internal(force=True)

    def fail_job(self, error: str):
        with self._lock:
            self.state.is_running = False
            self.state.stage = "error"
            self.state.stage_label = "發生錯誤"
            self.state.error_message = error
            self.state.finished_at = time.time()
            self._broadcast_internal(force=True)

    def finish_cancelled(self):
        with self._lock:
            self.state.is_running = False
            self.state.stage = "idle"
            self.state.stage_label = "已取消"
            self.state.finished_at = time.time()
            self._broadcast_internal(force=True)

    def ensure_finished(self):
        with self._lock:
            if self.state.is_running:
                if self._cancel_requested:
                    self.finish_cancelled()
                else:
                    self.fail_job("任務意外中止")

    def set_scan_syncing(self, total: int):
        with self._lock:
            self.state.total_files = total
            self.state.stage = "processing"
            self.state.stage_label = f"正在同步資料庫（0/{total}）..."
            self._broadcast_internal(force=True)

    def update_scan_progress(self, processed: int, current_file: str = "", new_assets: int = 0):
        if self._cancel_requested:
            raise InterruptedError("使用者已取消掃描任務")
        with self._lock:
            self.state.processed_files = processed
            self.state.current_file = current_file
            self.state.new_assets_count = new_assets
            self._update_rate(processed, self.state.total_files)
            self.state.stage_label = f"正在同步資料庫 ({processed}/{self.state.total_files})..."
            self._broadcast_internal()

    def complete_scan(self, summary: dict):
        added = summary.get("added", 0)
        total = sum(summary.values())
        self._complete_job(label=f"掃描完成！新增 {added} 個資產", scan_summary=summary,
                           new_assets_count=added, processed_files=total, total_files=total)

    def set_current_stage(self, stage_name: str, total: int = 0, label: str = "", runners: Optional[int] = None):
        labels = {
            "hash": "正在計算檔案 SHA-256 雜湊 (Hash 階段)...",
            "basic": "正在生成縮圖與計算相片特徵 (Basic 階段)...",
            "clip": "正在執行 AI 語義向量提取與零樣本分類 (CLIP 階段)...",
            "video": "正在解析影片資訊 (Video 階段)...",
        }
        with self._lock:
            self.state.current_stage = stage_name
            self.state.stage = stage_name
            self.state.stage_label = label or labels.get(stage_name, f"正在執行 {stage_name} 階段...")
            if runners is not None:
                self.state.current_runners = runners
            self.state.processed_files = 0
            self.state.total_files = total
            self.state.percentage = 0.0
            self.state.speed_fps = 0.0
            self.state.eta_seconds = None
            self.state.stage_started_at = time.time()
            self._broadcast_internal(force=True)

    def set_stage_info(self, **fields):
        with self._lock:
            for k, v in fields.items():
                if hasattr(self.state, k):
                    setattr(self.state, k, v)
            self._broadcast_internal(force=True)

    def update_analyze_progress(self, processed: int, total: int, current_file: str = ""):
        if self._cancel_requested:
            raise InterruptedError("使用者已取消分析任務")
        with self._lock:
            self.state.processed_files = processed
            self.state.total_files = total
            self.state.current_file = current_file
            self._update_rate(processed, total)
            self._broadcast_internal()

    def update(self, count: int = 1, current_file: str = ""):
        with self._lock:
            self.state.processed_files += count
            if current_file:
                self.state.current_file = current_file
            self._update_rate(self.state.processed_files, self.state.total_files)
            self._broadcast_internal()

    def complete_analyze(self, results: dict, device_used: str = ""):
        self._complete_job(label="分析完成！", stage_stats=results, device_used=device_used)

    def complete_rebuild_thumbs(self, done: int):
        self._complete_job(label=f"縮圖補算完成！共補算 {done} 張")

    def complete_evaluate(self, new_candidates: int, result: Optional[Dict[str, Any]] = None):
        result = result or {}
        if result.get("skipped"):
            label = f"評估結果已是最新，略過（共 {new_candidates} 個候選）"
        elif result:
            label = (f"評估完成！共 {new_candidates} 個候選（新增 {result.get('added', 0)}、"
                     f"移除 {result.get('removed', 0)}）")
        else:
            label = f"評估完成！找到 {new_candidates} 個候選資產"
        self._complete_job(label=label, new_candidates_count=new_candidates, evaluate_result=result)

    def complete_pipeline(self, scan_summary: dict, stage_stats: dict, new_candidates: int, result: dict):
        self._complete_job(
            label=(f"流水線完成！新增 {scan_summary.get('added', 0)} 個資產，共 {new_candidates} 個候選"
                   f"（新增 {result.get('added', 0)}、移除 {result.get('removed', 0)}）"),
            scan_summary=scan_summary, stage_stats=stage_stats, new_candidates_count=new_candidates,
            evaluate_result=result, pipeline_step="done")

    def set_discovered_total(self, total: int):
        self.set_scan_syncing(total)

    def update_progress(self, processed: int, current_file: str = "", new_assets: int = 0):
        self.update_scan_progress(processed, current_file, new_assets)

    def fail_scan(self, error: str):
        self.fail_job(error)

    def request_cancel(self) -> bool:
        """設定取消旗標。評估步驟無法中途停止，此時不接受取消並回傳 False（F-12）。"""
        with self._lock:
            if not self.state.is_running:
                return False
            if not self.state.cancellable:
                return False
            self._cancel_requested = True
            self.state.cancel_requested = True
            self.state.stage_label = "取消中，等待目前工作完成..."
            self._broadcast_internal(force=True)
            return True

    def is_cancel_requested(self) -> bool:
        return self._cancel_requested

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            d = self.state.model_dump()
            d["is_scanning"] = d["is_running"]
            return d

    def _update_rate(self, processed: int, total: int):
        total = max(total, 1)
        self.state.percentage = min(round((processed / total) * 100, 1), 99.0)
        now = time.time()
        start = self.state.stage_started_at or self.state.started_at or now
        elapsed = now - start
        if elapsed > 0 and processed > 0:
            speed = processed / elapsed
            self.state.speed_fps = round(speed, 1)
            remaining = max(total - processed, 0)
            if speed > 0:
                self.state.eta_seconds = int(remaining / speed)

    def _broadcast_internal(self, force: bool = False):
        now = time.time()
        if not force and now - self._last_broadcast < _BROADCAST_MIN_INTERVAL:
            return
        self._last_broadcast = now
        data = self.get_state()
        for loop, q in list(self._subscribers):
            try:
                loop.call_soon_threadsafe(_publish_latest, q, data)
            except RuntimeError:
                try:
                    self._subscribers.remove((loop, q))
                except ValueError:
                    pass

    def broadcast(self):
        with self._lock:
            self._broadcast_internal(force=True)

    async def subscribe(self):
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue(maxsize=1)
        entry = (loop, q)
        self._subscribers.append(entry)
        try:
            yield self.get_state()
            while True:
                yield await q.get()
        finally:
            try:
                self._subscribers.remove(entry)
            except ValueError:
                pass


progress_tracker = ProgressTracker()
