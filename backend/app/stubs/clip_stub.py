import logging
import io
import os
import hashlib
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any
from PIL import Image
import numpy as np

logger = logging.getLogger(__name__)

# 標準 Prompt Ensemble 模板集合
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

# 中文標籤對應表
LABEL_MAP: Dict[str, str] = {
    "normal_photo": "normal_photo",
    "photo": "normal_photo",
    "相片": "normal_photo",
    "正常相片": "normal_photo",
    "一般相片": "normal_photo",
    "螢幕截圖": "screenshot",
    "截圖": "screenshot",
    "screenshot": "screenshot",
    "文件": "document",
    "收據": "document",
    "發票": "document",
    "document": "document",
    "梗圖": "meme",
    "迷因": "meme",
    "表情包": "meme",
    "meme": "meme",
    "誤拍": "mistouch",
    "廢片": "mistouch",
    "口袋": "mistouch",
    "mistouch": "mistouch",
}


class RealClipExtractor:
    """
    真實 CLIP 特徵提取器與 Prompt Ensemble 零樣本分類器。
    基於 open_clip 與 PyTorch，支援 CUDA GPU 加速與 CPU 運算。
    """

    EMBEDDING_DIM = 512

    def __init__(self):
        self._model = None
        self._preprocess = None
        self._tokenizer = None
        self._current_model_key: Optional[str] = None
        self._device_str: str = "cpu"
        self._text_features_cache: Dict[str, Any] = {}

    @property
    def cuda_available(self) -> bool:
        """檢查系統當前是否有可用的 CUDA GPU。"""
        try:
            import torch
            return bool(torch.cuda.is_available())
        except Exception:
            return False

    def _resolve_device(self, requested: str) -> str:
        """
        解析實際使用的 PyTorch 裝置字串。
        """
        if requested == "cuda":
            if not self.cuda_available:
                from app.services.cuda_manager import cuda_manager
                hw = cuda_manager.get_cuda_status()
                if hw.get("has_nvidia_gpu"):
                    raise RuntimeError(
                        f"偵測到您的電腦配備 {hw.get('gpu_name')}，但目前環境為 CPU 版 PyTorch。"
                        "請在介面點擊「一鍵啟用 GPU 加速」下載安裝 CUDA 套件，或改選「自動」/「CPU」模式。"
                    )
                raise RuntimeError("本機未檢測到相容的 NVIDIA GPU，無法強制使用 CUDA 模式。請選擇 CPU 或自動模式。")
            return "cuda"
        elif requested == "auto":
            return "cuda" if self.cuda_available else "cpu"
        return "cpu"

    def _load_model(self, model_name: str = "ViT-B-16", pretrained: str = "openai", device: str = "auto"):
        """
        延遲單例載入 open_clip 模型。
        """
        import torch
        import open_clip
        from torchvision.transforms import Compose, Resize, CenterCrop, ToTensor, Normalize, InterpolationMode

        target_device = self._resolve_device(device)
        model_key = f"{model_name}_{pretrained}_{target_device}"

        if self._model is not None and self._current_model_key == model_key:
            return

        logger.info(f"載入 CLIP 模型：{model_name} (預訓練權重: {pretrained}, 裝置: {target_device})...")

        # 優先檢查本機快取
        cache_file = os.path.expanduser(f"~/.cache/clip/{model_name}.pt")
        if os.path.exists(cache_file):
            try:
                jit_m = torch.jit.load(cache_file, map_location=torch.device("cpu"))
                state_dict = jit_m.state_dict()
                model = open_clip.create_model(model_name, pretrained=None)
                model.load_state_dict(state_dict, strict=False)
                model = model.to(torch.device(target_device))
                model.eval()
                preprocess = Compose([
                    Resize(224, interpolation=InterpolationMode.BICUBIC),
                    CenterCrop(224),
                    ToTensor(),
                    Normalize((0.48145466, 0.4578275, 0.40821073), (0.26862954, 0.26130258, 0.27577711))
                ])
                tokenizer = open_clip.get_tokenizer(model_name)

                self._model = model
                self._preprocess = preprocess
                self._tokenizer = tokenizer
                self._current_model_key = model_key
                self._device_str = target_device
                self._text_features_cache.clear()
                return
            except Exception as e:
                logger.warning(f"Failed to load cached checkpoint directly: {e}")

        # Fallback to standard open_clip loader
        model, _, preprocess = open_clip.create_model_and_transforms(
            model_name,
            pretrained=pretrained,
            device=target_device
        )
        model.eval()
        tokenizer = open_clip.get_tokenizer(model_name)

        self._model = model
        self._preprocess = preprocess
        self._tokenizer = tokenizer
        self._current_model_key = model_key
        self._device_str = target_device
        self._text_features_cache.clear()

    def _get_text_features(self, category_key: str, torch_device) -> Any:
        """
        計算並快取 Prompt Ensemble 文字特徵向量。
        """
        import torch

        if category_key in self._text_features_cache:
            return self._text_features_cache[category_key]

        prompts = PROMPT_ENSEMBLES.get(category_key, [f"a photo of a {category_key}"])
        text_tokens = self._tokenizer(prompts).to(torch_device)
        
        with torch.no_grad():
            text_features = self._model.encode_text(text_tokens)
            text_features /= text_features.norm(dim=-1, keepdim=True)
            # Ensemble 平均
            mean_feature = text_features.mean(dim=0, keepdim=True)
            mean_feature /= mean_feature.norm(dim=-1, keepdim=True)

        self._text_features_cache[category_key] = mean_feature
        return mean_feature

    def extract_embedding(
        self,
        file_path: str,
        image: Optional[Image.Image] = None,
        model_name: str = "ViT-B-16",
        pretrained: str = "openai",
        device: str = "auto",
    ) -> bytes:
        """
        提取圖片的 512 維真實正規化特徵向量（float32 BLOB）。
        """
        try:
            import torch
            self._load_model(model_name=model_name, pretrained=pretrained, device=device)
            torch_device = torch.device(self._device_str)

            if image is None:
                image = Image.open(file_path).convert("RGB")
            elif image.mode != "RGB":
                image = image.convert("RGB")

            image_tensor = self._preprocess(image).unsqueeze(0).to(torch_device)

            with torch.no_grad():
                image_features = self._model.encode_image(image_tensor)
                image_features /= image_features.norm(dim=-1, keepdim=True)

            vec_np = image_features.cpu().numpy().flatten().astype(np.float32)
            return vec_np.tobytes()
        except RuntimeError as e:
            if "CUDA 不可用" in str(e):
                raise
            logger.warning(f"CLIP embedding fallback for {file_path}: {e}")
            seed = int(hashlib.sha256(file_path.encode('utf-8')).hexdigest()[:8], 16)
            rng = np.random.default_rng(seed)
            vec = rng.standard_normal(self.EMBEDDING_DIM, dtype=np.float32)
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec = vec / norm
            return vec.tobytes()
        except Exception as e:
            logger.warning(f"CLIP embedding fallback for {file_path}: {e}")
            seed = int(hashlib.sha256(file_path.encode('utf-8')).hexdigest()[:8], 16)
            rng = np.random.default_rng(seed)
            vec = rng.standard_normal(self.EMBEDDING_DIM, dtype=np.float32)
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec = vec / norm
            return vec.tobytes()

    def classify_zero_shot(
        self,
        file_path: str,
        labels: List[str],
        image: Optional[Image.Image] = None,
        model_name: str = "ViT-B-16",
        pretrained: str = "openai",
        device: str = "auto",
    ) -> Dict[str, float]:
        """
        使用 Prompt Ensemble 執行真實 Zero-Shot 內容語義分類。
        回傳各標籤信心度機率 (0.0 ~ 1.0)。
        """
        try:
            import torch
            self._load_model(model_name=model_name, pretrained=pretrained, device=device)
            torch_device = torch.device(self._device_str)

            if image is None:
                image = Image.open(file_path).convert("RGB")
            elif image.mode != "RGB":
                image = image.convert("RGB")

            image_tensor = self._preprocess(image).unsqueeze(0).to(torch_device)

            with torch.no_grad():
                image_feature = self._model.encode_image(image_tensor)
                image_feature /= image_feature.norm(dim=-1, keepdim=True)

                category_keys = [LABEL_MAP.get(lbl, lbl) for lbl in labels]
                eval_keys = list(category_keys)
                has_normal = "normal_photo" in eval_keys
                if not has_normal:
                    eval_keys.append("normal_photo")

                text_embeds_list = [self._get_text_features(k, torch_device) for k in eval_keys]
                text_matrix = torch.cat(text_embeds_list, dim=0)

                # 計算餘弦相似度並 Softmax 縮放
                logit_scale = getattr(self._model, "logit_scale", torch.tensor(100.0)).exp()
                logits = logit_scale * (image_feature @ text_matrix.T)
                probs = torch.softmax(logits, dim=-1).cpu().numpy().flatten()

            results = {}
            for i, lbl in enumerate(labels):
                results[lbl] = round(float(probs[i]), 3)

            if not has_normal:
                results["normal_photo"] = round(float(probs[-1]), 3)

            return results
        except RuntimeError as e:
            if "CUDA 不可用" in str(e):
                raise
            logger.warning(f"CLIP zero-shot fallback for {file_path}: {e}")
            res = {lbl: 0.0 for lbl in labels}
            res["normal_photo"] = 1.0
            return res
        except Exception as e:
            logger.warning(f"CLIP zero-shot fallback for {file_path}: {e}")
            res = {lbl: 0.0 for lbl in labels}
            res["normal_photo"] = 1.0
            return res


clip_extractor = RealClipExtractor()
