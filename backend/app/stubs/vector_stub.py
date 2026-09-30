import numpy as np
from typing import List, Tuple, Dict

class VectorIndex:
    """
    高效記憶體向量檢索索引。
    支援真實 512 維 CLIP 嵌入向量進行矩陣化餘弦相似度 (Cosine Similarity) 快速計算與排序。
    """

    def __init__(self):
        self.asset_ids: List[int] = []
        self.embeddings: List[np.ndarray] = []

    def clear(self):
        self.asset_ids.clear()
        self.embeddings.clear()

    def add_item(self, asset_id: int, embedding_bytes: bytes):
        if not embedding_bytes:
            return
        vec = np.frombuffer(embedding_bytes, dtype=np.float32)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        self.asset_ids.append(asset_id)
        self.embeddings.append(vec)

    def find_similar(self, query_bytes: bytes, top_k: int = 10, threshold: float = 0.85) -> List[Tuple[int, float]]:
        if not self.embeddings or not query_bytes:
            return []
        
        query_vec = np.frombuffer(query_bytes, dtype=np.float32)
        norm = np.linalg.norm(query_vec)
        if norm > 0:
            query_vec = query_vec / norm

        matrix = np.vstack(self.embeddings)
        similarities = np.dot(matrix, query_vec)

        results = []
        for idx, sim in enumerate(similarities):
            if sim >= threshold:
                results.append((self.asset_ids[idx], float(sim)))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

vector_index = VectorIndex()
