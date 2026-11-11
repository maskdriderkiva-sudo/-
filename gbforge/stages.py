"""ステージ (シーン) エディタのデータとエクスポート。

stages/<名前>.json
{
  "width": 20, "height": 64,                     # タイル数
  "tileset": {"kind": "png", "path": "road.png"}  # または {"kind": "carray", "file": "assets.c", "symbol": "road_tiles",
                                                  #         "palette_file": "assets.c", "palette_symbol": "road_palettes"}
  "tile_origin": 0,                              # マップに書く値 = タイル番号 + tile_origin
  "tiles": [...], "attrs": [...],                # w*h (attrs は CGB の属性: 下位 3bit = パレット)
  "objects": [{"type": "car", "x": 40, "y": 120, "props": {"speed": 2}}],
  "export": {"mode": "generated"}                # stage_data.c/.h に出力
         or {"mode": "array", "file": "assets.c", "map_symbol": "road_map", "attr_symbol": ""}  # 既存の配列へ書き戻す
}
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from PIL import Image

from . import carray, convert
from .project import Project, ProjectError, check_ident

MAX_FIELDS = 4


def stages_dir(p: Project) -> Path:
    return p.root / "stages"


def list_stages(p: Project) -> list[str]:
    d = stages_dir(p)
    return sorted(f.stem for f in d.glob("*.json")) if d.exists() else []


def load(p: Project, name: str) -> dict:
    f = stages_dir(p) / f"{check_ident(name, 'ステージ')}.json"
    if not f.exists():
        raise ProjectError(f"ステージ {name} がありません")
    st = json.loads(f.read_text(encoding="utf-8"))
    n = st["width"] * st["height"]
    st["tiles"] = (st.get("tiles") or [0] * n)[:n] + [0] * max(0, n - len(st.get("tiles") or []))
    if st.get("attrs") is not None:
        st["attrs"] = (st["attrs"][:n] + [0] * max(0, n - len(st["attrs"])))
    st.setdefault("objects", [])
    st.setdefault("tile_origin", 0)
    st.setdefault("export", {"mode": "generated"})
    return st


def save(p: Project, name: str, st: dict):
    check_ident(name, "ステージ")
    stages_dir(p).mkdir(exist_ok=True)
    (stages_dir(p) / f"{name}.json").write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")


def object_types(p: Project) -> list[dict]:
    return p.settings.get("object_types") or []


def guess_width(arr: carray.CArray, root: Path) -> int:
    """配列の幅を推測 (NAME_W / NAME_WIDTH の #define、なければ 20 か 32)。"""
    base = re.sub(r"_?(map|tiles|data)$", "", arr.name, flags=re.I).upper()
    for f in [arr.file] + [arr.file.rsplit(".", 1)[0] + ".h"]:
        path = root / f
        if path.exists():
            txt = path.read_text(encoding="utf-8", errors="replace")
            for pat in (rf"#define\s+{base}_MAP_W(?:IDTH)?\s+(\d+)", rf"#define\s+{base}_W(?:IDTH)?\s+(\d+)",
                        rf"#define\s+{arr.name.upper()}_W(?:IDTH)?\s+(\d+)"):
                m = re.search(pat, txt)
                if m:
                    return int(m.group(1))
    for w in (20, 32, 16, 10):
        if arr.count % w == 0:
            return w
    return arr.count


def create_from_array(p: Project, name: str, file: str, symbol: str, width: int | None, tileset: dict | None) -> dict:
    arr = carray.find(p.root, file, symbol)
    if arr.values is None:
        raise ProjectError(f"{symbol} の中にマクロなど数値以外の要素があり、編集できません")
    w = width or guess_width(arr, p.root)
    h = max(1, len(arr.values) // w)
    st = {"width": w, "height": h, "tileset": tileset or {}, "tile_origin": 0,
          "tiles": [v & 0xFF for v in arr.values[:w * h]], "attrs": None, "objects": [],
          "export": {"mode": "array", "file": file, "map_symbol": symbol, "attr_symbol": ""}}
    save(p, name, st)
    return st


def create_new(p: Project, name: str, width: int, height: int, tileset: dict | None, cgb: bool) -> dict:
    n = width * height
    st = {"width": width, "height": height, "tileset": tileset or {}, "tile_origin": 0,
          "tiles": [0] * n, "attrs": [0] * n if cgb else None, "objects": [], "export": {"mode": "generated"}}
    save(p, name, st)
    return st


# ---------------------------------------------------------------------------
# タイルセットの画像
# ---------------------------------------------------------------------------
def tileset_image(p: Project, ts: dict, palette: int = 0) -> tuple[Image.Image, int]:
    """(タイルを横 16 枚で並べた画像, タイル数)"""
    if not ts or not ts.get("kind"):
        im = Image.new("RGB", (128, 8), (60, 60, 70))
        return im, 0
    if ts["kind"] == "png":
        src = Image.open(p.resolve(ts["path"])).convert("RGB")
        w, h = src.size
        tiles = [(x, y) for y in range(0, h - h % 8, 8) for x in range(0, w - w % 8, 8)]
        out = Image.new("RGB", (128, max(8, ((len(tiles) + 15) // 16) * 8)), (0, 0, 0))
        for i, (x, y) in enumerate(tiles):
            out.paste(src.crop((x, y, x + 8, y + 8)), ((i % 16) * 8, (i // 16) * 8))
        return out, len(tiles)
    arr = carray.find(p.root, ts["file"], ts["symbol"])
    data = bytes(v & 0xFF for v in (arr.values or []))
    pal = None
    if ts.get("palette_symbol"):
        pa = carray.find(p.root, ts.get("palette_file") or ts["file"], ts["palette_symbol"])
        pal = convert.palette_from_values(pa.values, palette)
    return convert.tiles_to_image(data, pal, 16), len(data) // 16


# ---------------------------------------------------------------------------
# エクスポート
# ---------------------------------------------------------------------------
def _c_ident(s: str) -> str:
    return re.sub(r"\W", "_", s)


def export(p: Project, log=print, write_arrays: bool = False, only: str | None = None) -> list[Path]:
    """全ステージを出力: generated → stage_data.c/.h、array → 既存の配列に書き戻し (write_arrays=True の時だけ)。"""
    names = list_stages(p)
    written: list[Path] = []
    gen = []
    for n in names:
        st = load(p, n)
        ex = st.get("export") or {}
        if ex.get("mode") == "array":
            if st.get("objects"):
                gen.append((n, st))        # オブジェクトは stage_data.c に (マップは既存の配列を参照)
            if not write_arrays or (only and n != only):
                continue
            vals = [(t + st.get("tile_origin", 0)) & 0xFF for t in st["tiles"]]
            carray.write_back(p.root, ex["file"], ex["map_symbol"], vals, per_line=min(32, st["width"]))
            if ex.get("attr_symbol") and st.get("attrs") is not None:
                carray.write_back(p.root, ex["file"], ex["attr_symbol"], st["attrs"], per_line=min(32, st["width"]))
            written.append(p.root / ex["file"])
            log(f"ステージ {n} → {ex['file']} の {ex['map_symbol']} に書き戻しました")
        else:
            gen.append((n, st))
    if not gen:
        return written
    out_name = p.settings.get("stages_output") or "stage_data"
    out_dir = p.src_dir if p.settings.get("layout") != "flat" and p.src_dir.exists() else p.root
    types = object_types(p)
    tindex = {t["name"]: i for i, t in enumerate(types)}
    h = ["/* GBForge ステージエディタで自動生成 — 編集しないでください */",
         f"#ifndef {out_name.upper()}_H", f"#define {out_name.upper()}_H", "#include <stdint.h>", ""]
    for i, t in enumerate(types):
        h.append(f"#define OBJ_{_c_ident(t['name']).upper()} {i}")
        for fi, fld in enumerate((t.get("fields") or [])[:MAX_FIELDS]):
            h.append(f"#define OBJ_{_c_ident(t['name']).upper()}_{_c_ident(fld).upper()} {fi}   /* p[{fi}] */")
    h += ["", "typedef struct {", "    uint8_t type;      /* OBJ_xxx */", "    uint16_t x, y;     /* ピクセル座標 */",
          f"    int16_t p[{MAX_FIELDS}];      /* オブジェクトごとの値 (OBJ_型_項目 の番号) */", "} stage_object_t;", "",
          "typedef struct {", "    uint16_t width, height;          /* タイル数 */",
          "    const uint8_t *map;               /* width*height */",
          "    const uint8_t *attr;              /* CGB 属性 (無ければ NULL) */",
          "    const stage_object_t *objects;", "    uint8_t object_count;", "} stage_t;", "",
          f"#define STAGE_COUNT {len(gen)}"]
    c = ["/* GBForge ステージエディタで自動生成 — 編集しないでください */", f'#include "{out_name}.h"', ""]
    for i, (n, st) in enumerate(gen):
        cid = _c_ident(n)
        h.append(f"#define STAGE_{cid.upper()} {i}")
        h.append(f"extern const stage_t stage_{cid};")
        ex = st.get("export") or {}
        attr_ref = "0"
        if ex.get("mode") == "array":
            c.append(f"extern const unsigned char {ex['map_symbol']}[];   /* {ex['file']} の既存の配列 */")
            map_ref = f"(const uint8_t *){ex['map_symbol']}"
            if ex.get("attr_symbol"):
                c.append(f"extern const unsigned char {ex['attr_symbol']}[];")
                attr_ref = f"(const uint8_t *){ex['attr_symbol']}"
        else:
            vals = [(t + st.get("tile_origin", 0)) & 0xFF for t in st["tiles"]]
            c.append(f"static const uint8_t {cid}_map[{len(vals)}] = {{")
            for r in range(st["height"]):
                c.append("    " + ",".join(str(v) for v in vals[r * st["width"]:(r + 1) * st["width"]]) + ",")
            c.append("};")
            map_ref = f"{cid}_map"
        if ex.get("mode") != "array" and st.get("attrs") is not None and any(st["attrs"]):
            c.append(f"static const uint8_t {cid}_attr[{len(st['attrs'])}] = {{")
            for r in range(st["height"]):
                c.append("    " + ",".join(str(v) for v in st["attrs"][r * st["width"]:(r + 1) * st["width"]]) + ",")
            c.append("};")
            attr_ref = f"{cid}_attr"
        objs = sorted(st["objects"], key=lambda o: (o.get("y", 0), o.get("x", 0)))
        obj_ref = "0"
        if objs:
            c.append(f"static const stage_object_t {cid}_objects[{len(objs)}] = {{")
            for o in objs:
                t = next((tt for tt in types if tt["name"] == o.get("type")), None)
                fields = (t.get("fields") if t else []) or []
                pv = [int((o.get("props") or {}).get(f, 0) or 0) for f in fields[:MAX_FIELDS]]
                pv += [0] * (MAX_FIELDS - len(pv))
                c.append(f"    {{ {tindex.get(o.get('type'), 0)}, {int(o.get('x', 0))}, {int(o.get('y', 0))}, "
                         f"{{ {', '.join(str(v) for v in pv)} }} }},  /* {o.get('type')} */")
            c.append("};")
            obj_ref = f"{cid}_objects"
        c.append(f"const stage_t stage_{cid} = {{ {st['width']}, {st['height']}, {map_ref}, {attr_ref}, {obj_ref}, {len(objs)} }};")
        c.append("")
    c.append("const stage_t *const stages[STAGE_COUNT] = { " + ", ".join(f"&stage_{_c_ident(n)}" for n, _ in gen) + " };")
    h += ["extern const stage_t *const stages[STAGE_COUNT];", "", "#endif", ""]
    from .adopt import is_foreign
    for f in (out_dir / f"{out_name}.c", out_dir / f"{out_name}.h"):
        if is_foreign(f):
            raise ProjectError(f"{f.name} は GBForge 以外で作られたファイルなので上書きしません。"
                               "設定の「ステージの出力ファイル名」を別の名前 (例: gbforge_stages) にしてください")
    (out_dir / f"{out_name}.h").write_text("\n".join(h), encoding="utf-8")
    (out_dir / f"{out_name}.c").write_text("\n".join(c) + "\n", encoding="utf-8")
    written += [out_dir / f"{out_name}.c", out_dir / f"{out_name}.h"]
    log(f"ステージ {len(gen)} 個 → {out_name}.c / {out_name}.h")
    if p.settings.get("build", {}).get("mode") == "script":
        from .adopt import script_has
        if not script_has(p, f"{out_name}.c"):
            log(f"注意: {out_name}.c がビルドスクリプトに入っていません。インスペクターの「ビルドスクリプトに追加」で追加できます")
    return written
