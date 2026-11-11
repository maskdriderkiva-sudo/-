"""既存の C ソース中の配列 (タイル・マップ・パレット) を読み書きする。

    const unsigned char road_tiles[80] = { 0x00, 0x10, ... };
    const palette_color_t road_palettes[8] = { RGB(20, 28, 12), ... };

ソースの他の部分には一切触れず、初期化子 { ... } の中身だけを書き換える。
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

DECL_RE = re.compile(
    r"(?P<prefix>(?:\b(?:static|const|volatile|extern)\s+)*)"
    r"(?P<ctype>unsigned\s+char|signed\s+char|char|unsigned\s+short|short|unsigned\s+int|int|"
    r"u?int8_t|u?int16_t|UINT8|INT8|UINT16|INT16|UBYTE|BYTE|UWORD|WORD|palette_color_t|uint8|uint16)"
    r"\s+(?:const\s+)?(?P<name>[A-Za-z_]\w*)\s*\[(?P<size>[^\]]*)\]\s*(?:\[(?P<size2>[^\]]*)\])?\s*=\s*\{",
    re.M)
COMMENT_RE = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)
SIXTEEN_BIT = ("short", "int", "int16_t", "uint16_t", "INT16", "UINT16", "WORD", "UWORD", "palette_color_t", "uint16")


@dataclass
class CArray:
    file: str            # プロジェクトからの相対パス
    name: str
    ctype: str
    size_text: str
    line: int
    start: int           # '{' の直後
    end: int             # '}' の位置
    values: list[int] | None = None
    raw_items: list[str] = field(default_factory=list)
    kind: str = "data"   # tiles / palette / map / data
    bits: int = 8

    @property
    def count(self) -> int:
        return len(self.raw_items)

    def summary(self) -> dict:
        d = asdict(self)
        d.pop("raw_items")
        d["count"] = self.count
        d["parsed"] = self.values is not None
        if self.values is not None and len(self.values) > 4096 * 4:
            d["values"] = None  # 巨大な配列は個別に取得
        return d


def _blank_comments(text: str) -> str:
    """コメントを同じ長さの空白に置き換える (オフセットを保つ)。"""
    return COMMENT_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def _split_items(body: str) -> list[str]:
    items, depth, cur = [], 0, ""
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            items.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        items.append(cur.strip())
    return items


def rgb5(r: int, g: int, b: int) -> int:
    return (r & 31) | ((g & 31) << 5) | ((b & 31) << 10)


def _eval_item(s: str) -> int | None:
    s = s.strip()
    m = re.fullmatch(r"RGB\s*\(\s*(\w+)\s*,\s*(\w+)\s*,\s*(\w+)\s*\)", s)
    if m:
        vals = [_eval_item(x) for x in m.groups()]
        return None if None in vals else rgb5(*vals)
    m = re.fullmatch(r"RGB8\s*\(\s*(\w+)\s*,\s*(\w+)\s*,\s*(\w+)\s*\)", s)
    if m:
        vals = [_eval_item(x) for x in m.groups()]
        return None if None in vals else rgb5(*(v >> 3 for v in vals))
    m = re.fullmatch(r"RGBHTML\s*\(\s*(0x[0-9A-Fa-f]+)\s*\)", s)
    if m:
        v = int(m.group(1), 16)
        return rgb5((v >> 19) & 31, (v >> 11) & 31, (v >> 3) & 31)
    if re.fullmatch(r"'.'", s):
        return ord(s[1])
    s2 = re.sub(r"[uUlL]+$", "", s)
    try:
        if re.fullmatch(r"-?0[xX][0-9A-Fa-f]+", s2):
            return int(s2, 16)
        if re.fullmatch(r"-?0[bB][01]+", s2):
            return int(s2, 2)
        if re.fullmatch(r"-?\d+", s2):
            return int(s2, 10)
    except ValueError:
        return None
    # 簡単な式 (1+2, 0x10|3 など) は安全な範囲で評価
    if re.fullmatch(r"[0-9xXa-fA-F\s\+\-\*\|&<>\(\)]+", s2):
        try:
            return int(eval(s2, {"__builtins__": {}}, {}))  # noqa: S307 (数字と演算子のみ)
        except Exception:  # noqa: BLE001
            return None
    return None


def classify(arr: CArray) -> str:
    n = arr.name.lower()
    if arr.ctype == "palette_color_t" or any(i.startswith("RGB") for i in arr.raw_items[:4]) or ("pal" in n and arr.bits == 16):
        return "palette"
    if arr.bits == 8 and arr.count and arr.count % 16 == 0 and re.search(r"tile|spr|gfx|font|chr|data|sprite", n):
        return "tiles"
    if arr.bits == 8 and re.search(r"map|stage|level|course|road|table|layout|attr", n):
        return "map"
    if arr.bits == 8 and arr.count >= 32 and arr.count % 16 == 0 and not re.search(r"map|table|lane|pattern|wave|tbl", n):
        return "tiles"
    return "data"


def scan_file(path: Path, rel: str) -> list[CArray]:
    text = path.read_text(encoding="utf-8", errors="replace")
    clean = _blank_comments(text)
    out: list[CArray] = []
    for m in DECL_RE.finditer(clean):
        if "extern" in (m.group("prefix") or ""):
            continue
        start = m.end()
        depth, i = 1, start
        while i < len(clean) and depth:
            if clean[i] == "{":
                depth += 1
            elif clean[i] == "}":
                depth -= 1
            i += 1
        if depth:
            continue
        end = i - 1
        body = clean[start:end].replace("{", " ").replace("}", " ")
        items = _split_items(body)
        vals = [_eval_item(it) for it in items]
        ctype = re.sub(r"\s+", " ", m.group("ctype"))
        arr = CArray(file=rel, name=m.group("name"), ctype=ctype, size_text=m.group("size").strip(),
                     line=clean.count("\n", 0, m.start()) + 1, start=start, end=end,
                     values=None if None in vals else vals, raw_items=items,
                     bits=16 if ctype in SIXTEEN_BIT else 8)
        arr.kind = classify(arr)
        out.append(arr)
    return out


def scan_project(root: Path, files: list[str]) -> list[CArray]:
    out = []
    for rel in files:
        if rel.endswith(".c") or rel.endswith(".h"):
            try:
                out += scan_file(root / rel, rel)
            except OSError:
                pass
    return out


def find(root: Path, rel: str, name: str) -> CArray:
    for a in scan_file(root / rel, rel):
        if a.name == name:
            return a
    raise KeyError(f"{rel} に配列 {name} が見つかりません")


def format_values(values: list[int], arr: CArray, per_line: int | None = None) -> str:
    if arr.kind == "palette" or arr.ctype == "palette_color_t":
        items = [f"RGB({v & 31}, {(v >> 5) & 31}, {(v >> 10) & 31})" for v in values]
        per_line = per_line or 4
    elif arr.bits == 16:
        items = [f"0x{v & 0xFFFF:04X}" for v in values]
        per_line = per_line or 8
    else:
        items = [f"0x{v & 0xFF:02X}" for v in values]
        per_line = per_line or 16
    lines = []
    for i in range(0, len(items), per_line):
        lines.append("    " + ", ".join(items[i:i + per_line]) + ",")
    return "\n" + "\n".join(lines) + "\n"


def write_back(root: Path, rel: str, name: str, values: list[int], per_line: int | None = None) -> CArray:
    """配列の初期化子だけを書き換える。要素数が変わり、サイズが数字で書かれていればそれも更新。"""
    path = root / rel
    arr = find(root, rel, name)
    text = path.read_text(encoding="utf-8", errors="replace")
    new_text = text[:arr.start] + format_values(values, arr, per_line) + text[arr.end:]
    if len(values) != arr.count and re.fullmatch(r"\d+", arr.size_text):
        decl = re.compile(r"(\b" + re.escape(name) + r"\s*\[)\s*" + re.escape(arr.size_text) + r"\s*(\])")
        head = new_text[:arr.start]
        idx = list(decl.finditer(head))
        if idx:
            m = idx[-1]
            head = head[:m.start()] + m.group(1) + str(len(values)) + m.group(2) + head[m.end():]
            new_text = head + new_text[arr.start:]
    path.write_text(new_text, encoding="utf-8")
    return find(root, rel, name)


# ---------------------------------------------------------------------------
# タイル (2bpp) / パレット
# ---------------------------------------------------------------------------
def palette_rgb(values: list[int]) -> list[tuple[int, int, int]]:
    out = []
    for v in values:
        r, g, b = v & 31, (v >> 5) & 31, (v >> 10) & 31
        out.append(((r << 3) | (r >> 2), (g << 3) | (g >> 2), (b << 3) | (b >> 2)))
    return out


DMG_GRAYS = [(224, 248, 208), (136, 192, 112), (52, 104, 86), (8, 24, 32)]
