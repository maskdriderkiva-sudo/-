"""画像の読み込み・調整・160x144 への変換・ディザリング・減色。

色番号の約束 (DMG): 0 = 一番明るい (白) … 3 = 一番暗い (黒)。BGP=0xE4 でそのまま表示される。
CGB: 1 タイル (8x8) は 8 本の BG パレットのうち 1 本だけ、そのパレットの 4 色だけを使える。
     パレットの色は RGB555 (各 5 ビット)。各パレットは明るい順に並べる (0 = 一番明るい)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from . import SCREEN_H, SCREEN_W

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif"}

DEFAULT_PARAMS = {
    "fit": "fill",            # fill (はみ出しを切る) / fit (余白を足す) / stretch (引き伸ばす)
    "zoom": 1.0,              # 1.0〜4.0: 拡大して一部を切り出す
    "crop_x": 0.5,            # 0〜1: 切り出す位置 (横)
    "crop_y": 0.5,            # 0〜1: 切り出す位置 (縦)
    "pad": "black",           # fit のときの余白の色 black / white
    "brightness": 0,          # -100〜100
    "contrast": 0,            # -100〜100
    "gamma": 1.0,             # 0.3〜3.0 (1 より大きいと明るく)
    "saturation": 0,          # -100〜100 (カラーのみ)
    "black_point": 0,         # 0〜255 これより暗い所を黒に
    "white_point": 255,       # 0〜255 これより明るい所を白に
    "sharpen": 0.0,           # 0〜3 輪郭を強調
    "denoise": 0,             # 0〜3 ノイズを減らす
    "grayscale": False,       # カラーでも白黒にする
    "dither": "fs",           # none / fs / atkinson / bayer2 / bayer4 / bayer8
    "dither_strength": 0.8,   # 0〜1
    "palettes": 8,            # カラー: 使う BG パレット数 (1〜8)
    "tile_limit": 0,          # 0 = 自動 (DMG 256 / CGB 512)。固有タイル数の上限
}

# 写真らしさを残すためのプリセット (実機で再現できる範囲の調整だけ)
PRESETS = {
    "standard": {"label": "標準", "params": {}},
    "old_mono": {"label": "古いモノクロ写真", "params": {
        "grayscale": True, "contrast": -10, "gamma": 1.1, "denoise": 1, "sharpen": 0.6,
        "dither": "fs", "dither_strength": 0.75, "black_point": 18, "white_point": 235}},
    "horror": {"label": "高コントラスト・ホラー", "params": {
        "contrast": 45, "brightness": -18, "gamma": 0.8, "sharpen": 1.2, "dither": "atkinson",
        "dither_strength": 0.9, "saturation": -40, "black_point": 40}},
    "dim_room": {"label": "薄暗い室内", "params": {
        "brightness": -12, "gamma": 1.35, "contrast": 12, "denoise": 2, "dither": "bayer4",
        "dither_strength": 0.7, "saturation": -15}},
    "grainy": {"label": "粗い写真", "params": {
        "contrast": 20, "sharpen": 1.6, "dither": "fs", "dither_strength": 1.0, "denoise": 0}},
    "retro_film": {"label": "レトロフィルム", "params": {
        "contrast": -8, "gamma": 1.05, "saturation": -30, "dither": "bayer8", "dither_strength": 0.85,
        "black_point": 25, "white_point": 225, "denoise": 1}},
    "flat_art": {"label": "イラスト・ドット絵 (ディザなし)", "params": {
        "dither": "none", "sharpen": 0.0, "denoise": 0}},
}


def merged_params(p: dict | None) -> dict:
    out = dict(DEFAULT_PARAMS)
    if p:
        preset = PRESETS.get(p.get("preset", ""), {}).get("params", {})
        out.update(preset)
        out.update({k: v for k, v in p.items() if k != "preset"})
    return out


# ---------------------------------------------------------------------------
# 読み込み
# ---------------------------------------------------------------------------
def load_image(path: Path | str, frame: int = 0) -> Image.Image:
    im = Image.open(path)
    if getattr(im, "n_frames", 1) > 1:
        im.seek(min(frame, im.n_frames - 1))
    im = ImageOps.exif_transpose(im) if frame == 0 else im
    return im.convert("RGBA")


def gif_frames(path: Path | str) -> tuple[list[Image.Image], list[float]]:
    """アニメーション GIF / WebP のフレームと各フレームの表示時間 (秒)。"""
    im = Image.open(path)
    frames, durs = [], []
    for i in range(getattr(im, "n_frames", 1)):
        im.seek(i)
        frames.append(im.convert("RGBA"))
        durs.append(max(0.02, (im.info.get("duration") or 100) / 1000.0))
    return frames, durs


# ---------------------------------------------------------------------------
# 160x144 への変換と調整
# ---------------------------------------------------------------------------
def fit_image(im: Image.Image, p: dict, w: int = SCREEN_W, h: int = SCREEN_H) -> Image.Image:
    bg = (255, 255, 255) if p.get("pad") == "white" else (0, 0, 0)
    if im.mode == "RGBA":
        base = Image.new("RGB", im.size, bg)
        base.paste(im, mask=im.split()[3])
        im = base
    else:
        im = im.convert("RGB")
    sw, sh = im.size
    zoom = max(1.0, float(p.get("zoom", 1.0)))
    if zoom > 1.0:   # 拡大: 指定位置を中心に切り出す
        cw, ch = sw / zoom, sh / zoom
        cx = (sw - cw) * float(p.get("crop_x", 0.5))
        cy = (sh - ch) * float(p.get("crop_y", 0.5))
        im = im.crop((int(cx), int(cy), int(cx + cw), int(cy + ch)))
        sw, sh = im.size
    mode = p.get("fit", "fill")
    if mode == "stretch":
        return im.resize((w, h), Image.LANCZOS)
    if mode == "fit":
        s = min(w / sw, h / sh)
        nw, nh = max(1, round(sw * s)), max(1, round(sh * s))
        out = Image.new("RGB", (w, h), bg)
        out.paste(im.resize((nw, nh), Image.LANCZOS), ((w - nw) // 2, (h - nh) // 2))
        return out
    s = max(w / sw, h / sh)
    nw, nh = max(w, round(sw * s)), max(h, round(sh * s))
    big = im.resize((nw, nh), Image.LANCZOS)
    ox = round((nw - w) * float(p.get("crop_x", 0.5)))
    oy = round((nh - h) * float(p.get("crop_y", 0.5)))
    return big.crop((ox, oy, ox + w, oy + h))


def adjust(im: Image.Image, p: dict) -> np.ndarray:
    """明るさ・コントラスト・ガンマなどを適用して float32 (h, w, 3) 0..1 を返す。"""
    d = int(p.get("denoise", 0) or 0)
    if d > 0:
        im = im.filter(ImageFilter.MedianFilter(3))
        if d > 1:
            im = im.filter(ImageFilter.GaussianBlur(0.4 * (d - 1)))
    sh = float(p.get("sharpen", 0) or 0)
    if sh > 0:
        im = im.filter(ImageFilter.UnsharpMask(radius=1.2, percent=int(60 * sh), threshold=1))
    a = np.asarray(im, dtype=np.float32) / 255.0
    bp, wp = float(p.get("black_point", 0)) / 255.0, float(p.get("white_point", 255)) / 255.0
    if wp - bp > 0.02 and (bp > 0 or wp < 1):
        a = np.clip((a - bp) / (wp - bp), 0, 1)
    sat = float(p.get("saturation", 0)) / 100.0
    gray = (a @ np.array([0.299, 0.587, 0.114], np.float32))[..., None]
    if p.get("grayscale"):
        a = np.repeat(gray, 3, axis=2)
    elif sat:
        a = np.clip(gray + (a - gray) * (1 + sat), 0, 1)
    c = float(p.get("contrast", 0)) / 100.0
    if c:
        a = np.clip((a - 0.5) * (1 + c * 1.5 if c > 0 else 1 + c) + 0.5, 0, 1)
    b = float(p.get("brightness", 0)) / 100.0
    if b:
        a = np.clip(a + b * 0.5, 0, 1)
    g = float(p.get("gamma", 1.0) or 1.0)
    if abs(g - 1.0) > 1e-3:
        a = np.power(a, 1.0 / g)
    return a.astype(np.float32)


def luminance(a: np.ndarray) -> np.ndarray:
    return a @ np.array([0.299, 0.587, 0.114], np.float32)


# ---------------------------------------------------------------------------
# ディザリング
# ---------------------------------------------------------------------------
def bayer(n: int) -> np.ndarray:
    m = np.array([[0, 2], [3, 1]], np.float32)
    while m.shape[0] < n:
        m = np.block([[4 * m, 4 * m + 2], [4 * m + 3, 4 * m + 1]])
    return (m + 0.5) / (m.size) - 0.5          # -0.5〜0.5


FS_W = [(0, 1, 7 / 16), (1, -1, 3 / 16), (1, 0, 5 / 16), (1, 1, 1 / 16)]
ATK_W = [(0, 1, 1 / 8), (0, 2, 1 / 8), (1, -1, 1 / 8), (1, 0, 1 / 8), (1, 1, 1 / 8), (2, 0, 1 / 8)]


def quantize_dmg(a: np.ndarray, method: str = "fs", strength: float = 0.8) -> np.ndarray:
    """4 階調に減色。(h, w) uint8 0 (白)〜3 (黒)。"""
    v = luminance(a) * 3.0                      # 0 (黒)〜3 (白)
    h, w = v.shape
    if method.startswith("bayer"):
        n = int(method[5:] or 4)
        t = np.tile(bayer(n), (h // n + 1, w // n + 1))[:h, :w]
        q = np.clip(np.rint(v + t * strength), 0, 3)
    elif method in ("fs", "atkinson"):
        wts = FS_W if method == "fs" else ATK_W
        buf = v.astype(np.float64).copy()
        q = np.zeros_like(buf)
        for y in range(h):
            row = buf[y]
            for x in range(w):
                old = row[x]
                new = 3.0 if old >= 2.5 else 2.0 if old >= 1.5 else 1.0 if old >= 0.5 else 0.0
                q[y, x] = new
                err = (old - new) * strength
                if err:
                    for dy, dx, wt in wts:
                        yy, xx = y + dy, x + dx
                        if yy < h and 0 <= xx < w:
                            buf[yy, xx] += err * wt
    else:
        q = np.clip(np.rint(v), 0, 3)
    return (3 - q).astype(np.uint8)


# ---------------------------------------------------------------------------
# カラー (CGB): タイルごとのパレット割り当て
# ---------------------------------------------------------------------------
def _kmeans(pts: np.ndarray, k: int, iters: int = 10, seed: int = 0) -> np.ndarray:
    """小さな k-means (k-means++ 初期化)。pts: (n, 3)。"""
    rng = np.random.default_rng(seed)
    n = len(pts)
    if n == 0:
        return np.zeros((k, 3), np.float32)
    uniq = np.unique(pts, axis=0)
    if len(uniq) <= k:
        c = np.concatenate([uniq, np.repeat(uniq[-1:], k - len(uniq), axis=0)])
        return c.astype(np.float32)
    centers = [pts[rng.integers(n)]]
    for _ in range(1, k):
        d = np.min(((pts[:, None, :] - np.array(centers)[None]) ** 2).sum(-1), axis=1)
        s = d.sum()
        centers.append(pts[rng.choice(n, p=d / s)] if s > 0 else pts[rng.integers(n)])
    c = np.array(centers, np.float32)
    for _ in range(iters):
        lab = np.argmin(((pts[:, None, :] - c[None]) ** 2).sum(-1), axis=1)
        for j in range(k):
            sel = pts[lab == j]
            if len(sel):
                c[j] = sel.mean(axis=0)
    return c


def to5(c: np.ndarray) -> np.ndarray:
    """0..1 の色を 5 ビット (0..31) に。"""
    return np.clip(np.rint(c * 31.0), 0, 31).astype(np.int32)


def from5(c5: np.ndarray) -> np.ndarray:
    return c5.astype(np.float32) / 31.0


def tiles_of(a: np.ndarray) -> np.ndarray:
    """(144, 160, 3) → (360, 64, 3)"""
    h, w = a.shape[:2]
    t = a.reshape(h // 8, 8, w // 8, 8, -1).transpose(0, 2, 1, 3, 4)
    return t.reshape((h // 8) * (w // 8), 64, -1)


def build_cgb_palettes(images: list[np.ndarray], n_pal: int = 8, iters: int = 6) -> np.ndarray:
    """複数の画像 (動画なら複数フレーム) のタイルから BG パレット n_pal 本 x 4 色 (5 ビット) を作る。"""
    tiles = np.concatenate([tiles_of(a) for a in images])          # (N, 64, 3)
    N = len(tiles)
    # 初期値: 平均色の異なるタイルを選ぶ
    means = tiles.mean(axis=1)
    order = [int(np.argmax(np.linalg.norm(means - means.mean(0), axis=1)))]
    for _ in range(1, n_pal):
        d = np.min(np.linalg.norm(means[:, None] - means[order][None], axis=2), axis=1)
        order.append(int(np.argmax(d)))
    pals = np.stack([_kmeans(tiles[i], 4, seed=i) for i in order])  # (P, 4, 3)
    for it in range(iters):
        err = _tile_errors(tiles, pals)                              # (N, P)
        lab = np.argmin(err, axis=1)
        for p in range(n_pal):
            sel = tiles[lab == p].reshape(-1, 3)
            if len(sel) == 0:
                worst = int(np.argmax(err[np.arange(N), lab]))      # 一番合っていないタイルで作り直す
                pals[p] = _kmeans(tiles[worst], 4, seed=p + 100)
            else:
                if len(sel) > 6000:
                    sel = sel[np.random.default_rng(it).choice(len(sel), 6000, replace=False)]
                pals[p] = _kmeans(sel, 4, seed=p + it)
    out = to5(pals)
    # 明るい順に並べる
    for p in range(n_pal):
        lum = (out[p] * np.array([0.299, 0.587, 0.114])).sum(1)
        out[p] = out[p][np.argsort(-lum, kind="stable")]
    return out


def _tile_errors(tiles: np.ndarray, pals: np.ndarray) -> np.ndarray:
    """各タイルを各パレットで表したときの誤差 (N, P)。"""
    out = np.empty((len(tiles), len(pals)), np.float32)
    for p in range(len(pals)):
        d = ((tiles[:, :, None, :] - pals[p][None, None]) ** 2).sum(-1)   # (N, 64, 4)
        out[:, p] = d.min(axis=2).sum(axis=1)
    return out


def assign_tile_palettes(a: np.ndarray, pals5: np.ndarray) -> np.ndarray:
    """各タイルに一番合うパレット番号 (18, 20)。"""
    err = _tile_errors(tiles_of(a), from5(pals5))
    return np.argmin(err, axis=1).reshape(SCREEN_H // 8, SCREEN_W // 8).astype(np.uint8)


def quantize_cgb(a: np.ndarray, pals5: np.ndarray, tile_pal: np.ndarray, method: str = "fs",
                 strength: float = 0.8) -> np.ndarray:
    """タイルごとに決めたパレットの 4 色で減色 (ディザリング付き)。(h, w) 0..3"""
    h, w = a.shape[:2]
    pal = from5(pals5)                                   # (P, 4, 3)
    pix_pal = np.repeat(np.repeat(tile_pal, 8, axis=0), 8, axis=1)   # (h, w)
    cols = pal[pix_pal]                                  # (h, w, 4, 3)
    if method.startswith("bayer"):
        n = int(method[5:] or 4)
        t = np.tile(bayer(n), (h // n + 1, w // n + 1))[:h, :w][..., None]
        # パレット内の色の間隔に合わせてずらす
        spread = np.ptp(cols, axis=2).max(axis=-1, keepdims=True) / 3.0
        b = a + t * spread * strength
        return np.argmin(((b[:, :, None, :] - cols) ** 2).sum(-1), axis=2).astype(np.uint8)
    if method in ("fs", "atkinson"):
        wts = FS_W if method == "fs" else ATK_W
        buf = a.astype(np.float64).copy()
        out = np.zeros((h, w), np.uint8)
        for y in range(h):
            for x in range(w):
                old = buf[y, x]
                c = cols[y, x]
                d = ((c - old) ** 2).sum(axis=1)
                i = int(np.argmin(d))
                out[y, x] = i
                err = (old - c[i]) * strength
                for dy, dx, wt in wts:
                    yy, xx = y + dy, x + dx
                    if yy < h and 0 <= xx < w:
                        buf[yy, xx] += err * wt
        return out
    return np.argmin(((a[:, :, None, :] - cols) ** 2).sum(-1), axis=2).astype(np.uint8)


# ---------------------------------------------------------------------------
# まとめ
# ---------------------------------------------------------------------------
@dataclass
class Indexed:
    """減色済みの 1 画面。"""
    target: str                          # dmg / cgb
    idx: np.ndarray                      # (144, 160) 色番号 0..3
    tile_pal: np.ndarray | None = None   # (18, 20) CGB のパレット番号
    pals5: np.ndarray | None = None      # (8, 4, 3) CGB パレット (5 ビット)
    source_rgb: np.ndarray | None = field(default=None, repr=False)   # 比較用の調整後画像

    def rgb(self) -> np.ndarray:
        """表示色 (uint8 RGB)。DMG はグレー 4 階調。"""
        if self.target == "dmg":
            g = np.array([255, 170, 85, 0], np.uint8)[self.idx]
            return np.stack([g, g, g], axis=-1)
        pix_pal = np.repeat(np.repeat(self.tile_pal, 8, axis=0), 8, axis=1)
        c5 = self.pals5[pix_pal, self.idx]
        return ((c5 << 3) | (c5 >> 2)).astype(np.uint8)


def convert_frame(im: Image.Image, params: dict, target: str, pals5: np.ndarray | None = None) -> Indexed:
    p = merged_params(params)
    a = adjust(fit_image(im, p), p)
    if target == "dmg":
        return Indexed("dmg", quantize_dmg(a, p["dither"], float(p["dither_strength"])), source_rgb=a)
    if p.get("grayscale"):
        a = np.repeat(luminance(a)[..., None], 3, axis=2)
    n_pal = max(1, min(8, int(p.get("palettes", 8))))
    if pals5 is None:
        pals5 = build_cgb_palettes([a], n_pal)
    tp = assign_tile_palettes(a, pals5)
    idx = quantize_cgb(a, pals5, tp, p["dither"], float(p["dither_strength"]))
    full = np.zeros((8, 4, 3), np.int32)
    full[:len(pals5)] = pals5
    return Indexed("cgb", idx, tp, full, source_rgb=a)


def source_preview(im: Image.Image, params: dict) -> np.ndarray:
    """比較表示用: 調整だけした (減色前の) 160x144 画像 uint8。"""
    p = merged_params(params)
    return (np.clip(adjust(fit_image(im, p), p), 0, 1) * 255).astype(np.uint8)
