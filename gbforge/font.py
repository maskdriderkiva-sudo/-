"""日本語フォント (美咲フォント 8x8) とテキストエンコード。

ゲームボーイ側では 1 文字 = 8x8 タイル。プロジェクト内で使われている文字だけを
抽出してグリフテーブルを作り、文字列をグリフ番号 (uint16) 列に変換する。
"""
from __future__ import annotations

import struct
import unicodedata
from functools import lru_cache
from pathlib import Path

FONT_DIR = Path(__file__).parent / "fonts"

# 制御コード (engine/gbf_types.h と一致させること)
TC_END = 0xFFFF
TC_NEWLINE = 0xFFFE
TC_PAGE = 0xFFFD
TC_NUMBER = 0xFFFC

COLS = 18
ROWS = 3

# 先頭に固定配置するグリフ: 空白, 0-9, マイナス, ▶, ▼
FIXED_GLYPHS = ["　"] + [chr(0xFF10 + i) for i in range(10)] + ["−", "▶", "▼"]

# フォントに無い記号の内蔵グリフ (美咲フォントは JIS X 0208 のため ▶ が無い)
CUSTOM_GLYPHS = {
    "▶": bytes([0x40, 0x60, 0x70, 0x78, 0x70, 0x60, 0x40, 0x00]),
}

# 行頭禁則文字 (行頭に来る場合は直前の 1 文字と一緒に次の行へ送る)
KINSOKU_HEAD = set("、。，．,.！？!?」』）)]】〉》ー〜…・ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ")

# 半角 → 全角 変換表 (8x8 セルに 1 文字ずつ置くため)
_HALF_TO_FULL = {chr(c): chr(c + 0xFEE0) for c in range(0x21, 0x7F)}
_HALF_TO_FULL[" "] = "　"
_HALF_TO_FULL["-"] = "−"


class FontError(Exception):
    pass


@lru_cache(maxsize=4)
def load_font(name: str = "misaki_gothic") -> dict[str, bytes]:
    path = FONT_DIR / f"{name}.bin"
    if not path.exists():
        raise FontError(f"フォント {name} が見つかりません: {path}")
    data = path.read_bytes()
    if data[:4] != b"GBFF":
        raise FontError(f"フォントファイルの形式が不正です: {path}")
    (n,) = struct.unpack_from("<I", data, 4)
    glyphs = {}
    off = 8
    for _ in range(n):
        (cp,) = struct.unpack_from("<I", data, off)
        glyphs[chr(cp)] = data[off + 4: off + 12]
        off += 12
    for ch, bm in CUSTOM_GLYPHS.items():
        glyphs.setdefault(ch, bm)
    return glyphs


def available_fonts() -> list[str]:
    return sorted(p.stem for p in FONT_DIR.glob("*.bin"))


def normalize_char(ch: str) -> str:
    """表示用に文字を正規化 (半角英数→全角, 半角カナ→全角カナ)。"""
    if ch in _HALF_TO_FULL:
        return _HALF_TO_FULL[ch]
    if "｡" <= ch <= "ﾟ":
        return unicodedata.normalize("NFKC", ch)
    return ch


def tokenize(text: str):
    """テキストを (種別, 値) のトークン列にする。

    記法:
      改行        → 改行
      \\p 行頭の「---」 → 改ページ (ボタン待ち)
      {n}         → msg_number() で設定した数値
    """
    i = 0
    text = text.replace("\r\n", "\n")
    lines = text.split("\n")
    for li, line in enumerate(lines):
        if line.strip() == "---":
            yield ("page", None)
            continue
        i = 0
        while i < len(line):
            if line.startswith("{n}", i):
                yield ("number", None)
                i += 3
                continue
            if line.startswith("\\p", i):
                yield ("page", None)
                i += 2
                continue
            # 結合文字 (濁点など) は NFKC で合成
            ch = line[i]
            if i + 1 < len(line) and line[i + 1] in "゙゚ﾞﾟ":
                ch = unicodedata.normalize("NFKC", line[i: i + 2])
                if len(ch) == 1:
                    i += 2
                    yield ("char", normalize_char(ch))
                    continue
                ch = line[i]
            yield ("char", normalize_char(ch))
            i += 1
        if li != len(lines) - 1:
            if not (li + 1 < len(lines) and lines[li + 1].strip() == "---"):
                yield ("newline", None)


