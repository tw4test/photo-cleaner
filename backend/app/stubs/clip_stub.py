"""
CLIP 特徵提取器（open_clip + PyTorch），v0.5.3 批次版。

重點：
- encode_batch()：一次對 N 張已前處理的影像做 forward，CUDA 下使用 fp16。
- 影像前處理改為「補邊成正方形」再縮放（不再中心裁切），長截圖的狀態列不會被切掉。
- 任何錯誤一律拋出例外，由呼叫端寫入 analysis_failures；不再產生隨機向量或
  normal_photo=1.0 的假結果。
- 移除 ~/.cache/clip/*.pt 的自製載入路徑（會以錯誤的 GELU 架構、strict=False 靜默載入）。
- v0.5.1：OpenAI 權重強制使用 QuickGELU（與訓練時一致）。
- v0.5.3：CUDA 精度自動選擇（實測 fp16 / fp32 取較快者），可用 PC_CLIP_PRECISION 強制指定。
- 後端可替換（set_backend_factory），測試可以注入假的 encoder，不需要下載模型。
"""
import io
import logging
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 零樣本分類 Prompt Ensemble
# ─────────────────────────────────────────────
PROMPT_ENSEMBLES: Dict[str, List[str]] = {
    "normal_photo": [
        "a regular photograph of people, family, kids, children, portrait or group photo",
        "a photograph of people on a stage at an event, celebration, performance or ceremony",
        "a photograph of outdoor scenery, travel, landscape, nature, city street",
        "a photograph of food, dish, meals, pets, animals, or objects",
        "a clear standard camera photograph of daily life",
        "a portrait photo of smiling people",
    ],
    "screenshot": [
        "a digital screenshot of a mobile phone screen showing app interface, status bar, and time",
        "a digital screenshot of a computer monitor, desktop window, or website browser",
        "a digital screenshot of a messaging chat conversation, SMS, or social media app interface",
    ],
    "document": [
        "a photo of a paper receipt, payment invoice, store ticket with prices and text",
        "a photo of a printed paper document, form, bill, contract, or page of a book",
        "a close-up photo of printed text on white paper or receipt paper",
        "a photo of handwritten notes on paper, notebook, or whiteboard",
        "a table spreadsheet or receipt with line items and numbers on paper",
    ],
    "meme": [
        "an internet meme with funny text captions in Impact font",
        "a funny viral internet meme image, reaction picture, or cartoon sticker",
        "a comic strip or comic illustration meme",
    ],
    "mistouch": [
        "an accidental completely black dark photo taken inside a dark pocket or purse",
        "an accidental blurry photo of floor tiles, asphalt ground, carpet or blank ceiling",
        "an extreme blurry unusable photo taken by accident with lens completely covered",
    ],
}

# 分類輸出順序（clip_features.zs 一律使用這些英文鍵）
CATEGORY_KEYS: List[str] = ["screenshot", "document", "meme", "mistouch", "normal_photo"]

# 舊版中文標籤 → 標準鍵（向下相容 extract_all / classify_zero_shot）
LABEL_MAP: Dict[str, str] = {
    "normal_photo": "normal_photo", "photo": "normal_photo", "相片": "normal_photo",
    "正常相片": "normal_photo", "一般相片": "normal_photo",
    "螢幕截圖": "screenshot", "截圖": "screenshot", "screenshot": "screenshot",
    "文件": "document", "收據": "document", "發票": "document", "document": "document",
    "梗圖": "meme", "迷因": "meme", "表情包": "meme", "meme": "meme",
    "誤拍": "mistouch", "廢片": "mistouch", "口袋": "mistouch", "mistouch": "mistouch",
}

OPENAI_MEAN = (0.48145466, 0.4578275, 0.40821073)
OPENAI_STD = (0.26862954, 0.26130258, 0.27577711)


class ClipDeviceError(RuntimeError):
    """要求的運算裝置不可用（例如指定 cuda 但沒有 CUDA）。"""


def is_oom_error(exc: BaseException) -> bool:
    """判斷是否為 GPU 顯存不足。"""
    try:
        import torch
        if isinstance(exc, getattr(torch.cuda, "OutOfMemoryError", ())):
            return True
    except Exception:
        pass
    return "out of memory" in str(exc).lower()


