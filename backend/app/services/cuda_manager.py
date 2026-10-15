import os
import sys
import re
import json
import time
import shutil
import asyncio
import logging
import threading
import subprocess
from typing import Optional, Dict, Any, List, Tuple

logger = logging.getLogger(__name__)

NVIDIA_SMI_CANDIDATE_PATHS = [
    "nvidia-smi",
    r"C:\Windows\System32\nvidia-smi.exe",
    r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
]

# PyTorch wheel 變體與所需的最低 CUDA 驅動版本（nvidia-smi 顯示的 "CUDA Version"）
TORCH_VARIANTS: List[Tuple[str, float]] = [
    ("cu128", 12.8),
    ("cu126", 12.6),
    ("cu124", 12.4),
    ("cu121", 12.1),
    ("cu118", 11.8),
]
VALID_VARIANTS = {v for v, _ in TORCH_VARIANTS}

_TORCH_PROBE_CODE = (
    "import json,torch;"
    "ok=torch.cuda.is_available();"
    "print(json.dumps({'version':torch.__version__,'cuda_ok':ok,'cuda_ver':torch.version.cuda,"
    "'name':torch.cuda.get_device_name(0) if ok else ''}))"
)


def _is_blackwell(gpu_name: str) -> bool:
    """RTX 50 系列（Blackwell, sm_120）只有 cu128 以上的 wheel 支援。"""
    return bool(re.search(r"RTX\s*50\d\d", gpu_name or "", re.IGNORECASE))


def _parse_cuda(ver: str) -> float:
    try:
        return float(re.match(r"(\d+\.\d+)", ver).group(1))
    except Exception:
        return 0.0


def recommend_variant(gpu_name: str, max_cuda: str) -> str:
    if _is_blackwell(gpu_name):
        return "cu128"
    driver = _parse_cuda(max_cuda)
    for variant, need in TORCH_VARIANTS:
        if driver == 0.0 or driver >= need:
            return variant
    return "cu118"