def layout(text: str, choice: bool = False):
    """自動折り返し・自動改ページを行い、ページのリスト [[行, ...], ...] を返す。
    各行はトークンのリスト。プレビューとエンコードの両方で使う。"""
    pages = [[[]]]
    if choice:
        # 選択肢: 最後の 2 行の先頭にカーソル用の空白を入れる
        raw_lines = text.replace("\r\n", "\n").split("\n")
        if len(raw_lines) >= 2:
            raw_lines[-1] = "　" + raw_lines[-1]
            raw_lines[-2] = "　" + raw_lines[-2]
        text = "\n".join(raw_lines)

    def width(tok):
        return 5 if tok[0] == "number" else 1

    def newline():
        page = pages[-1]
        if len(page) >= ROWS:
            pages.append([[]])
        else:
            page.append([])

    for tok in tokenize(text):
        if tok[0] == "page":
            pages.append([[]])
            continue
        if tok[0] == "newline":
            newline()
            continue
        line = pages[-1][-1]
        if sum(width(t) for t in line) + width(tok) > COLS:
            carry = []
            if tok[0] == "char" and tok[1] in KINSOKU_HEAD and len(line) > 1 and line[-1][0] == "char" \
                    and line[-1][1] not in KINSOKU_HEAD:
                carry = [line.pop()]   # 追い出し: 直前の文字ごと次の行へ
            newline()
            line = pages[-1][-1]
            line.extend(carry)
            if tok == ("char", "\u3000"):
                continue  # 行頭の空白は捨てる
        line.append(tok)
    while len(pages) > 1 and pages[-1] == [[]]:
        pages.pop()
    return pages


class GlyphTable:
    """プロジェクトで使用するグリフの集合。"""

    def __init__(self, font_name: str = "misaki_gothic"):
        self.font_name = font_name
        self.font = load_font(font_name)
        self.chars: list[str] = []
        self.index: dict[str, int] = {}
        self.missing: set[str] = set()
        for ch in FIXED_GLYPHS:
            self._add(ch)

    def _add(self, ch: str) -> int:
        if ch not in self.index:
            self.index[ch] = len(self.chars)
            self.chars.append(ch)
        return self.index[ch]

    def glyph_id(self, ch: str) -> int:
        if ch not in self.font:
            self.missing.add(ch)
            ch = "？" if "？" in self.font else "　"
        return self._add(ch)

    def bitmap(self, ch: str) -> bytes:
        return self.font.get(ch, bytes(8))

    def encode(self, text: str, choice: bool = False) -> list[int]:
        out: list[int] = []
        pages = layout(text, choice)
        for pi, page in enumerate(pages):
            if pi:
                out.append(TC_PAGE)
            for li, line in enumerate(page):
                if li:
                    out.append(TC_NEWLINE)
                for kind, val in line:
                    if kind == "char":
                        out.append(self.glyph_id(val))
                    elif kind == "number":
                        out.append(TC_NUMBER)
        out.append(TC_END)
        return out

    def font_blob(self) -> bytes:
        return b"".join(self.bitmap(ch) for ch in self.chars)


def render_preview(text: str, font_name: str = "misaki_gothic", choice: bool = False):
    """エディタのプレビュー用: ページごとに 18x3 の文字配置を返す。"""
    font = load_font(font_name)
    result = []
    for page in layout(text, choice):
        rows = []
        for line in page:
            row = []
            for kind, val in line:
                if kind == "number":
                    row.extend("１２３４５")
                else:
                    row.append(val)
            rows.append(row)
        result.append(rows)
    missing = sorted({c for p in result for r in p for c in r if c not in font})
    return result, missing
