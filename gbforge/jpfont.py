"""gbjp (GBDK 用日本語フォントライブラリ) のフォントデータ生成。

    gbforge jpfont --out lib/gbjp --scan src            # ソースで使っている文字だけ
    gbforge jpfont --out lib/gbjp --full                # JIS 第1・第2水準すべて (約 7000 字, 約 70KB)

出力 (lib/gbjp/):
    gbjp.h, gbjp.c              ライブラリ本体 (無ければコピー)
    font/gbjp_font_index.c      バンク 0: 文字数・バンク番号の問い合わせ関数
    font/gbjp_codes.c           コードポイント表 (#pragma bank 255)
    font/gbjp_glyphs_N.c        グリフ 1024 文字ずつ (#pragma bank 255)
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from .font import load_font

LIB_DIR = Path(__file__).parent / "gbjp"
CHUNK = 1024

# 必ず入れる文字: 数字・記号 (jp_print_int や代替文字で使う)
ALWAYS = "０１２３４５６７８９？-"

_STR_RE = re.compile(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'')
_COMMENT_RE = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)


def chars_in_source(code: str) -> set[str]:
    """C ソースの文字列・文字リテラルに含まれる文字 (コメントは除外)。"""
    code = _COMMENT_RE.sub("", code)
    out: set[str] = set()
    for lit in _STR_RE.findall(code):
        out.update(lit[1:-1])
    return out


def scan_dirs(dirs: list[Path], exts=(".c", ".h")) -> set[str]:
    chars: set[str] = set()
    for d in dirs:
        d = Path(d)
        files = [d] if d.is_file() else [p for p in d.rglob("*") if p.suffix in exts]
        for f in files:
            if "font" in f.parts and f.name.startswith("gbjp_"):
                continue
            chars |= chars_in_source(f.read_text(encoding="utf-8", errors="replace"))
    return chars


def _normalize(chars: set[str], font: dict) -> set[str]:
    """表示に必要なグリフの集合 (半角英数は全角グリフを使う)。"""
    need = set(ALWAYS)
    for ch in chars:
        cp = ord(ch)
        if cp < 0x20 or cp > 0xFFFF or ch in " 　\n\r\t":
            continue
        if 0x20 < cp < 0x7F and chr(cp + 0xFEE0) in font:
            need.add(chr(cp + 0xFEE0))
        elif ch in font:
            need.add(ch)
    return need


def _c_bytes(data: bytes) -> str:
    return "\n".join("    " + ",".join(f"0x{b:02X}" for b in data[i:i + 16]) + ","
                     for i in range(0, len(data), 16))


def install_library(out: Path, overwrite: bool = False):
    out.mkdir(parents=True, exist_ok=True)
    for name in ("gbjp.h", "gbjp.c"):
        dst = out / name
        if overwrite or not dst.exists():
            shutil.copy(LIB_DIR / name, dst)


def generate(out: Path, chars: set[str] | None = None, font_name: str = "misaki_gothic") -> dict:
    """フォントデータを out/font/ に生成する。chars=None なら全文字。"""
    font = load_font(font_name)
    if chars is None:
        glyphs = sorted(c for c in font if ord(c) <= 0xFFFF and c not in " 　")
        missing: list[str] = []
    else:
        glyphs = sorted(_normalize(chars, font) - {" ", "　"})
        missing = sorted(c for c in chars if ord(c) > 0x7E and c not in font and c not in "　\n\r\t")
    fdir = Path(out) / "font"
    fdir.mkdir(parents=True, exist_ok=True)
    for old in fdir.glob("gbjp_*.c"):
        old.unlink()

    head = "/* 自動生成: gbforge jpfont (美咲フォント {}) — 編集しないでください */\n".format(font_name)
    codes = [ord(c) for c in glyphs]
    src = [head, "#pragma bank 255", "#include <gbdk/platform.h>", "#include <stdint.h>", "",
           "BANKREF(gbjp_codes)", f"const uint16_t gbjp_codes[{max(1, len(codes))}] = {{"]
    for i in range(0, len(codes), 12):
        src.append("    " + ",".join(f"0x{c:04X}" for c in codes[i:i + 12]) + ",")
    if not codes:
        src.append("    0xFFFF,")
    src += ["};", ""]
    (fdir / "gbjp_codes.c").write_text("\n".join(src), encoding="utf-8")

    nchunks = max(1, (len(glyphs) + CHUNK - 1) // CHUNK)
    for ci in range(nchunks):
        part = glyphs[ci * CHUNK:(ci + 1) * CHUNK]
        data = b"".join(font[c] for c in part) or bytes(8)
        src = [head, "#pragma bank 255", "#include <gbdk/platform.h>", "#include <stdint.h>", "",
               f"BANKREF(gbjp_glyphs_{ci})", f"const uint8_t gbjp_glyphs_{ci}[{len(data)}] = {{", _c_bytes(data), "};", ""]
        (fdir / f"gbjp_glyphs_{ci}.c").write_text("\n".join(src), encoding="utf-8")

    idx = [head, "#include <gbdk/platform.h>", "#include <stdint.h>", "",
           "BANKREF_EXTERN(gbjp_codes)"]
    for ci in range(nchunks):
        idx += [f"BANKREF_EXTERN(gbjp_glyphs_{ci})", f"extern const uint8_t gbjp_glyphs_{ci}[];"]
    idx += ["", f"const uint16_t gbjp_count = {len(glyphs)};", "",
            "const uint8_t *const gbjp_chunks[] = {"]
    idx += [f"    gbjp_glyphs_{ci}," for ci in range(nchunks)]
    idx += ["};", "", "uint8_t gbjp_codes_bank(void) { return BANK(gbjp_codes); }", "",
            "uint8_t gbjp_chunk_bank(uint8_t chunk) {", "    switch (chunk) {"]
    idx += [f"        case {ci}: return BANK(gbjp_glyphs_{ci});" for ci in range(nchunks)]
    idx += ["    }", "    return 0;", "}", ""]
    (fdir / "gbjp_font_index.c").write_text("\n".join(idx), encoding="utf-8")

    rom_bytes = len(glyphs) * 10
    return {"glyphs": len(glyphs), "chunks": nchunks, "bytes": rom_bytes, "missing": missing,
            "files": sorted(str(p) for p in fdir.glob("gbjp_*.c"))}
