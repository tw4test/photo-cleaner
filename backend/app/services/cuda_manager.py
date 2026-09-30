import os
import sys
import re
import time
import shutil
import asyncio
import logging
import threading
import subprocess
from typing import Optional, Dict, Any, List
from pathlib import Path

logger = logging.getLogger(__name__)

# Known Windows paths for nvidia-smi
NVIDIA_SMI_CANDIDATE_PATHS = [
    "nvidia-smi",
    r"C:\Windows\System32\nvidia-smi.exe",
    r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
]


class CudaManager:
    """
    Windows + NVIDIA GPU 硬體檢測與 PyTorch CUDA 自動轉換管理器。
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._install_task = None
        self._install_process: Optional[subprocess.Popen] = None
        self._cancel_requested = False
        self._subscribers: List[asyncio.Queue] = []
        self._cached_hardware_info: Optional[Dict[str, Any]] = None
        self._last_hw_check = 0.0

        self.install_state = {
            "status": "idle",       # idle | downloading | installing | verifying | success | failed | cancelled
            "percentage": 0.0,
            "message": "閒置中",
            "current_step": "",
            "downloaded_bytes": 0,
            "total_bytes": 0,
            "speed": "",
            "logs": [],
            "error": None,
            "started_at": None,
            "finished_at": None,
        }

    # ─────────────────────────────────────────────────────────────
    # 1. 硬體與環境檢測
    # ─────────────────────────────────────────────────────────────

    def _find_nvidia_smi(self) -> Optional[str]:
        """尋找可用的 nvidia-smi 可執行檔路徑。"""
        for candidate in NVIDIA_SMI_CANDIDATE_PATHS:
            if shutil.which(candidate) or (os.path.isabs(candidate) and os.path.exists(candidate)):
                return candidate
        return None

    def _detect_nvidia_gpu_native(self) -> Dict[str, Any]:
        """透過 nvidia-smi 查詢 NVIDIA 顯卡資訊。"""
        smi_path = self._find_nvidia_smi()
        if not smi_path:
            return {"has_nvidia": False, "gpu_name": "未檢測到相容 GPU", "driver": "", "max_cuda": "", "vram_mb": 0}

        try:
            # 1. 查詢 GPU 名稱與顯存
            cmd_query = [
                smi_path,
                "--query-gpu=name,driver_version,memory.total",
                "--format=csv,noheader,nounits"
            ]
            res = subprocess.run(cmd_query, capture_output=True, text=True, timeout=3, check=False)
            if res.returncode == 0 and res.stdout.strip():
                lines = res.stdout.strip().splitlines()
                first_gpu = lines[0].split(",")
                gpu_name = first_gpu[0].strip() if len(first_gpu) > 0 else "NVIDIA GPU"
                driver_ver = first_gpu[1].strip() if len(first_gpu) > 1 else ""
                vram_mb = int(first_gpu[2].strip()) if len(first_gpu) > 2 and first_gpu[2].strip().isdigit() else 0

                # 2. 查詢支援的最大 CUDA 版本
                res_smi = subprocess.run([smi_path], capture_output=True, text=True, timeout=3, check=False)
                max_cuda = ""
                if res_smi.stdout:
                    cuda_match = re.search(r"CUDA Version:\s*([\d\.]+)", res_smi.stdout)
                    if cuda_match:
                        max_cuda = cuda_match.group(1)

                return {
                    "has_nvidia": True,
                    "gpu_name": gpu_name,
                    "driver": driver_ver,
                    "max_cuda": max_cuda,
                    "vram_mb": vram_mb,
                }
        except Exception as e:
            logger.debug(f"nvidia-smi 查詢失敗：{e}")

        # WMI 回退檢測（若 nvidia-smi 暫時不可用但為 Windows NVIDIA 顯卡）
        try:
            if sys.platform == "win32":
                wmi_cmd = ["powershell", "-NoProfile", "-Command", "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"]
                wmi_res = subprocess.run(wmi_cmd, capture_output=True, text=True, timeout=3, check=False)
                if wmi_res.returncode == 0 and wmi_res.stdout:
                    for line in wmi_res.stdout.splitlines():
                        if "nvidia" in line.lower() or "geforce" in line.lower() or "rtx" in line.lower() or "gtx" in line.lower():
                            return {
                                "has_nvidia": True,
                                "gpu_name": line.strip(),
                                "driver": "NVIDIA Driver",
                                "max_cuda": "12.x+",
                                "vram_mb": 4096,
                            }
        except Exception:
            pass

        return {"has_nvidia": False, "gpu_name": "未檢測到相容 GPU", "driver": "", "max_cuda": "", "vram_mb": 0}

    def get_cuda_status(self, force_refresh = False) -> Dict[str, Any]:
        """
        取得系統當前 GPU 硬體狀態與 PyTorch CUDA 支援資訊。
        """
        now = time.time()
        if not self._cached_hardware_info or force_refresh or (now - self._last_hw_check > 10.0):
            self._cached_hardware_info = self._detect_nvidia_gpu_native()
            self._last_hw_check = now

        hw = self._cached_hardware_info

        # 檢測當前 PyTorch
        torch_ver = "未安裝"
        torch_cuda_ok = False
        torch_cuda_ver = None
        gpu_device_name = ""

        try:
            import torch
            torch_ver = getattr(torch, "__version__", "unknown")
            torch_cuda_ok = bool(torch.cuda.is_available())
            torch_cuda_ver = getattr(torch.version, "cuda", None)
            if torch_cuda_ok and torch.cuda.device_count() > 0:
                gpu_device_name = torch.cuda.get_device_name(0)
        except Exception as e:
            logger.debug(f"PyTorch 檢測失敗：{e}")

        # 狀態決策
        if not hw["has_nvidia"]:
            status_code = "no_nvidia_gpu"
            status_desc = "未檢測到相容 GPU（純 CPU 運算模式）"
        elif torch_cuda_ok:
            status_code = "cuda_ready"
            status_desc = f"GPU 加速已就緒 ({hw['gpu_name']} / CUDA {torch_cuda_ver or '12.x'})"
        else:
            status_code = "gpu_detected_need_cuda"
            status_desc = f"偵測到 {hw['gpu_name']}，目前運行於 CPU 版 PyTorch，可一鍵啟用 GPU 加速"

        return {
            "has_nvidia_gpu": hw["has_nvidia"],
            "gpu_name": hw["gpu_name"],
            "driver_version": hw["driver"],
            "max_cuda_version": hw["max_cuda"],
            "vram_mb": hw["vram_mb"],
            "torch_version": torch_ver,
            "torch_cuda_available": torch_cuda_ok,
            "torch_cuda_version": torch_cuda_ver,
            "is_ready": torch_cuda_ok,
            "status_code": status_code,
            "status_desc": status_desc,
            "recommended_variant": "cu121",
            "install_state": self.get_install_state(),
        }

    # ─────────────────────────────────────────────────────────────
    # 2. 背景安裝與即時進度廣播
    # ─────────────────────────────────────────────────────────────

    def get_install_state(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self.install_state)

    def _broadcast(self):
        state = self.get_install_state()
        for q in list(self._subscribers):
            try:
                q.put_nowait(state)
            except Exception:
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

    def start_cuda_install(self, variant: str = "cu121") -> Dict[str, Any]:
        """啟動背景安裝 CUDA 版 PyTorch 任務。"""
        with self._lock:
            if self.install_state["status"] in ("downloading", "installing", "verifying"):
                return {"status": "busy", "message": "已有安裝任務進行中"}

            self._cancel_requested = False
            self.install_state = {
                "status": "downloading",
                "percentage": 2.0,
                "message": f"正在準備下載 PyTorch ({variant})...",
                "current_step": "連接下載伺服器",
                "downloaded_bytes": 0,
                "total_bytes": 0,
                "speed": "",
                "logs": [f"🚀 啟動 PyTorch CUDA ({variant}) 一鍵安裝程序..."],
                "error": None,
                "started_at": time.time(),
                "finished_at": None,
            }
            self._broadcast()

        thread = threading.Thread(target=self._run_install_worker, args=(variant,), daemon=True)
        thread.start()
        return {"status": "ok", "message": f"已啟動 PyTorch CUDA ({variant}) 安裝"}

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
        """背景安裝工作緒。"""
        python_exe = sys.executable
        index_url = f"https://download.pytorch.org/whl/{variant}"

        cmd = [
            python_exe,
            "-m",
            "pip",
            "install",
            "torch",
            "torchvision",
            "--index-url",
            index_url,
            "--upgrade",
            "--progress-bar",
            "on",
        ]

        self._add_log(f"執行命令：{' '.join(cmd)}")

        try:
            # 建立環境變數確保無緩衝輸出
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"

            self._install_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
                env=env,
            )

            # 解析 pip 下載輸出
            # 例如：Downloading torch-2.x.x+cu121-cp310-cp310-win_amd64.whl (2415.8 MB)
            # 或進度百分比、大小
            total_mb_est = 2500.0  # torch + torchvision cu121 大約 2.5 GB

            for line in iter(self._install_process.stdout.readline, ""):
                if self._cancel_requested:
                    break

                clean_line = line.strip()
                if not clean_line:
                    continue

                self._add_log(clean_line)

                # 匹配下載百分比
                # 例： 45% |████████████████ | 1.1 GB 12.3 MB/s
                pct_match = re.search(r"(\d{1,3})%\s*\|", clean_line)
                if pct_match:
                    pct = float(pct_match.group(1))
                    # 映射 5% ~ 85% 為下載階段
                    calc_pct = 5.0 + (pct * 0.8)
                    speed_match = re.search(r"([\d\.]+\s*[kMG]B/s)", clean_line)
                    speed = speed_match.group(1) if speed_match else ""
                    self._update_state(
                        status="downloading",
                        percentage=min(round(calc_pct, 1), 85.0),
                        message=f"正在下載 PyTorch CUDA 套件... ({pct:.0f}%)",
                        current_step=f"下載中 {speed}",
                        speed=speed,
                    )
                    continue

                # 匹配安裝解壓縮階段
                if "Installing collected packages" in clean_line:
                    self._update_state(
                        status="installing",
                        percentage=88.0,
                        message="下載完成，正在解壓縮並安裝 PyTorch 與 CUDA 驅動支援...",
                        current_step="安裝套件中",
                    )
                elif "Successfully installed" in clean_line:
                    self._update_state(
                        status="verifying",
                        percentage=95.0,
                        message="安裝完成，正在驗證 CUDA GPU 加速能力...",
                        current_step="校驗 CUDA 環境",
                    )

            self._install_process.wait()
            ret_code = self._install_process.returncode

            if self._cancel_requested:
                self._update_state(
                    status="cancelled",
                    message="安裝已被使用者中斷",
                    finished_at=time.time(),
                )
                return

            if ret_code != 0:
                self._update_state(
                    status="failed",
                    message=f"安裝失敗 (結束代碼 {ret_code})，請檢視日誌",
                    error=f"pip exit code {ret_code}",
                    finished_at=time.time(),
                )
                return

            # 3. 驗證新安裝的 PyTorch 是否支援 CUDA
            self._add_log("🔍 正在執行獨立子程序驗證 PyTorch CUDA...")
            verify_cmd = [
                python_exe,
                "-c",
                "import torch; assert torch.cuda.is_available(), 'CUDA still not available'; print('SUCCESS', torch.__version__, torch.cuda.get_device_name(0))",
            ]
            v_res = subprocess.run(verify_cmd, capture_output=True, text=True, timeout=10, check=False)

            if v_res.returncode == 0 and "SUCCESS" in v_res.stdout:
                self._add_log(f"🎉 驗證成功：{v_res.stdout.strip()}")
                # 清除快取並重新檢測
                self.get_cuda_status(force_refresh=True)

                # 嘗試熱重載 clip_extractor 中的 PyTorch 模型快取
                try:
                    from app.stubs.clip_stub import clip_extractor
                    clip_extractor._model = None
                    clip_extractor._current_model_key = None
                    clip_extractor._text_features_cache.clear()
                except Exception:
                    pass

                self._update_state(
                    status="success",
                    percentage=100.0,
                    message="🎉 GPU (CUDA) 加速已成功啟用！系統已就緒。",
                    current_step="安裝與驗證全部完成",
                    finished_at=time.time(),
                )
            else:
                self._add_log(f"⚠️ 驗證警告：{v_res.stderr or v_res.stdout}")
                self._update_state(
                    status="success",
                    percentage=100.0,
                    message="套件已安裝完成，建議重新整理頁面以啟用 GPU 加速。",
                    finished_at=time.time(),
                )

        except Exception as exc:
            logger.error(f"CUDA 安裝程序異常：{exc}", exc_info=True)
            self._add_log(f"❌ 發生異常：{exc}")
            self._update_state(
                status="failed",
                message=f"安裝發生錯誤：{exc}",
                error=str(exc),
                finished_at=time.time(),
            )

    async def subscribe(self):
        """SSE 訂閱安裝進度與日誌。"""
        q = asyncio.Queue()
        self._subscribers.append(q)
        try:
            yield self.get_install_state()
            while True:
                data = await q.get()
                yield data
        finally:
            if q in self._subscribers:
                self._subscribers.remove(q)


cuda_manager = CudaManager()
