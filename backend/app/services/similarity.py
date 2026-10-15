"""
similarity.py — 近似重複 / 連拍的向量化計算工具（v0.5 新增）

- clip_pairs()：CLIP 向量分塊相似度矩陣，只保留 ≥ 門檻的配對；有 CUDA 時在 GPU 上算。
  10 萬張 × 512 維在 RTX 級 GPU 上約數秒；CPU（numpy BLAS）約數十秒。
- phash_pairs()：64-bit pHash 以「鴿籠原理分塊索引」（multi-index hashing）找出
  漢明距離 ≤ 門檻的配對，避免 O(n²) 全比對；門檻 > 8 時退回分塊暴力比對。
- UnionFind / components()：把配對串成群組（傳遞閉包）。
- split_large_component()：群組超過上限時逐步收緊門檻拆小，避免 A~B~C~… 串連成巨大群組。
"""
import logging
from typing import Callable, Dict, List, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Bit helpers
# ─────────────────────────────────────────────
if hasattr(np, "bitwise_count"):
    def popcount64(x: np.ndarray) -> np.ndarray:
        return np.bitwise_count(x.astype(np.uint64)).astype(np.int16)
else:  # numpy < 2.0
    def popcount64(x: np.ndarray) -> np.ndarray:
        x = np.ascontiguousarray(x, dtype=np.uint64)
        bits = np.unpackbits(x.view(np.uint8).reshape(-1, 8), axis=1).sum(axis=1)
        return bits.reshape(x.shape).astype(np.int16)


def phash_hex_to_u64(hexes: Sequence[str]) -> np.ndarray:
    return np.array([int(h, 16) & 0xFFFFFFFFFFFFFFFF for h in hexes], dtype=np.uint64)