class CudaManager:
    """Windows + NVIDIA GPU 硬體檢測與 PyTorch CUDA 安裝管理器。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._install_process: Optional[subprocess.Popen] = None
        self._cancel_requested = False
        self._subscribers: List[Tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._cached_hardware_info: Optional[Dict[str, Any]] = None
        self._last_hw_check = 0.0
        self._cached_torch_info: Optional[Dict[str, Any]] = None
        self._last_torch_check = 0.0
        self.install_state = self._idle_state()

    @staticmethod
    def _idle_state() -> Dict[str, Any]:
        return {
            "status": "idle", "percentage": 0.0, "message": "閒置中", "current_step": "",
            "downloaded_bytes": 0, "total_bytes": 0, "speed": "", "logs": [], "error": None,
            "started_at": None, "finished_at": None, "variant": None,
        }

    # ─── 1. 硬體檢測 ─────────────────────────────────────────────
    def _find_nvidia_smi(self) -> Optional[str]:
        for candidate in NVIDIA_SMI_CANDIDATE_PATHS:
            if shutil.which(candidate) or (os.path.isabs(candidate) and os.path.exists(candidate)):
                return candidate
        return None

    def _detect_nvidia_gpu_native(self) -> Dict[str, Any]:
        none = {"has_nvidia": False, "gpu_name": "未檢測到相容 GPU", "driver": "", "max_cuda": "", "vram_mb": 0}
        smi_path = self._find_nvidia_smi()
        if smi_path:
            try:
                res = subprocess.run(
                    [smi_path, "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=5, check=False)
                if res.returncode == 0 and res.stdout.strip():
                    first = res.stdout.strip().splitlines()[0].split(",")
                    gpu_name = first[0].strip() if first else "NVIDIA GPU"
                    driver_ver = first[1].strip() if len(first) > 1 else ""
                    vram_mb = int(first[2].strip()) if len(first) > 2 and first[2].strip().isdigit() else 0
                    res_smi = subprocess.run([smi_path], capture_output=True, text=True, timeout=5, check=False)
                    m = re.search(r"CUDA Version:\s*([\d\.]+)", res_smi.stdout or "")
                    return {"has_nvidia": True, "gpu_name": gpu_name, "driver": driver_ver,
                            "max_cuda": m.group(1) if m else "", "vram_mb": vram_mb}
            except Exception as e:
                logger.debug(f"nvidia-smi 查詢失敗：{e}")
        try:
            if sys.platform == "win32":
                wmi = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"],
                    capture_output=True, text=True, timeout=5, check=False)
                for line in (wmi.stdout or "").splitlines():
                    if any(k in line.lower() for k in ("nvidia", "geforce", "rtx", "gtx")):
                        # WMI 拿不到可靠的顯存與 CUDA 版本，回報未知而不是猜
                        return {"has_nvidia": True, "gpu_name": line.strip(), "driver": "",
                                "max_cuda": "", "vram_mb": 0}
        except Exception:
            pass
        return none

    def _torch_info(self, force: bool = False) -> Dict[str, Any]:
        """
        取得 PyTorch 版本與 CUDA 可用性。
        若本程序尚未載入 torch，改在子程序中查詢，避免 torch 的 DLL 被本程序鎖住
        （Windows 上會導致之後無法升級 / 安裝 CUDA 版 PyTorch）。
        """
        if "torch" in sys.modules:
            torch = sys.modules["torch"]
            try:
                ok = bool(torch.cuda.is_available())
                return {"version": getattr(torch, "__version__", "unknown"), "cuda_ok": ok,
                        "cuda_ver": getattr(torch.version, "cuda", None),
                        "name": torch.cuda.get_device_name(0) if ok else "", "loaded": True}
            except Exception:
                pass
        now = time.time()
        if not force and self._cached_torch_info and now - self._last_torch_check < 60:
            return self._cached_torch_info
        info = {"version": "未安裝", "cuda_ok": False, "cuda_ver": None, "name": "", "loaded": False}
        try:
            res = subprocess.run([sys.executable, "-c", _TORCH_PROBE_CODE],
                                 capture_output=True, text=True, timeout=60, check=False)
            if res.returncode == 0 and res.stdout.strip():
                info.update(json.loads(res.stdout.strip().splitlines()[-1]))
        except Exception as e:
            logger.debug(f"PyTorch 檢測失敗：{e}")
        self._cached_torch_info, self._last_torch_check = info, now
        return info

    def get_cuda_status(self, force_refresh=False) -> Dict[str, Any]:
        now = time.time()
        if not self._cached_hardware_info or force_refresh or (now - self._last_hw_check > 10.0):
            self._cached_hardware_info = self._detect_nvidia_gpu_native()
            self._last_hw_check = now
        hw = self._cached_hardware_info
        t = self._torch_info(force=force_refresh)
        if not hw["has_nvidia"]:
            code, desc = "no_nvidia_gpu", "未檢測到相容 GPU（純 CPU 運算模式）"
        elif t["cuda_ok"]:
            code, desc = "cuda_ready", f"GPU 加速已就緒 ({hw['gpu_name']} / CUDA {t['cuda_ver'] or '?'})"
        else:
            code, desc = "gpu_detected_need_cuda", f"偵測到 {hw['gpu_name']}，目前為 CPU 版 PyTorch，可一鍵啟用 GPU 加速"
        return {
            "has_nvidia_gpu": hw["has_nvidia"],
            "gpu_name": hw["gpu_name"],
            "driver_version": hw["driver"],
            "max_cuda_version": hw["max_cuda"],
            "vram_mb": hw["vram_mb"],
            "torch_version": t["version"],
            "torch_cuda_available": t["cuda_ok"],
            "torch_cuda_version": t["cuda_ver"],
            "torch_loaded_in_process": t.get("loaded", False),
            "is_ready": t["cuda_ok"],
            "status_code": code,
            "status_desc": desc,
            "recommended_variant": recommend_variant(hw["gpu_name"], hw["max_cuda"]),
            "install_state": self.get_install_state(),
        }

    # ─── 2. 背景安裝 ─────────────────────────────────────────────
    def get_install_state(self) -> Dict[str, Any]:
        with self._lock:
            d = dict(self.install_state)
            d["logs"] = list(d["logs"])
            return d

    def _broadcast(self):
        state = self.get_install_state()
        for loop, q in list(self._subscribers):
            try:
                loop.call_soon_threadsafe(_publish_latest, q, state)
            except RuntimeError:
                pass

    def _update_state(self, **kwargs):
        with self._lock:
            self.install_state.update(kwargs)
        self._broadcast()

    def _add_log(self, line: str):
        with self._lock:
            logs = self.install_state["logs"]
            logs.append(line.rstrip())
            if len(logs) > 300:
                logs.pop(0)
        self._broadcast()

    def resolve_variant(self, requested: str) -> Tuple[str, Optional[str]]:
        """回傳 (實際使用的變體, 提示訊息)。RTX 50 系列一律改用 cu128。"""
        hw = self._cached_hardware_info or self._detect_nvidia_gpu_native()
        recommended = recommend_variant(hw["gpu_name"], hw["max_cuda"])
        if not requested or requested == "auto":
            return recommended, None
        if requested not in VALID_VARIANTS:
            raise ValueError(f"不支援的 CUDA 變體：{requested}")
        if _is_blackwell(hw["gpu_name"]) and requested != "cu128":
            return "cu128", f"{hw['gpu_name']} 需要 CUDA 12.8 版 PyTorch，已自動由 {requested} 改為 cu128"
        return requested, None

    def start_cuda_install(self, variant: str = "auto") -> Dict[str, Any]:
        if "torch" in sys.modules:
            return {"status": "restart_required",
                    "message": "PyTorch 已在後端執行中被載入（例如剛跑過 CLIP 分析），"
                               "Windows 會鎖住其檔案。請先重新啟動後端，再執行一鍵安裝。"}
        variant, note = self.resolve_variant(variant)
        with self._lock:
            if self.install_state["status"] in ("downloading", "installing", "verifying"):
                return {"status": "busy", "message": "已有安裝任務進行中"}
            self._cancel_requested = False
            self.install_state = self._idle_state()
            self.install_state.update({
                "status": "downloading", "percentage": 2.0, "variant": variant,
                "message": f"正在準備下載 PyTorch ({variant})...", "current_step": "連接下載伺服器",
                "logs": [f"🚀 啟動 PyTorch CUDA ({variant}) 安裝程序..."] + ([f"ℹ️ {note}"] if note else []),
                "started_at": time.time(),
            })
        self._broadcast()
        threading.Thread(target=self._run_install_worker, args=(variant,), daemon=True).start()
        return {"status": "ok", "variant": variant, "message": note or f"已啟動 PyTorch CUDA ({variant}) 安裝"}

    def cancel_cuda_install(self):
        with self._lock:
            self._cancel_requested = True
            if self._install_process and self._install_process.poll() is None:
                try:
                    self._install_process.terminate()
                except Exception:
                    pass
            self.install_state["status"] = "cancelled"
            self.install_state["message"] = "使用者已取消安裝"
        self._broadcast()

    def _run_install_worker(self, variant: str):
        python_exe = sys.executable
        cmd = [python_exe, "-m", "pip", "install", "torch", "torchvision",
               "--index-url", f"https://download.pytorch.org/whl/{variant}",
               "--upgrade", "--progress-bar", "on"]
        self._add_log(f"執行命令：{' '.join(cmd)}")
        try:
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            self._install_process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=env)
            for line in iter(self._install_process.stdout.readline, ""):
                if self._cancel_requested:
                    break
                clean = line.strip()
                if not clean:
                    continue
                self._add_log(clean)
                m = re.search(r"(\d{1,3})%\s*\|", clean)
                if m:
                    pct = float(m.group(1))
                    sm = re.search(r"([\d\.]+\s*[kMG]B/s)", clean)
                    speed = sm.group(1) if sm else ""
                    self._update_state(status="downloading", percentage=min(round(5.0 + pct * 0.8, 1), 85.0),
                                       message=f"正在下載 PyTorch CUDA 套件... ({pct:.0f}%)",
                                       current_step=f"下載中 {speed}", speed=speed)
                    continue
                if "Installing collected packages" in clean:
                    self._update_state(status="installing", percentage=88.0,
                                       message="下載完成，正在安裝 PyTorch...", current_step="安裝套件中")
                elif "Successfully installed" in clean:
                    self._update_state(status="verifying", percentage=95.0,
                                       message="安裝完成，正在驗證 CUDA...", current_step="校驗 CUDA 環境")
            self._install_process.wait()
            ret = self._install_process.returncode
            if self._cancel_requested:
                self._update_state(status="cancelled", message="安裝已被使用者中斷", finished_at=time.time())
                return
            if ret != 0:
                self._update_state(status="failed", message=f"安裝失敗 (結束代碼 {ret})，請檢視日誌",
                                   error=f"pip exit code {ret}", finished_at=time.time())
                return
            self._add_log("🔍 正在以獨立子程序驗證 PyTorch CUDA...")
            info = self._torch_info(force=True)
            if info.get("cuda_ok"):
                self._add_log(f"🎉 驗證成功：torch {info.get('version')} / {info.get('name')}")
                self.get_cuda_status(force_refresh=True)
                try:
                    from app.stubs.clip_stub import clip_extractor
                    clip_extractor.reset()
                except Exception:
                    pass
                self._update_state(status="success", percentage=100.0,
                                   message="🎉 GPU (CUDA) 加速已成功啟用！", current_step="完成",
                                   finished_at=time.time())
            else:
                self._add_log("⚠️ 驗證未通過：安裝後仍無法使用 CUDA（請確認顯示卡驅動版本）")
                self._update_state(status="failed", percentage=100.0,
                                   message="套件已安裝，但 CUDA 仍不可用。請更新 NVIDIA 驅動後重新啟動後端。",
                                   error="cuda not available after install", finished_at=time.time())
        except Exception as exc:
            logger.error(f"CUDA 安裝程序異常：{exc}", exc_info=True)
            self._add_log(f"❌ 發生異常：{exc}")
            self._update_state(status="failed", message=f"安裝發生錯誤：{exc}", error=str(exc),
                               finished_at=time.time())

    async def subscribe(self):
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue(maxsize=1)
        entry = (loop, q)
        self._subscribers.append(entry)
        try:
            yield self.get_install_state()
            while True:
                yield await q.get()
        finally:
            try:
                self._subscribers.remove(entry)
            except ValueError:
                pass


def _publish_latest(q: asyncio.Queue, data):
    try:
        while not q.empty():
            q.get_nowait()
        q.put_nowait(data)
    except Exception:
        pass


cuda_manager = CudaManager()
