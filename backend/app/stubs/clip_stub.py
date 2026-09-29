import hashlib
import numpy as np
from typing import List, Dict

# TODO(GPU): 替換為 open_clip / SigLIP (CUDA 12.8+, RTX 5080 批次推論)
class MockClipExtractor:
    """
    Mock CLIP 特徵提取器與零樣本分類器。
    在無 GPU 環境下生成確定性的 512 維虛擬特徵向量與模擬零樣本分類分數。
    """
    
    EMBEDDING_DIM = 512

    def extract_embedding(self, file_path: str) -> bytes:
        """
        根據檔案路徑的 SHA-256 生成固定種子的 512 維正規化隨機向量 (float32 BLOB)。
        """
        seed = int(hashlib.sha256(file_path.encode('utf-8')).hexdigest()[:8], 16)
        rng = np.random.default_rng(seed)
        vec = rng.standard_normal(self.EMBEDDING_DIM, dtype=np.float32)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec.tobytes()

    def classify_zero_shot(self, file_path: str, labels: List[str]) -> Dict[str, float]:
        """
        計算 zero-shot 分類機率 (截圖, 文件收據, 梗圖迷因, 誤拍廢片等)。
        在 Mock 階段根據檔名特性與 Hash 提供逼真的模擬預測機率。
        """
        lower_path = file_path.lower()
        seed = int(hashlib.md5(file_path.encode('utf-8')).hexdigest()[:6], 16)
        rng = np.random.default_rng(seed)

        scores = {}
        for label in labels:
            prob = float(rng.uniform(0.01, 0.25))

            # 檔名關鍵字命中時給予高信心度
            if "screenshot" in lower_path or "截圖" in lower_path or "screen" in lower_path:
                if "screenshot" in label or "截圖" in label:
                    prob = float(rng.uniform(0.82, 0.98))
            elif "doc" in lower_path or "scan" in lower_path or "receipt" in lower_path or "發票" in lower_path or "文件" in lower_path:
                if "document" in label or "文件" in label or "收據" in label:
                    prob = float(rng.uniform(0.78, 0.95))
            elif "meme" in lower_path or "梗圖" in lower_path or "表情" in lower_path or "sticker" in lower_path:
                if "meme" in label or "梗圖" in label or "迷因" in label:
                    prob = float(rng.uniform(0.80, 0.96))
            elif "dark" in lower_path or "blur" in lower_path or "mistouch" in lower_path or "pocket" in lower_path:
                if "mistouch" in label or "誤拍" in label:
                    prob = float(rng.uniform(0.75, 0.93))
            else:
                # 依 hash 自然散佈少量高機率樣本，供 mock 測試體驗
                if seed % 13 == 0 and ("screenshot" in label or "截圖" in label):
                    prob = float(rng.uniform(0.75, 0.92))
                elif seed % 17 == 0 and ("document" in label or "文件" in label or "收據" in label):
                    prob = float(rng.uniform(0.72, 0.91))
                elif seed % 19 == 0 and ("meme" in label or "梗圖" in label or "迷因" in label):
                    prob = float(rng.uniform(0.76, 0.94))
                elif seed % 23 == 0 and ("mistouch" in label or "誤拍" in label):
                    prob = float(rng.uniform(0.70, 0.89))

            scores[label] = round(prob, 3)

        return scores

clip_extractor = MockClipExtractor()
