"""2bpp タイル化・重複タイルの統合・似たタイルの統合 (タイル数の削減)。"""
from __future__ import annotations

import numpy as np

from .imageproc import Indexed


def split_tiles(idx: np.ndarray) -> np.ndarray:
    """(144, 160) → (360, 8, 8)"""
    h, w = idx.shape
    return idx.reshape(h // 8, 8, w // 8, 8).transpose(0, 2, 1, 3).reshape(-1, 8, 8)


def encode_2bpp(tiles: np.ndarray) -> np.ndarray:
    """(n, 8, 8) 色番号 → (n, 16) バイト (各行 下位ビット面, 上位ビット面)。"""
    w = (1 << np.arange(7, -1, -1)).astype(np.uint16)
    lo = ((tiles & 1).astype(np.uint16) * w).sum(axis=2)
    hi = (((tiles >> 1) & 1).astype(np.uint16) * w).sum(axis=2)
    return np.stack([lo, hi], axis=2).reshape(len(tiles), 16).astype(np.uint8)


def decode_2bpp(b: np.ndarray) -> np.ndarray:
    """(n, 16) → (n, 8, 8)"""
    b = b.reshape(-1, 8, 2).astype(np.uint8)
    sh = np.arange(7, -1, -1)
    lo = (b[:, :, 0:1] >> sh) & 1
    hi = (b[:, :, 1:2] >> sh) & 1
    return (lo | (hi << 1)).astype(np.uint8)


def visual(ind: Indexed) -> np.ndarray:
    """似ているかを比べるための見た目 (360, 64*ch)。DMG は濃さ 0..3、CGB は RGB を 0..3 の目盛りに。"""
    t = split_tiles(ind.idx)
    if ind.target == "dmg":
        return t.reshape(len(t), -1).astype(np.float32)
    tp = ind.tile_pal.reshape(-1)
    rgb = ind.pals5[tp[:, None, None], t]                     # (360, 8, 8, 3)
    return (rgb.reshape(len(t), -1).astype(np.float32) / (31.0 / 3.0))


def flip_variants(t: np.ndarray) -> list[tuple[np.ndarray, int]]:
    """CGB の属性で表せる反転 (属性ビット: 0x20 = 左右, 0x40 = 上下)。"""
    return [(t, 0), (t[:, ::-1], 0x20), (t[::-1, :], 0x40), (t[::-1, ::-1], 0x60)]


def merge_to_limit(vis: np.ndarray, counts: np.ndarray, limit: int, fixed: int = 0) -> np.ndarray:
    """似ているタイルを統合して limit 個以下にする。

    vis: (n, d) 見た目, counts: 各タイルの使用数, 先頭 fixed 個は消さない (既に VRAM にあるタイルなど)。
    戻り値: 各タイルの統合先の番号 (n,)。"""
    n = len(vis)
    target = np.arange(n)
    if n <= limit:
        return target
    alive = np.ones(n, bool)
    d = np.abs(vis[:, None, :] - vis[None, :, :]).sum(-1).astype(np.float32)
    np.fill_diagonal(d, np.inf)
    cnt = counts.astype(np.float32).copy()
    remaining = n
    while remaining > limit:
        # 消しても良いタイル (fixed 以外) の中で、一番近い相手との距離 × 使用数 が最小のものを統合
        cand = np.where(alive)[0]
        removable = cand[cand >= fixed]
        if len(removable) == 0:
            break
        sub = d[np.ix_(removable, cand)]
        j = np.argmin(sub, axis=1)
        cost = sub[np.arange(len(removable)), j] * cnt[removable]
        r = int(np.argmin(cost))
        victim, keep = int(removable[r]), int(cand[j[r]])
        alive[victim] = False
        target[target == victim] = keep
        cnt[keep] += cnt[victim]
        d[victim, :] = np.inf
        d[:, victim] = np.inf
        remaining -= 1
    # 推移的に解決
    for i in range(n):
        while target[target[i]] != target[i]:
            target[i] = target[target[i]]
    return target