def _pairs_within(idx: np.ndarray, h: np.ndarray, thr: int, max_cells: int = 4_000_000):
    """idx 內所有 i<j 配對中，漢明距離 ≤ thr 者。回傳 (i, j, dist) 陣列。"""
    out_i, out_j, out_d = [], [], []
    m = len(idx)
    hv = h[idx]
    chunk = max(1, min(m, max_cells // max(m, 1)))
    for s in range(0, m, chunk):
        e = min(s + chunk, m)
        d = popcount64(hv[s:e, None] ^ hv[None, s:])
        # 只保留 j > i：區塊欄位從 s 開始，所以條件就是 col > row
        upper = np.triu(np.ones((e - s, m - s), dtype=bool), k=1)
        rows, cols = np.nonzero((d <= thr) & upper)
        if len(rows):
            out_i.append(idx[s + rows])
            out_j.append(idx[s + cols])
            out_d.append(d[rows, cols])
    if not out_i:
        return np.empty(0, np.int64), np.empty(0, np.int64), np.empty(0, np.int16)
    return np.concatenate(out_i), np.concatenate(out_j), np.concatenate(out_d)


def phash_pairs(h: np.ndarray, thr: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """回傳所有漢明距離 ≤ thr 的配對 (i, j, dist)，i < j，無重複。"""
    n = len(h)
    if n < 2:
        return np.empty(0, np.int64), np.empty(0, np.int64), np.empty(0, np.int16)
    h = np.asarray(h, dtype=np.uint64)
    if thr > 8:
        i, j, d = _pairs_within(np.arange(n), h, thr)
        return _dedupe(i, j, d, n)
    nb = thr + 1
    cuts = [64 * k // nb for k in range(nb + 1)]
    all_i, all_j, all_d = [], [], []
    for b0, b1 in zip(cuts, cuts[1:]):
        width = b1 - b0
        key = (h >> np.uint64(b0)) & np.uint64((1 << width) - 1)
        order = np.argsort(key, kind="stable")
        sk = key[order]
        bounds = np.flatnonzero(np.diff(sk)) + 1
        for grp in np.split(order, bounds):
            if len(grp) < 2:
                continue
            grp = np.sort(grp)
            i, j, d = _pairs_within(grp, h, thr)
            if len(i):
                all_i.append(i); all_j.append(j); all_d.append(d)
    if not all_i:
        return np.empty(0, np.int64), np.empty(0, np.int64), np.empty(0, np.int16)
    return _dedupe(np.concatenate(all_i), np.concatenate(all_j), np.concatenate(all_d), n)


def _dedupe(i, j, v, n):
    a = np.minimum(i, j).astype(np.int64)
    b = np.maximum(i, j).astype(np.int64)
    key = a * n + b
    _, first = np.unique(key, return_index=True)
    return a[first], b[first], v[first]


# ─────────────────────────────────────────────
# CLIP cosine
# ─────────────────────────────────────────────
def _torch_cuda():
    try:
        import torch
        if torch.cuda.is_available():
            return torch
    except Exception:
        pass
    return None


def clip_pairs(emb: np.ndarray, thr: float, block: int = 2048,
               use_gpu: bool = True) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    emb：已 L2 正規化的 float32 [N, D]。回傳餘弦相似度 ≥ thr 的配對 (i, j, sim)，i < j。
    """
    n = len(emb)
    if n < 2:
        return np.empty(0, np.int64), np.empty(0, np.int64), np.empty(0, np.float32)
    torch = _torch_cuda() if use_gpu else None
    out_i, out_j, out_s = [], [], []
    # 區塊的欄位從 s 開始（E[s:]），所以「只保留 j > i」就是區塊內的上三角（不含對角線）
    if torch is not None:
        gpu_block = max(256, min(block, 100_000_000 // n))
        E = torch.from_numpy(np.ascontiguousarray(emb, dtype=np.float32)).to("cuda", dtype=torch.float16)
        with torch.inference_mode():
            for s in range(0, n, gpu_block):
                e = min(s + gpu_block, n)
                S = torch.triu(E[s:e] @ E[s:].T, diagonal=1)   # [b, n-s]
                r, c = torch.nonzero(S >= thr, as_tuple=True)
                if r.numel():
                    out_i.append((r + s).cpu().numpy())
                    out_j.append((c + s).cpu().numpy())
                    out_s.append(S[r, c].float().cpu().numpy())
    else:
        E = np.ascontiguousarray(emb, dtype=np.float32)
        cpu_block = max(64, min(1024, 32_000_000 // n))
        for s in range(0, n, cpu_block):
            e = min(s + cpu_block, n)
            S = E[s:e] @ E[s:].T
            S[np.tril_indices(e - s, k=0, m=n - s)] = -1.0
            r, c = np.nonzero(S >= thr)
            if len(r):
                out_i.append(r + s)
                out_j.append(c + s)
                out_s.append(S[r, c])
    if not out_i:
        return np.empty(0, np.int64), np.empty(0, np.int64), np.empty(0, np.float32)
    return (np.concatenate(out_i).astype(np.int64), np.concatenate(out_j).astype(np.int64),
            np.concatenate(out_s).astype(np.float32))


def pair_cosine(emb: np.ndarray, i: np.ndarray, j: np.ndarray, chunk: int = 200_000) -> np.ndarray:
    out = np.empty(len(i), dtype=np.float32)
    for s in range(0, len(i), chunk):
        e = s + chunk
        out[s:e] = np.einsum("nd,nd->n", emb[i[s:e]], emb[j[s:e]])
    return out


# ─────────────────────────────────────────────
# Union-Find
# ─────────────────────────────────────────────
class UnionFind:
    def __init__(self, n: int):
        self.p = np.arange(n)

    def find(self, a: int) -> int:
        p = self.p
        root = a
        while p[root] != root:
            root = p[root]
        while p[a] != root:
            p[a], a = root, p[a]
        return root

    def union(self, a: int, b: int):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            if ra < rb:
                self.p[rb] = ra
            else:
                self.p[ra] = rb


def components(nodes: Sequence[int], ei: np.ndarray, ej: np.ndarray) -> List[List[int]]:
    """在 nodes（全域索引）上依邊 (ei, ej) 求連通分量，只回傳大小 ≥ 2 的群組。"""
    nodes = list(nodes)
    pos = {g: k for k, g in enumerate(nodes)}
    uf = UnionFind(len(nodes))
    for a, b in zip(ei.tolist(), ej.tolist()):
        if a in pos and b in pos:
            uf.union(pos[a], pos[b])
    groups: Dict[int, List[int]] = {}
    for k, g in enumerate(nodes):
        groups.setdefault(uf.find(k), []).append(g)
    return [sorted(v) for v in groups.values() if len(v) > 1]


EdgeFilter = Callable[[int], np.ndarray]  # level -> bool mask over edges


def split_large_component(
    comp: List[int],
    ei: np.ndarray,
    ej: np.ndarray,
    edge_filter: EdgeFilter,
    cap: int,
    max_level: int = 10,
    level: int = 1,
) -> List[List[int]]:
    """群組大於 cap 時，以更嚴格的門檻（edge_filter(level)）重新分群，遞迴直到 ≤ cap 或到達上限。"""
    if len(comp) <= cap or level > max_level:
        if len(comp) > cap:
            logger.warning("近似群組有 %d 張，收緊門檻到上限仍無法拆小，維持原樣", len(comp))
        return [comp]
    members = set(comp)
    keep = edge_filter(level)
    in_comp = np.fromiter((a in members and b in members for a, b in zip(ei.tolist(), ej.tolist())),
                          dtype=bool, count=len(ei))
    m = keep & in_comp
    sub = components(comp, ei[m], ej[m])
    out: List[List[int]] = []
    for c in sub:
        out.extend(split_large_component(c, ei, ej, edge_filter, cap, max_level, level + 1))
    return out
