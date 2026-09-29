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
        計算 zero-shot 分類機率 (截圖, 文件, 誤拍, 梗圖等)。
        """
        lower_path = file_path.lower()
        scores = {}
        for label in labels:
            if "screenshot" in lower_path or "螢幕截圖" in label:
                scores[label] = 0.85 if "screenshot" in lower_path else 0.02
            elif "mistouch" in label or "誤拍" in label:
                scores[label] = 0.75 if "blur" in lower_path or "dark" in lower_path else 0.01
            else:
                scores[label] = 0.03
        return scores

clip_extractor = MockClipExtractor()
