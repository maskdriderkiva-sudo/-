"""PNG 画像 → ゲームボーイ 2bpp タイル変換。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

MAX_BG_TILES = 128
SPRITE_SIZE = 16


class AssetError(Exception):
    pass


def _luma(rgb) -> float:
    r, g, b = rgb[:3]
    return 0.299 * r + 0.587 * g + 0.114 * b


def shade_of(rgba) -> int:
    """色 → GB の濃さ 0(白)..3(黒)。"""
    y = _luma(rgba)
    if y >= 192:
        return 0
    if y >= 128:
        return 1
    if y >= 64:
        return 2
    return 3


def encode_tile(pixels: list[list[int]]) -> bytes:
    """8x8 の色番号 (0..3) → 2bpp 16 バイト。"""
    out = bytearray()
    for row in pixels:
        lo = hi = 0
        for x, c in enumerate(row):
            if c & 1:
                lo |= 0x80 >> x
            if c & 2:
                hi |= 0x80 >> x
        out += bytes((lo, hi))
    return bytes(out)


def decode_tile(data: bytes) -> list[list[int]]:
    rows = []
    for y in range(8):
        lo, hi = data[y * 2], data[y * 2 + 1]
        rows.append([((lo >> (7 - x)) & 1) | (((hi >> (7 - x)) & 1) << 1) for x in range(8)])
    return rows


def _open(path: Path) -> Image.Image:
    if not path.exists():
        raise AssetError(f"画像が見つかりません: {path}")
    return Image.open(path).convert("RGBA")


def background_to_tiles(path: Path):
    """背景 PNG → (tiles: bytes, map: bytes, w, h)。同一タイルは重複除去。"""
    im = _open(path)
    w, h = im.size
    if w % 8 or h % 8:
        raise AssetError(f"{path.name}: 幅と高さは 8 の倍数にしてください ({w}x{h})")
    tw, th = w // 8, h // 8
    if not (20 <= tw <= 32 and 18 <= th <= 32):
        raise AssetError(f"{path.name}: 背景サイズは 160x144 〜 256x256 ピクセルにしてください ({w}x{h})")
    px = im.load()
    tiles: list[bytes] = []
    index: dict[bytes, int] = {}
    tmap = bytearray()
    for ty in range(th):
        for tx in range(tw):
            t = encode_tile([[shade_of(px[tx * 8 + x, ty * 8 + y]) for x in range(8)] for y in range(8)])
            if t not in index:
                index[t] = len(tiles)
                tiles.append(t)
            tmap.append(index[t])
    if len(tiles) > MAX_BG_TILES:
        raise AssetError(
            f"{path.name}: ユニークなタイルが {len(tiles)} 個あります (上限 {MAX_BG_TILES})。"
            "同じ模様のタイルを増やして減らしてください")
    return b"".join(tiles), bytes(tmap), tw, th


# スプライトパレット OBP0 = 0xE0: 色番号 1=白, 2=濃い灰, 3=黒 (0=透明)
_SPRITE_INDEX_FOR_SHADE = {0: 1, 1: 2, 2: 2, 3: 3}


def sprite_frames(path: Path) -> tuple[bytes, int]:
    """スプライトシート PNG (高さ16, 幅 16*N) → (データ, フレーム数)。
    透明 (alpha<128) は透過色。各フレームは TL,TR,BL,BR の 4 タイル = 64 バイト。"""
    im = _open(path)
    w, h = im.size
    if h != SPRITE_SIZE or w % SPRITE_SIZE or w == 0:
        raise AssetError(f"{path.name}: スプライトシートは高さ16px, 幅16の倍数にしてください ({w}x{h})")
    px = im.load()
    frames = w // SPRITE_SIZE
    out = bytearray()
    for f in range(frames):
        for oy, ox in ((0, 0), (0, 8), (8, 0), (8, 8)):
            rows = []
            for y in range(8):
                row = []
                for x in range(8):
                    p = px[f * 16 + ox + x, oy + y]
                    row.append(0 if p[3] < 128 else _SPRITE_INDEX_FOR_SHADE[shade_of(p)])
                rows.append(row)
            out += encode_tile(rows)
    return bytes(out), frames


def image_info(path: Path) -> dict:
    im = Image.open(path)
    return {"width": im.size[0], "height": im.size[1]}