def needs_quick_gelu(model_name: str, pretrained: str) -> bool:
    """
    OpenAI 原版 CLIP 權重是以 QuickGELU 訓練的，但 open_clip 的 "ViT-B-16" 等設定預設是標準 GELU。
    不強制指定的話，模型會以錯誤的激活函數載入：照樣能跑、但向量與分類準確度下降，
    並出現 "QuickGELU mismatch" 警告。模型名稱已含 "quickgelu" 時不需要再指定。
    """
    return (pretrained or "").lower() == "openai" and "quickgelu" not in (model_name or "").lower()


# ─────────────────────────────────────────────
# Backend：真正執行神經網路的部分
# ─────────────────────────────────────────────
class OpenClipBackend:
    """
    open_clip 後端。

    精度（環境變數 PC_CLIP_PRECISION：auto | fp16 | fp32，預設 auto）：
    - CPU 一律 fp32。
    - CUDA + auto：載入後用同一批假資料實測 fp16 與 fp32，選較快的一個。
      部分顯示卡（例如 GTX 16 系列）fp16 反而比 fp32 慢好幾倍；RTX 系列通常 fp16 較快。
    """

    BENCH_BATCH = 16
    BENCH_RUNS = 3

    def __init__(self, model_name: str, pretrained: str, device: str):
        import copy
        import os
        import time

        import torch
        import open_clip

        self.torch = torch
        self.device = device
        quick_gelu = needs_quick_gelu(model_name, pretrained)
        model, _, preprocess = open_clip.create_model_and_transforms(
            model_name, pretrained=pretrained, device=device, precision="fp32",
            force_quick_gelu=quick_gelu,
        )
        model.eval()
        self.tokenizer = open_clip.get_tokenizer(model_name)

        size = getattr(model.visual, "image_size", 224)
        if isinstance(size, (tuple, list)):
            size = int(size[0])
        self.image_size = int(size)

        mean, std = OPENAI_MEAN, OPENAI_STD
        for t in getattr(preprocess, "transforms", []):
            if t.__class__.__name__ == "Normalize":
                mean, std = tuple(float(x) for x in t.mean), tuple(float(x) for x in t.std)
        self.mean, self.std = mean, std

        requested = os.environ.get("PC_CLIP_PRECISION", "auto").strip().lower()
        if requested not in ("auto", "fp16", "fp32"):
            requested = "auto"
        self.precision_bench: Dict[str, float] = {}

        if device != "cuda" or requested == "fp32":
            precision = "fp32"
        elif requested == "fp16":
            precision = "fp16"
            model = model.half()
        else:
            def rate(m, dtype):
                x = torch.randn(self.BENCH_BATCH, 3, self.image_size, self.image_size, device=device, dtype=dtype)
                with torch.inference_mode():
                    m.encode_image(x)                       # 暖機
                    torch.cuda.synchronize()
                    t0 = time.perf_counter()
                    for _ in range(self.BENCH_RUNS):
                        m.encode_image(x)
                    torch.cuda.synchronize()
                return self.BENCH_BATCH * self.BENCH_RUNS / (time.perf_counter() - t0)

            r32 = rate(model, torch.float32)
            m16 = copy.deepcopy(model).half()
            try:
                r16 = rate(m16, torch.float16)
            except Exception as exc:  # noqa: BLE001
                logger.warning("fp16 測試失敗，改用 fp32：%s", exc)
                r16 = 0.0
            self.precision_bench = {"fp16": round(r16, 1), "fp32": round(r32, 1)}
            if r16 > r32:
                precision, model = "fp16", m16
            else:
                precision = "fp32"
                del m16
            torch.cuda.empty_cache()

        self.precision = precision
        self.dtype = torch.float16 if precision == "fp16" else torch.float32
        self.model = model
        logger.info("CLIP 模型 %s / %s 已載入（裝置：%s，精度：%s，QuickGELU：%s，實測：%s）",
                    model_name, pretrained, device, precision, quick_gelu, self.precision_bench or "-")

        with torch.no_grad():
            self.logit_scale = float(model.logit_scale.exp().item()) if hasattr(model, "logit_scale") else 100.0
            probe = torch.zeros(1, 3, self.image_size, self.image_size, device=device, dtype=self.dtype)
            self.embedding_dim = int(model.encode_image(probe).shape[-1])

    def encode_images(self, batch: np.ndarray) -> np.ndarray:
        torch = self.torch
        with torch.inference_mode():
            # 每批都是新配置的陣列，用 pin_memory() 反而要多一次複製 + 配置鎖頁記憶體（Windows 上很慢），
            # 直接複製到 GPU 即可
            x = torch.from_numpy(batch).to(self.device, dtype=self.dtype)
            feats = self.model.encode_image(x)
            feats = feats / feats.norm(dim=-1, keepdim=True)
            return feats.float().cpu().numpy()

    def encode_texts(self, prompts: Sequence[str]) -> np.ndarray:
        torch = self.torch
        with torch.inference_mode():
            tokens = self.tokenizer(list(prompts)).to(self.device)
            feats = self.model.encode_text(tokens)
            feats = feats / feats.norm(dim=-1, keepdim=True)
            return feats.float().cpu().numpy()

    def empty_cache(self):
        if self.device == "cuda":
            self.torch.cuda.empty_cache()


