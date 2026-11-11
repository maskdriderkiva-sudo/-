"""画像 ⇄ ゲームボーイ / ゲームボーイカラーのデータ変換ツール。

- PNG → タイル (2bpp) / スプライト (8x8, 8x16 並び) / 背景マップ (重複タイル除去 + マップ + CGB 属性)
- ゲームボーイカラー: タイルごとの色から BG/OBJ パレット (最大 8 本 x 4 色) を自動で組み立てる
- 初代: 明るさで 4 階調に変換
- 2bpp タイル → PNG (既存の C 配列をプレビュー・書き出し)
- PNG → 2bpp (書き出した PNG を編集して C 配列へ戻す)
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from .carray import DMG_GRAYS, palette_rgb, rgb5


class ConvertError(Exception):
    pass


def luma(c) -> float:
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def to5(c) -> tuple[int, int, int]:
    return (c[0] >> 3, c[1] >> 3, c[2] >> 3)


def from5(c) -> tuple[int, int, int]:
    return tuple((v << 3) | (v >> 2) for v in c)


# ---------------------------------------------------------------------------
# 2bpp
# ---------------------------------------------------------------------------
def encode_tile(idx: list[list[int]]) -> bytes:
    out = bytearray()
    for row in idx:
        lo = hi = 0
        for x, c in enumerate(row):
            if c & 1:
                lo |= 0x80 >> x
            if c & 2:
                hi |= 0x80 >> x
        out += bytes((lo, hi))
    return bytes(out)


def decode_tile(b: bytes) -> list[list[int]]:
    return [[((b[y * 2] >> (7 - x)) & 1) | (((b[y * 2 + 1] >> (7 - x)) & 1) << 1) for x in range(8)] for y in range(8)]


def tiles_to_image(data: bytes, palette: list[tuple] | None = None, columns: int = 16,
                   order: str = "tiles", sprite_w: int = 16, sprite_h: int = 16) -> Image.Image:
    """2bpp データを画像にする。order=sprite8x16 なら 8x16 の並びで組み立てる。"""
    pal = palette or DMG_GRAYS
    n = len(data) // 16
    if order == "sprite8x16":
        cols_per_frame = sprite_w // 8
        rows_per_frame = sprite_h // 16
        tiles_per_frame = cols_per_frame * rows_per_frame * 2
        frames = max(1, n // tiles_per_frame)
        im = Image.new("RGB", (sprite_w * frames, sprite_h), pal[0])
        for f in range(frames):
            for i in range(tiles_per_frame):
                t = f * tiles_per_frame + i
                if t >= n:
                    break
                col = (i // 2) % cols_per_frame
                row = (i // 2) // cols_per_frame
                _paste(im, data[t * 16:(t + 1) * 16], pal, f * sprite_w + col * 8, row * 16 + (i & 1) * 8)
        return im
    cols = max(1, min(columns, n))
    rows = (n + cols - 1) // cols
    im = Image.new("RGB", (cols * 8, max(1, rows) * 8), pal[0])
    for t in range(n):
        _paste(im, data[t * 16:(t + 1) * 16], pal, (t % cols) * 8, (t // cols) * 8)
    return im


def _paste(im, tile: bytes, pal, ox, oy):
    px = im.load()
    for y, row in enumerate(decode_tile(tile)):
        for x, c in enumerate(row):
            px[ox + x, oy + y] = tuple(pal[c])


def image_to_tiles_with_palette(im: Image.Image, palette: list[tuple], columns: int | None = None) -> bytes:
    """パレットの色 (最も近い色) で 2bpp に戻す。画像は 8 の倍数サイズ。"""
    im = im.convert("RGBA")
    w, h = im.size
    if w % 8 or h % 8:
        raise ConvertError("画像の幅と高さは 8 の倍数にしてください")
    px = im.load()
    out = bytearray()
    cols = w // 8
    for ty in range(h // 8):
        for tx in range(cols):
            rows = []
            for y in range(8):
                row = []
                for x in range(8):
                    p = px[tx * 8 + x, ty * 8 + y]
                    if p[3] < 128:
                        row.append(0)
                    else:
                        row.append(min(range(len(palette)), key=lambda i: sum((a - b) ** 2 for a, b in zip(p, palette[i]))))
                rows.append(row)
            out += encode_tile(rows)
    return bytes(out)


# ---------------------------------------------------------------------------
# PNG → GB データ
# ---------------------------------------------------------------------------
def _frame_w(res) -> int:
    return getattr(res, "sprite_w", 16)


def _frame_h(res) -> int:
    return getattr(res, "sprite_h", 16)


@dataclass
class Converted:
    name: str
    mode: str
    tiles: bytes = b""
    map: list[int] = field(default_factory=list)
    attrs: list[int] = field(default_factory=list)
    palettes: list[list[tuple]] = field(default_factory=list)
    width: int = 0          # px
    height: int = 0
    map_w: int = 0
    map_h: int = 0
    frames: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def tile_count(self) -> int:
        return len(self.tiles) // 16


def _cells(im: Image.Image, cw: int, ch: int):
    w, h = im.size
    for cy in range(h // ch):
        for cx in range(w // cw):
            yield cx, cy


def _build_palettes(cell_colors: list[set], max_pal: int, per: int) -> tuple[list[list[tuple]], list[int]]:
    """各セルの色集合を最大 max_pal 本のパレット (各 per 色) にまとめる。"""
    pals: list[set] = []
    assign = []
    order = sorted(range(len(cell_colors)), key=lambda i: -len(cell_colors[i]))
    tmp_assign = {}
    for i in order:
        cs = cell_colors[i]
        if len(cs) > per:
            raise ConvertError(f"8x8 (または 8x16) の 1 マスに {len(cs)} 色あります。1 マス {per} 色までにしてください")
        best = None
        for pi, p in enumerate(pals):
            if cs <= p:
                best = pi
                break
        if best is None:
            for pi, p in enumerate(pals):
                if len(p | cs) <= per:
                    best = pi
                    p |= cs
                    break
        if best is None:
            pals.append(set(cs))
            best = len(pals) - 1
        tmp_assign[i] = best
    if len(pals) > max_pal:
        raise ConvertError(f"パレットが {len(pals)} 本必要です (最大 {max_pal} 本)。色数を減らしてください")
    assign = [tmp_assign[i] for i in range(len(cell_colors))]
    out = []
    for p in pals:
        cols = sorted(p, key=lambda c: -luma(from5(c)))   # 明るい順 (0 = 一番明るい)
        out.append([from5(c) for c in cols])
    return out, assign


def convert_image(path_or_img, name: str, mode: str = "map", target: str = "cgb", dedupe: bool = True,
                  sprite_w: int = 16, sprite_h: int = 16, tile_origin: int = 0, max_palettes: int = 8) -> Converted:
    """mode: tiles / sprite8x8 / sprite8x16 / map"""
    im = path_or_img if isinstance(path_or_img, Image.Image) else Image.open(path_or_img)
    im = im.convert("RGBA")
    w, h = im.size
    sprite = mode.startswith("sprite")
    ch = 16 if mode == "sprite8x16" else 8
    if w % 8 or h % ch:
        raise ConvertError(f"画像サイズ {w}x{h} は 8x{ch} の倍数にしてください")
    if sprite and (w % sprite_w or h % sprite_h or sprite_w % 8 or sprite_h % ch):
        raise ConvertError(f"スプライトの大きさ {sprite_w}x{sprite_h} で画像 {w}x{h} を割り切れません")
    px = im.load()
    res = Converted(name=name, mode=mode, width=w, height=h)
    res.sprite_w, res.sprite_h = sprite_w, sprite_h

    # セル (CGB の色単位) ごとの色
    def cell_pixels(cx, cy, cw, chh):
        return [[px[cx * cw + x, cy * chh + y] for x in range(cw)] for y in range(chh)]

    # ---- 色 → 色番号 ----
    if target == "dmg":
        def index_cell(cell):
            out = []
            for row in cell:
                r = []
                for p in row:
                    if sprite and p[3] < 128:
                        r.append(0)
                    else:
                        y = luma(p)
                        v = 0 if y >= 192 else 1 if y >= 128 else 2 if y >= 64 else 3
                        if sprite and v == 0:
                            v = 1
                        r.append(v)
                out.append(r)
            return out, 0
        res.palettes = [list(DMG_GRAYS)]
        cell_w, cell_h = 8, ch
        indexer = index_cell
    else:
        cell_w, cell_h = 8, ch
        cells = list(_cells(im, cell_w, cell_h))
        colors = []
        for cx, cy in cells:
            cs = set()
            for row in cell_pixels(cx, cy, cell_w, cell_h):
                for p in row:
                    if sprite and p[3] < 128:
                        continue
                    cs.add(to5(p))
            colors.append(cs)
        pals, assign = _build_palettes(colors, max_palettes, 3 if sprite else 4)
        if sprite:
            pals = [[(0, 0, 0)] + p + [(0, 0, 0)] * (3 - len(p)) for p in pals]   # 0 番は透明
        else:
            pals = [p + [p[-1] if p else (0, 0, 0)] * (4 - len(p)) for p in pals]
        res.palettes = pals or [list(DMG_GRAYS)]
        cell_pal = {cells[i]: assign[i] for i in range(len(cells))}

        def indexer(cell, key=None):
            pi = cell_pal[key]
            pal5 = [to5(c) for c in res.palettes[pi]]
            out = []
            for row in cell:
                r = []
                for p in row:
                    if sprite and p[3] < 128:
                        r.append(0)
                    else:
                        c = to5(p)
                        start = 1 if sprite else 0
                        r.append(min(range(start, 4), key=lambda i: sum((a - b) ** 2 for a, b in zip(c, pal5[i]))))
                out.append(r)
            return out, pi

    def idx_of(cx, cy):
        cell = cell_pixels(cx, cy, cell_w, cell_h)
        if target == "dmg":
            return indexer(cell)
        return indexer(cell, (cx, cy))

    # ---- 並べ方 ----
    tiles: list[bytes] = []
    if mode == "map":
        index: dict[bytes, int] = {}
        for cy in range(h // 8):
            for cx in range(w // 8):
                rows, pi = idx_of(cx, cy)
                t = encode_tile(rows)
                if dedupe and t in index:
                    ti = index[t]
                else:
                    ti = len(tiles)
                    tiles.append(t)
                    index[t] = ti
                res.map.append((ti + tile_origin) & 0xFF)
                res.attrs.append(pi & 7)
        res.map_w, res.map_h = w // 8, h // 8
        if len(tiles) + tile_origin > 256:
            res.warnings.append(f"タイルが {len(tiles)} 枚あります (VRAM の 1 バンクは 256 枚まで)")
    elif mode == "tiles":
        for cy in range(h // 8):
            for cx in range(w // 8):
                rows, pi = idx_of(cx, cy)
                tiles.append(encode_tile(rows))
                res.attrs.append(pi)
    else:
        fw, fh = sprite_w, sprite_h
        for fy in range(h // fh):
            for fx in range(w // fw):
                for ty in range(fh // ch):
                    for tx in range(fw // 8):
                        cx, cy = (fx * fw) // 8 + tx, (fy * fh) // ch + ty
                        rows, pi = idx_of(cx, cy)
                        if ch == 16:
                            tiles.append(encode_tile(rows[:8]))
                            tiles.append(encode_tile(rows[8:]))
                            res.attrs += [pi, pi]          # タイルごとに揃える
                        else:
                            tiles.append(encode_tile(rows))
                            res.attrs.append(pi)
                res.frames += 1
    res.tiles = b"".join(tiles)
    return res


# ---------------------------------------------------------------------------
# C ソース出力
# ---------------------------------------------------------------------------
def _carr(ctype, name, vals, fmt="0x{:02X}", per=16):
    s = f"const {ctype} {name}[{len(vals)}] = {{\n"
    for i in range(0, len(vals), per):
        s += "    " + ", ".join(fmt.format(v) for v in vals[i:i + per]) + ",\n"
    return s + "};\n"


def to_c(res: Converted, bank: int | None = None) -> tuple[str, str]:
    n = re.sub(r"\W", "_", res.name)
    up = n.upper()
    head = f"/* GBForge 変換ツールで生成 ({res.mode}, {res.width}x{res.height}) */\n"
    c = head + (f"#pragma bank {bank}\n" if bank is not None else "") + \
        "#include <gb/gb.h>\n#include <gb/cgb.h>\n" + f'#include "{n}.h"\n\n'
    hh = head + f"#ifndef {up}_H\n#define {up}_H\n#include <gb/gb.h>\n#include <gb/cgb.h>\n\n"
    hh += f"#define {up}_TILE_COUNT {res.tile_count}\n"
    c += _carr("unsigned char", f"{n}_tiles", list(res.tiles)) + "\n"
    hh += f"extern const unsigned char {n}_tiles[{len(res.tiles)}];\n"
    if res.mode == "map":
        hh += f"#define {up}_MAP_W {res.map_w}\n#define {up}_MAP_H {res.map_h}\n"
        c += _carr("unsigned char", f"{n}_map", res.map, per=max(1, min(32, res.map_w))) + "\n"
        hh += f"extern const unsigned char {n}_map[{len(res.map)}];\n"
        if len(res.palettes) > 0 and any(res.attrs):
            c += _carr("unsigned char", f"{n}_attr", res.attrs, per=max(1, min(32, res.map_w))) + "\n"
            hh += f"extern const unsigned char {n}_attr[{len(res.attrs)}];  /* CGB: VBK_REG=1 で set_bkg_tiles */\n"
    if res.mode.startswith("sprite"):
        hh += f"#define {up}_FRAMES {res.frames}\n#define {up}_TILES_PER_FRAME {res.tile_count // max(1, res.frames)}\n"
        if res.attrs:
            c += _carr("unsigned char", f"{n}_tile_pal", res.attrs) + "\n"
            hh += f"extern const unsigned char {n}_tile_pal[{len(res.attrs)}];  /* タイルごとのパレット番号 */\n"
    pal_vals = [rgb5(*to5(col)) for p in res.palettes for col in p]
    hh += f"#define {up}_PALETTE_COUNT {len(res.palettes)}\n"
    c += f"const palette_color_t {n}_palettes[{len(pal_vals)}] = {{\n"
    for p in res.palettes:
        c += "    " + ", ".join(f"RGB({a >> 3}, {b >> 3}, {cc >> 3})" for a, b, cc in p) + ",\n"
    c += "};\n"
    hh += f"extern const palette_color_t {n}_palettes[{len(pal_vals)}];\n\n#endif\n"
    return c, hh


def preview_png(res: Converted, scale: int = 3) -> bytes:
    """変換結果をデータから描き直したプレビュー (正しく変換できたかの確認用)。"""
    if res.mode == "map":
        im = Image.new("RGB", (res.map_w * 8, res.map_h * 8))
        for i, ti in enumerate(res.map):
            pal = res.palettes[res.attrs[i]] if res.attrs else res.palettes[0]
            t = res.tiles[(ti) * 16:(ti + 1) * 16] if ti * 16 < len(res.tiles) else bytes(16)
            _paste(im, t, pal, (i % res.map_w) * 8, (i // res.map_w) * 8)
    elif res.mode.startswith("sprite"):
        # フレームを組み立てて表示 (8x16 は 上→下 の 2 タイルが 1 列)
        im = Image.new("RGB", (res.width, res.height), (40, 40, 48))
        ch = 16 if res.mode == "sprite8x16" else 8
        per = res.tile_count // max(1, res.frames)
        fw = res.width // max(1, res.width // max(8, _frame_w(res)))
        fh = res.height // max(1, res.height // max(ch, _frame_h(res)))
        cols_f = res.width // fw
        t = 0
        for f in range(res.frames):
            ox, oy = (f % cols_f) * fw, (f // cols_f) * fh
            for ty in range(fh // ch):
                for tx in range(fw // 8):
                    for half in range(ch // 8):
                        pal = res.palettes[res.attrs[t]] if t < len(res.attrs) and res.attrs[t] < len(res.palettes) else res.palettes[0]
                        _paste(im, res.tiles[t * 16:(t + 1) * 16], pal, ox + tx * 8, oy + ty * ch + half * 8)
                        t += 1
        _ = per
    else:
        n = res.tile_count
        cols = min(16, max(1, n))
        im = Image.new("RGB", (cols * 8, ((n + cols - 1) // cols) * 8), (40, 40, 48))
        for t in range(n):
            pal = res.palettes[res.attrs[t]] if t < len(res.attrs) and res.attrs[t] < len(res.palettes) else res.palettes[0]
            _paste(im, res.tiles[t * 16:(t + 1) * 16], pal, (t % cols) * 8, (t // cols) * 8)
    im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def palette_from_values(values: list[int] | None, index: int = 0) -> list[tuple]:
    if not values:
        return list(DMG_GRAYS)
    rgb = palette_rgb(values)
    pal = rgb[index * 4:index * 4 + 4]
    return pal if len(pal) == 4 else list(DMG_GRAYS)


def png_bytes(im: Image.Image, scale: int = 1) -> bytes:
    if scale != 1:
        im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def save_c(res: Converted, out_dir: Path, bank: int | None = None) -> list[Path]:
    c, hh = to_c(res, bank)
    n = re.sub(r"\W", "_", res.name)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{n}.c").write_text(c, encoding="utf-8")
    (out_dir / f"{n}.h").write_text(hh, encoding="utf-8")
    return [out_dir / f"{n}.c", out_dir / f"{n}.h"]