BackendFactory = Callable[[str, str, str], Any]


# ─────────────────────────────────────────────
# Extractor：快取模型、前處理、批次編碼、零樣本分類
# ─────────────────────────────────────────────
class ClipExtractor:
    def __init__(self):
        self._lock = threading.Lock()
        self._backend = None
        self._backend_key: Optional[Tuple[str, str, str]] = None
        self._text_matrix: Optional[np.ndarray] = None
        self._factory: BackendFactory = OpenClipBackend
        self._cuda_override: Optional[bool] = None

    # ── 測試 / 熱重載 ─────────────────────────────
    def set_backend_factory(self, factory: Optional[BackendFactory], cuda_available: Optional[bool] = None):
        with self._lock:
            self._factory = factory or OpenClipBackend
            self._cuda_override = cuda_available
            self._backend = None
            self._backend_key = None
            self._text_matrix = None

    def reset(self):
        """釋放模型（例如安裝 CUDA 版 PyTorch 後）。"""
        with self._lock:
            if self._backend is not None and hasattr(self._backend, "empty_cache"):
                try:
                    self._backend.empty_cache()
                except Exception:
                    pass
            self._backend = None
            self._backend_key = None
            self._text_matrix = None

    # 舊程式碼直接改這些屬性；保留以免出錯
    @property
    def _model(self):
        return self._backend

    @_model.setter
    def _model(self, value):
        if value is None:
            self.reset()

    # ── 裝置 ──────────────────────────────────────
    @property
    def cuda_available(self) -> bool:
        if self._cuda_override is not None:
            return self._cuda_override
        try:
            import torch
            return bool(torch.cuda.is_available())
        except Exception:
            return False

    def resolve_device(self, requested: str) -> str:
        if requested == "cuda":
            if not self.cuda_available:
                raise ClipDeviceError("指定使用 GPU（CUDA），但目前環境無法使用 CUDA。")
            return "cuda"
        if requested == "auto":
            return "cuda" if self.cuda_available else "cpu"
        return "cpu"

    def total_vram_mb(self) -> int:
        try:
            import torch
            if torch.cuda.is_available():
                return int(torch.cuda.get_device_properties(0).total_memory // (1024 * 1024))
        except Exception:
            pass
        return 0

    def recommended_batch_size(self, device: str) -> int:
        if device != "cuda":
            return 16
        vram = self.total_vram_mb()
        if vram >= 12000:
            return 128
        if vram >= 6000:
            return 64
        return 32

    # ── 模型 ──────────────────────────────────────
    def get_backend(self, model_name: str, pretrained: str, device: str):
        """取得（必要時載入）指定模型；device 必須是已解析的 'cuda' 或 'cpu'。"""
        key = (model_name, pretrained, device)
        with self._lock:
            if self._backend is None or self._backend_key != key:
                if self._backend is not None and hasattr(self._backend, "empty_cache"):
                    try:
                        self._backend.empty_cache()
                    except Exception:
                        pass
                self._backend = None
                self._text_matrix = None
                backend = self._factory(model_name, pretrained, device)
                prompt_feats = []
                for cat in CATEGORY_KEYS:
                    t = backend.encode_texts(PROMPT_ENSEMBLES[cat])
                    m = t.mean(axis=0)
                    m = m / (np.linalg.norm(m) + 1e-12)
                    prompt_feats.append(m)
                self._text_matrix = np.stack(prompt_feats).astype(np.float32)
                self._backend = backend
                self._backend_key = key
            return self._backend

    # ── 前處理（loader 執行緒呼叫，不需要 torch）──────
    def preprocess(self, img: Image.Image, image_size: int, mean=OPENAI_MEAN, std=OPENAI_STD) -> np.ndarray:
        """補邊成正方形 → 縮放 → 正規化，回傳 float32 [3, S, S]。"""
        if img.mode != "RGB":
            img = img.convert("RGB")
        fill = tuple(int(round(m * 255)) for m in mean)
        padded = ImageOps.pad(img, (image_size, image_size), method=Image.Resampling.BICUBIC, color=fill)
        arr = np.asarray(padded, dtype=np.float32) / 255.0
        arr = (arr - np.asarray(mean, dtype=np.float32)) / np.asarray(std, dtype=np.float32)
        return np.ascontiguousarray(arr.transpose(2, 0, 1))

    @staticmethod
    def load_image(file_path: str, is_raw: bool = False, thumb_path: Optional[str] = None) -> Image.Image:
        """
        讀取給 CLIP 的影像：優先本地縮圖快取（不經 NAS），其次 RAW 內嵌預覽，最後原檔。
        讀不到時拋出例外（不做任何假資料降級）。
        """
        if thumb_path and Path(thumb_path).exists():
            with Image.open(thumb_path) as t_img:
                return t_img.convert("RGB")
        if is_raw:
            from app.stubs.raw_stub import raw_decoder
            raw_bytes = raw_decoder.decode_raw(file_path)
            if not raw_bytes:
                raise ValueError(f"RAW 解碼失敗：{file_path}")
            with Image.open(io.BytesIO(raw_bytes)) as img:
                return ImageOps.exif_transpose(img).convert("RGB")
        with open(file_path, "rb") as f:
            data = f.read()
        with Image.open(io.BytesIO(data)) as img:
            img.draft("RGB", (448, 448))
            return ImageOps.exif_transpose(img).convert("RGB")

    # ── 批次編碼 ──────────────────────────────────
    def classify(self, embeddings: np.ndarray, logit_scale: float) -> np.ndarray:
        """embeddings [N,D] → 機率 [N, len(CATEGORY_KEYS)]。"""
        logits = logit_scale * (embeddings @ self._text_matrix.T)
        logits = logits - logits.max(axis=1, keepdims=True)
        e = np.exp(logits)
        return e / e.sum(axis=1, keepdims=True)

    def encode_batch(self, backend, batch: np.ndarray) -> Tuple[np.ndarray, List[Dict[str, float]]]:
        """batch float32 [N,3,S,S] → (embeddings float32 [N,D], 零樣本分數 list)。"""
        emb = backend.encode_images(batch).astype(np.float32)
        if not np.all(np.isfinite(emb)):
            raise ValueError("CLIP 輸出含 NaN/Inf")
        probs = self.classify(emb, backend.logit_scale)
        zs = [{k: round(float(p[i]), 4) for i, k in enumerate(CATEGORY_KEYS)} for p in probs]
        return emb, zs

    # ── 單張相容 API（feature_pipeline.extract_clip 使用）──
    def extract_all(
        self,
        file_path: str,
        labels: List[str],
        image: Optional[Image.Image] = None,
        is_raw: bool = False,
        thumb_path: Optional[str] = None,
        model_name: str = "ViT-B-16",
        pretrained: str = "openai",
        device: str = "auto",
    ) -> Tuple[bytes, Dict[str, float]]:
        dev = self.resolve_device(device)
        backend = self.get_backend(model_name, pretrained, dev)
        if image is None:
            image = self.load_image(file_path, is_raw=is_raw, thumb_path=thumb_path)
        arr = self.preprocess(image, backend.image_size, backend.mean, backend.std)[None]
        emb, zs = self.encode_batch(backend, arr)
        canonical = zs[0]
        result: Dict[str, float] = {}
        for lbl in labels:
            result[lbl] = canonical.get(LABEL_MAP.get(lbl, lbl), 0.0)
        result.setdefault("normal_photo", canonical["normal_photo"])
        return emb[0].tobytes(), result

    def extract_embedding(self, file_path: str, image: Optional[Image.Image] = None,
                          model_name: str = "ViT-B-16", pretrained: str = "openai",
                          device: str = "auto") -> bytes:
        emb, _ = self.extract_all(file_path, [], image=image, model_name=model_name,
                                  pretrained=pretrained, device=device)
        return emb

    def classify_zero_shot(self, file_path: str, labels: List[str], image: Optional[Image.Image] = None,
                           model_name: str = "ViT-B-16", pretrained: str = "openai",
                           device: str = "auto") -> Dict[str, float]:
        _, zs = self.extract_all(file_path, labels, image=image, model_name=model_name,
                                 pretrained=pretrained, device=device)
        return zs


clip_extractor = ClipExtractor()
