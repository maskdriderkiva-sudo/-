"""プロジェクト → ゲームボーイ ROM ビルダー。

1. アセット変換 (背景/スプライト PNG, テキスト → 美咲フォントのグリフ)
2. データを ROM バンク (16KB) に詰める
3. C ソース生成 (build/gen)
4. GBDK-2020 の lcc でコンパイル・リンク (MBC5 + SRAM + バッテリー)
5. ヘッダー (タイトル / チェックサム) を確定
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import assets
from .font import GlyphTable
from .project import BEHAVIOURS, DIRECTIONS, Project, ProjectError, check_ident

ENGINE_DIR = Path(__file__).parent / "engine"
BANK_SIZE = 0x4000
CODE_BANK = 1
FIRST_DATA_BANK = 2
CART_TYPE_MBC5_RAM_BATTERY = 0x1B
GLYPHS_PER_CHUNK = 2048

NO_TEXT = 0xFFFF
NO_SPRITE = 0xFF
NO_SCRIPT = 0xFF

BHV_IDS = {name: i for i, name in enumerate(BEHAVIOURS)}
DIR_IDS = {name: i for i, name in enumerate(DIRECTIONS)}
AF_HIDDEN = 0x02
AF_SOLID = 0x04


class BuildError(Exception):
    pass


@dataclass
class BuildResult:
    ok: bool = False
    rom: Path | None = None
    log: list[str] = field(default_factory=list)
    report: dict = field(default_factory=dict)


@dataclass
class DataItem:
    symbol: str
    data: bytes
    comment: str
    bank: int = 0


def _c_ident(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", name).upper()


def _c_bytes(data: bytes, indent: str = "    ") -> str:
    lines = []
    for i in range(0, len(data), 16):
        lines.append(indent + ",".join(f"0x{b:02X}" for b in data[i:i + 16]) + ",")
    return "\n".join(lines) if lines else indent + "0x00,"


def pack_banks(items: list[DataItem]) -> int:
    """first-fit decreasing でバンクに割り当てる。最後に使ったバンク番号を返す。"""
    free: list[int] = []
    for it in sorted(items, key=lambda i: -len(i.data)):
        if len(it.data) > BANK_SIZE:
            raise BuildError(f"{it.comment} が 16KB を超えています ({len(it.data)} バイト)")
        for bi, room in enumerate(free):
            if room >= len(it.data):
                it.bank = FIRST_DATA_BANK + bi
                free[bi] -= len(it.data)
                break
        else:
            free.append(BANK_SIZE - len(it.data))
            it.bank = FIRST_DATA_BANK + len(free) - 1
    return FIRST_DATA_BANK + len(free) - 1 if free else FIRST_DATA_BANK - 1


SCRIPT_FUNC_RE = r"\bvoid\s+{name}_(start|update|interact)\s*\(\s*(?:struct\s+)?Actor\s*\*"


def detect_script_functions(name: str, code: str) -> dict[str, bool]:
    code_nc = re.sub(r"/\*.*?\*/|//[^\n]*", "", code, flags=re.S)
    found = set(re.findall(SCRIPT_FUNC_RE.format(name=re.escape(name)), code_nc))
    return {k: (k in found) for k in ("start", "update", "interact")}


# ---------------------------------------------------------------------------
# 変換
# ---------------------------------------------------------------------------
class Converter:
    def __init__(self, project: Project, log: Callable[[str], None]):
        self.p = project
        self.log = log
        self.items: list[DataItem] = []
        self.glyphs = GlyphTable(project.settings.get("font", "misaki_gothic"))
        self.warnings: list[str] = []

    def add(self, data: bytes, comment: str) -> DataItem:
        it = DataItem(f"gbf_d{len(self.items)}", data, comment)
        self.items.append(it)
        return it

    def run(self):
        p = self.p
        self.scene_names = p.scene_names()
        if not self.scene_names:
            raise BuildError("シーンが 1 つもありません")
        self.scene_ids = {n: i for i, n in enumerate(self.scene_names)}
        start = p.settings.get("start_scene") or self.scene_names[0]
        if start not in self.scene_ids:
            raise BuildError(f"開始シーン「{start}」がありません")
        self.start_scene = self.scene_ids[start]

        # テキスト
        texts = p.load_texts()
        self.text_names = sorted(texts)
        self.text_ids = {n: i for i, n in enumerate(self.text_names)}
        self.text_items = []
        for n in self.text_names:
            check_ident(n, "テキスト")
            if texts[n]["choice"]:
                from .font import layout
                last = layout(texts[n]["text"], True)[-1]
                if len(last) < 2 or texts[n]["text"].count("\n") < 1:
                    self.warnings.append(f"テキスト {n}: 2択は最後の 2 行が同じページに入るようにしてください")
            words = self.glyphs.encode(texts[n]["text"], texts[n]["choice"])
            blob = b"".join(w.to_bytes(2, "little") for w in words)
            self.text_items.append(self.add(blob, f"テキスト {n}"))
        if self.glyphs.missing:
            self.warnings.append("フォントに無い文字を「？」に置き換えました: " + "".join(sorted(self.glyphs.missing)))

        # スプライト
        self.sprite_names = p.sprite_names()
        self.sprite_ids = {n: i for i, n in enumerate(self.sprite_names)}
        if len(self.sprite_names) > 254:
            raise BuildError("スプライトは 254 個までです")
        self.sprite_defs = []
        for n in self.sprite_names:
            check_ident(n, "スプライト")
            data, frames = assets.sprite_frames(p.sprites_dir / f"{n}.png")
            self.sprite_defs.append((frames, self.add(data, f"スプライト {n}")))

        # スクリプト
        self.script_names = p.script_names()
        self.script_ids = {n: i for i, n in enumerate(self.script_names)}
        self.script_funcs = {}
        for n in self.script_names:
            check_ident(n, "スクリプト")
            self.script_funcs[n] = detect_script_functions(n, p.read_script(n))

        # シーン
        self.scene_defs = []
        bg_cache: dict[str, tuple] = {}
        for n in self.scene_names:
            check_ident(n, "シーン")
            self.scene_defs.append(self.convert_scene(n, p.load_scene(n), bg_cache))

        # フォント (使用グリフのみ)
        blob = self.glyphs.font_blob()
        self.font_chunks = []
        for i in range(0, len(blob), GLYPHS_PER_CHUNK * 8):
            self.font_chunks.append(self.add(blob[i:i + GLYPHS_PER_CHUNK * 8], f"フォント {i // (GLYPHS_PER_CHUNK * 8)}"))

    def convert_scene(self, name: str, sc: dict, bg_cache: dict) -> dict:
        bg = sc.get("background")
        if not bg:
            raise BuildError(f"シーン {name}: 背景画像が設定されていません")
        if bg not in bg_cache:
            tiles, tmap, w, h = assets.background_to_tiles(self.p.backgrounds_dir / bg)
            bg_cache[bg] = (self.add(tiles, f"背景タイル {bg}"), self.add(tmap, f"背景マップ {bg}"), w, h, len(tiles) // 16)
        tiles_it, map_it, w, h, ntiles = bg_cache[bg]

        coll = bytearray(w * h)
        for y, row in enumerate(sc.get("collision", [])[:h]):
            for x, c in enumerate(str(row)[:w]):
                coll[y * w + x] = 1 if c not in "0 ." else 0
        coll_it = self.add(bytes(coll), f"当たり判定 {name}")

        actors = sc.get("actors", [])
        if len(actors) > 10:
            raise BuildError(f"シーン {name}: アクターは 10 体までです ({len(actors)} 体)")
        adefs = []
        player = 0xFF
        for i, a in enumerate(actors):
            label = f"シーン {name} のアクター {a.get('name')}"
            bhv = a.get("behaviour", "none")
            if bhv not in BHV_IDS:
                raise BuildError(f"{label}: 不明なビヘイビア {bhv}")
            if bhv == "player" and player == 0xFF:
                player = i
            spr = a.get("sprite")
            if spr and spr not in self.sprite_ids:
                raise BuildError(f"{label}: スプライト {spr} がありません")
            scr = a.get("script")
            if scr and scr not in self.script_ids:
                raise BuildError(f"{label}: スクリプト {scr} がありません")
            txt = a.get("text")
            if txt and txt not in self.text_ids:
                raise BuildError(f"{label}: テキスト {txt} がありません")
            param = [0, 0, 0, 0]
            if bhv == "warp":
                wp = a.get("warp") or {}
                if wp.get("scene") not in self.scene_ids:
                    raise BuildError(f"{label}: ワープ先シーン「{wp.get('scene')}」がありません")
                param = [self.scene_ids[wp["scene"]], int(wp.get("x", 0)) & 0xFF, int(wp.get("y", 0)) & 0xFF, 0]
            flags = (AF_SOLID if a.get("solid", True) and bhv != "warp" else 0) | (AF_HIDDEN if a.get("hidden") else 0)
            adefs.append({
                "name": a.get("name") or f"actor{i}",
                "x": int(a.get("x", 0)), "y": int(a.get("y", 0)),
                "sprite": self.sprite_ids[spr] if spr else NO_SPRITE,
                "behaviour": BHV_IDS[bhv],
                "script": self.script_ids[scr] if scr else NO_SCRIPT,
                "flags": flags,
                "dir": DIR_IDS.get(a.get("dir", "down"), 0),
                "speed": max(1, min(4, int(a.get("speed", 1) or 1))),
                "text": self.text_ids[txt] if txt else NO_TEXT,
                "param": param,
            })
        sscr = sc.get("script")
        if sscr and sscr not in self.script_ids:
            raise BuildError(f"シーン {name}: スクリプト {sscr} がありません")
        try:
            bgp = int(str(sc.get("palette", "E4")), 16) & 0xFF
        except ValueError:
            bgp = 0xE4
        return {
            "name": name, "w": w, "h": h, "ntiles": ntiles,
            "tiles": tiles_it, "map": map_it, "coll": coll_it,
            "actors": adefs, "script": self.script_ids[sscr] if sscr else NO_SCRIPT,
            "player": player, "bgp": bgp,
        }


# ---------------------------------------------------------------------------
# C コード生成
# ---------------------------------------------------------------------------
def generate(conv: Converter, gen: Path) -> list[Path]:
    gen.mkdir(parents=True, exist_ok=True)
    for old in gen.glob("*.c"):
        old.unlink()
    last_bank = pack_banks(conv.items)
    files: list[Path] = []

    # gbf_ids.h
    ids = ["/* 自動生成: 編集しないでください (GBForge) */", "#ifndef GBF_IDS_H", "#define GBF_IDS_H", "",
           f"#define CODE_BANK {CODE_BANK}", f"#define START_SCENE {conv.start_scene}",
           f"#define NUM_SCENES {len(conv.scene_names)}", ""]
    for n, i in conv.scene_ids.items():
        ids.append(f"#define SCENE_{_c_ident(n)} {i}")
    ids.append("")
    for n, i in conv.text_ids.items():
        ids.append(f"#define TEXT_{_c_ident(n)} {i}")
    ids.append("")
    for n, i in conv.sprite_ids.items():
        ids.append(f"#define SPRITE_{_c_ident(n)} {i}")
    ids.append("")
    for n, i in conv.script_ids.items():
        ids.append(f"#define SCRIPT_{_c_ident(n)} {i}")
    ids.append("")
    for i, v in enumerate(conv.p.settings.get("variables", [])):
        check_ident(v, "変数")
        ids.append(f"#define VAR_{_c_ident(v)} {i}")
    ids.append("")
    for sd in conv.scene_defs:
        for i, a in enumerate(sd["actors"]):
            if re.match(r"^[A-Za-z0-9_]+$", a["name"]):
                ids.append(f"#define ACTOR_{_c_ident(sd['name'])}_{_c_ident(a['name'])} {i}")
    ids += ["", "#endif", ""]
    (gen / "gbf_ids.h").write_text("\n".join(ids), encoding="utf-8")

    # バンクごとのデータファイル
    by_bank: dict[int, list[DataItem]] = {}
    for it in conv.items:
        by_bank.setdefault(it.bank, []).append(it)
    for bank, its in sorted(by_bank.items()):
        src = [f"#pragma bank {bank}", "/* 自動生成: 編集しないでください (GBForge) */", "#include <stdint.h>", ""]
        for it in its:
            src.append(f"/* {it.comment} ({len(it.data)} bytes) */")
            src.append(f"const uint8_t {it.symbol}[{max(1, len(it.data))}] = {{")
            src.append(_c_bytes(it.data))
            src.append("};")
            src.append("")
        f = gen / f"gbf_bank{bank}.c"
        f.write_text("\n".join(src), encoding="utf-8")
        files.append(f)

    # バンク0 テーブル
    d = ["/* 自動生成: 編集しないでください (GBForge) */", '#include "gbforge.h"', '#include "gbf_types.h"', ""]
    for it in conv.items:
        d.append(f"extern const uint8_t {it.symbol}[];")
    d.append("")

    def bp(it: DataItem) -> str:
        return f"{{ {it.bank}, {it.symbol} }}"

    if conv.sprite_defs:
        d.append("const SpriteDef gbf_sprites[] = {")
        for (frames, it), n in zip(conv.sprite_defs, conv.sprite_names):
            d.append(f"    {{ {frames}, {bp(it)} }}, /* {n} */")
        d.append("};")
    else:
        d.append("const SpriteDef gbf_sprites[1] = { { 0, { 0, 0 } } };")
    d.append("")
    for si, sd in enumerate(conv.scene_defs):
        if not sd["actors"]:
            continue
        d.append(f"static const ActorDef actors_{si}[] = {{")
        for a in sd["actors"]:
            p = ", ".join(str(x) for x in a["param"])
            d.append(f"    {{ {a['x']}, {a['y']}, {a['sprite']}, {a['behaviour']}, {a['script']}, {a['flags']}, "
                     f"{a['dir']}, {a['speed']}, {a['text']}U, {{ {p} }} }}, /* {a['name']} */")
        d.append("};")
    d.append("")
    d.append("const SceneDef gbf_scenes[] = {")
    for si, sd in enumerate(conv.scene_defs):
        acts = f"actors_{si}" if sd["actors"] else "0"
        d.append(f"    {{ {sd['w']}, {sd['h']}, {sd['ntiles']}, {bp(sd['tiles'])}, {bp(sd['map'])}, {bp(sd['coll'])}, "
                 f"{len(sd['actors'])}, {acts}, {sd['script']}, {sd['player']}, 0x{sd['bgp']:02X} }}, /* {sd['name']} */")
    d.append("};")
    d.append("")
    if conv.text_items:
        d.append("const BankPtr gbf_texts[] = {")
        for it, n in zip(conv.text_items, conv.text_names):
            d.append(f"    {bp(it)}, /* {n} */")
        d.append("};")
    else:
        d.append("const BankPtr gbf_texts[1] = { { 0, 0 } };")
    d.append("")
    d.append("const BankPtr gbf_font_chunks[] = {")
    for it in conv.font_chunks:
        d.append(f"    {bp(it)},")
    d.append("};")
    d.append("")
    f = gen / "gbf_data.c"
    f.write_text("\n".join(d), encoding="utf-8")
    files.append(f)

    # スクリプトテーブル
    s = ["/* 自動生成: 編集しないでください (GBForge) */", '#include "gbforge.h"', '#include "gbf_types.h"', ""]
    rows = []
    for n in conv.script_names:
        fn = conv.script_funcs[n]
        cols = []
        for k in ("start", "update", "interact"):
            if fn[k]:
                s.append(f"void {n}_{k}(Actor *self);")
                cols.append(f"{n}_{k}")
            else:
                cols.append("0")
        rows.append(f"    {{ {', '.join(cols)} }}, /* {n} */")
    s.append("")
    if rows:
        s.append("const ScriptDef gbf_scripts[] = {")
        s += rows
        s.append("};")
    else:
        s.append("const ScriptDef gbf_scripts[1] = { { 0, 0, 0 } };")
    s.append("")
    f = gen / "gbf_scripts.c"
    f.write_text("\n".join(s), encoding="utf-8")
    files.append(f)

    # ユーザースクリプトは CODE_BANK に配置するラッパー経由でコンパイル
    for n in conv.script_names:
        f = gen / f"script_{n}.c"
        f.write_text(f"#pragma bank {CODE_BANK}\n/* 自動生成ラッパー */\n#include \"{n}.c\"\n", encoding="utf-8")
        files.append(f)

    conv.last_bank = last_bank
    return files


def rom_bank_count(last_bank: int) -> int:
    n = 4
    while n < last_bank + 1:
        n *= 2
    if n > 512:
        raise BuildError(f"ROM が大きすぎます ({last_bank + 1} バンク, MBC5 の上限は 512)")
    return n


def finalize_header(rom: bytearray, title: str, banks: int):
    """タイトル・カートリッジ種別・サイズ・チェックサムを確定。"""
    if len(rom) < banks * BANK_SIZE:
        rom.extend(b"\xFF" * (banks * BANK_SIZE - len(rom)))
    t = re.sub(r"[^A-Z0-9 _-]", "", (title or "GAME").upper())[:11].encode("ascii")
    rom[0x134:0x13F] = t.ljust(11, b"\x00")
    rom[0x147] = CART_TYPE_MBC5_RAM_BATTERY
    rom[0x148] = (banks // 2).bit_length() - 1
    rom[0x149] = 0x02  # 8KB SRAM
    x = 0
    for b in rom[0x134:0x14D]:
        x = (x - b - 1) & 0xFF
    rom[0x14D] = x
    s = (sum(rom) - rom[0x14E] - rom[0x14F]) & 0xFFFF
    rom[0x14E] = s >> 8
    rom[0x14F] = s & 0xFF


# ---------------------------------------------------------------------------
# ツールチェーン
# ---------------------------------------------------------------------------
def find_gbdk(project: Project | None) -> Path | None:
    from .toolchain import find
    home = project.settings["build"].get("gbdk_home") if project else None
    return find(home)[0]


def _run(cmd: list[str], log, cwd: Path | None = None):
    log("$ " + " ".join(str(c) for c in cmd))
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, cwd=cwd,
                       encoding="utf-8", errors="replace")
    out = (r.stdout + r.stderr).strip()
    if out:
        for line in out.splitlines():
            log(line)
    if r.returncode != 0:
        raise BuildError(f"コマンドが失敗しました (終了コード {r.returncode}): {Path(str(cmd[0])).name}")
    return out


def compile_gbdk(lcc: Path, sources: list[Path], incs: list[Path], out_rom: Path, banks: int, obj_dir: Path, log):
    objs = []
    inc_flags = [f"-I{i}" for i in incs]
    for src in sources:
        obj = obj_dir / (src.stem + ".o")
        _run([lcc, "-c", *inc_flags, "-o", obj, src], log)
        objs.append(obj)
    _run([lcc, f"-Wl-yt0x{CART_TYPE_MBC5_RAM_BATTERY:02X}", f"-Wl-yo{banks}", "-Wl-ya1",
          "-Wl-m", "-o", out_rom, *objs], log)


def compile_sdcc_shim(shim_dir: Path, sources: list[Path], incs: list[Path], out_rom: Path, banks: int, obj_dir: Path, log):
    """テスト用: GBDK の代わりに素の SDCC + 最小シムでビルドする (tests/shim)。"""
    rels = []
    inc_flags = [f"-I{shim_dir / 'include'}"] + [f"-I{i}" for i in incs]
    _run(["sdasgb", "-plosgff", "-o", obj_dir / "crt0.rel", shim_dir / "crt0.s"], log)
    for src in [shim_dir / "gbshim.c", *sources]:
        rel = obj_dir / (src.stem + ".rel")
        _run(["sdcc", "-msm83", "-c", *inc_flags, "-o", rel, src], log)
        rels.append(rel)
    ihx = obj_dir / "rom.ihx"
    lib = Path(shutil.which("sdcc")).resolve().parent.parent / "share" / "sdcc" / "lib" / "sm83"
    area = ["-b", "_CODE=0x0200", "-b", "_DATA=0xC0A0"]
    present = set()
    for rel in rels:
        present |= {int(m) for m in re.findall(r"^A _CODE_(\d+) ", rel.read_text(errors="replace"), re.M)}
    for b in sorted(present):
        area += ["-b", f"_CODE_{b}=0x{b:X}4000"]
    _run(["sdldgb", "-n", "-m", "-i", *area, "-k", lib, "-l", "sm83", ihx, obj_dir / "crt0.rel", *rels], log)
    _run(["makebin", "-Z", "-yt", f"0x{CART_TYPE_MBC5_RAM_BATTERY:02X}", "-yo", str(banks), "-ya", "1", ihx, out_rom], log)


def build(project: Project, log: Callable[[str], None] = print, toolchain: str | None = None,
          shim_dir: Path | None = None) -> BuildResult:
    res = BuildResult()

    def _log(msg):
        res.log.append(msg)
        log(msg)

    if project.is_c:
        try:
            from .stages import export as export_stages
            export_stages(project, _log)
        except Exception as e:  # noqa: BLE001
            _log(f"エラー: ステージの出力に失敗しました: {e}")
            return res
        if project.settings.get("build", {}).get("mode") == "script":
            return build_script(project, _log, res)
        return build_c(project, _log, res)

    try:
        _log(f"=== ビルド開始: {project.settings.get('name')} ===")
        conv = Converter(project, _log)
        conv.run()
        gen = project.build_dir / "gen"
        obj = project.build_dir / "obj"
        obj.mkdir(parents=True, exist_ok=True)
        files = generate(conv, gen)
        banks = rom_bank_count(conv.last_bank)
        for w in conv.warnings:
            _log("警告: " + w)
        _log(f"シーン {len(conv.scene_names)} / スプライト {len(conv.sprite_names)} / テキスト {len(conv.text_names)} / "
             f"スクリプト {len(conv.script_names)} / 使用文字 {len(conv.glyphs.chars)} / ROM {banks * 16}KB ({banks} バンク)")

        sources = [ENGINE_DIR / "engine.c", ENGINE_DIR / "text.c"] + files
        incs = [gen, ENGINE_DIR, project.scripts_dir]
        out = project.rom_path
        tc = toolchain or project.settings["build"].get("toolchain", "gbdk")
        if tc == "gbdk":
            lcc = find_gbdk(project)
            if not lcc:
                raise BuildError("GBDK-2020 が見つかりません。`gbforge setup` (エディタでは「GBDK をセットアップ」) で自動インストールできます")
            compile_gbdk(lcc, sources, incs, out, banks, obj, _log)
        elif tc == "sdcc-shim":
            shim = shim_dir or Path(__file__).resolve().parent.parent / "tests" / "shim"
            compile_sdcc_shim(shim, sources, incs, out, banks, obj, _log)
        else:
            raise BuildError(f"不明なツールチェーン: {tc}")

        rom = bytearray(out.read_bytes())
        finalize_header(rom, project.settings.get("rom_title", "GAME"), banks)
        out.write_bytes(rom)
        res.ok = True
        res.rom = out
        res.report = {
            "banks": banks, "rom_size": len(rom), "glyphs": len(conv.glyphs.chars),
            "data_bytes": sum(len(i.data) for i in conv.items), "warnings": conv.warnings,
        }
        _log(f"=== 成功: {out} ({len(rom) // 1024}KB) ===")
    except (BuildError, ProjectError, assets.AssetError) as e:
        _log(f"エラー: {e}")
    except FileNotFoundError as e:
        _log(f"エラー: 実行ファイルが見つかりません: {e.filename}")
    return res


# ---------------------------------------------------------------------------
# C プロジェクト (GBDK でそのままプログラムを書く) のビルド
# ---------------------------------------------------------------------------
TARGET_FLAGS = {"dmg": [], "both": ["-Wm-yc"], "cgb": ["-Wm-yC"]}


def import_assets(project: Project, gen: Path, png2asset: Path, log) -> list[Path]:
    """res/ の PNG を GBDK の png2asset で C に変換 (Unity のアセットインポートのように自動)。

    res/backgrounds/*.png → 背景マップ (name_tiles, name_map, ...)
    res/sprites/*.png     → 16x16 メタスプライト (8x16 モード)
    res/tiles/*.png       → タイルデータのみ
    """
    out: list[Path] = []
    cgb = project.settings.get("target", "dmg") != "dmg"
    rules = {
        "backgrounds": ["-map", "-noflip"] + (["-use_map_attributes"] if cgb else []),
        "sprites": ["-spr8x16", "-sw", "16", "-sh", "16"],
        "tiles": ["-map", "-tiles_only", "-keep_duplicate_tiles", "-noflip"],
    }
    for kind, flags in rules.items():
        for png in sorted((project.res_dir / kind).glob("*.png")):
            check_ident(png.stem, "アセット")
            dst = gen / f"{png.stem}.c"
            if dst.exists() and dst.stat().st_mtime >= png.stat().st_mtime:
                out.append(dst)
                continue
            msg = _run([png2asset, png, "-o", dst, *flags], log)
            if re.search(r"^Error", msg, re.M):
                dst.unlink(missing_ok=True)
                raise BuildError(f"{png.relative_to(project.root)} を変換できません。"
                                 "スプライトは 8x16 のタイルごとに 3 色 + 透明 (背景は 4 色) までにしてください")
            out.append(dst)
    return out


def build_script(project: Project, log, res: BuildResult) -> BuildResult:
    """既存の build.bat / build.sh / make をそのまま実行する (取り込んだプロジェクト用)。"""
    import os
    import time
    b = project.settings["build"]
    win = os.name == "nt"
    script = (b.get("script_windows") if win else b.get("script_unix")) or ""
    if not script:
        log("エラー: この OS 用のビルドスクリプトがありません。設定で build.bat / build.sh を指定するか、"
            "ビルド方式を「GBForge でコンパイル」にしてください")
        return res
    if script == "make":
        cmd = ["make"]
    elif script.lower().endswith((".bat", ".cmd")):
        cmd = ["cmd", "/c", script]
    else:
        cmd = ["sh", script]
    env = dict(os.environ)
    lcc = find_gbdk(project)
    if lcc:
        env["GBDK_HOME"] = str(lcc.parent.parent)
        env["PATH"] = str(lcc.parent) + os.pathsep + env.get("PATH", "")
    out = project.rom_path
    before = out.stat().st_mtime if out.exists() else 0
    log(f"=== ビルド開始: {project.settings.get('name')} ({script}) ===")
    log("$ " + " ".join(cmd))
    t0 = time.time()
    try:
        p = subprocess.Popen(cmd, cwd=project.root, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT)
    except FileNotFoundError as e:
        log(f"エラー: 実行できません: {e.filename}")
        return res
    enc = "oem" if win else "utf-8"
    for raw in p.stdout:
        line = raw.decode(enc, errors="replace").rstrip()
        if line and not line.startswith("続行するには何かキーを押してください") and "Press any key" not in line:
            log(line)
    p.wait(timeout=900)
    built = out.exists() and out.stat().st_mtime > before and out.stat().st_mtime >= t0 - 2
    if p.returncode != 0 or not built:
        log(f"エラー: ビルドに失敗しました (終了コード {p.returncode}{'、ROM が更新されていません' if not built else ''})")
        return res
    res.ok, res.rom = True, out
    rom = out.read_bytes()
    res.report = {"banks": max(1, len(rom) // BANK_SIZE), "rom_size": len(rom), "glyphs": 0, "data_bytes": 0, "warnings": []}
    log(f"=== 成功: {out} ({len(rom) // 1024}KB) ===")
    return res


def build_c(project: Project, log, res: BuildResult) -> BuildResult:
    from . import jpfont
    try:
        s = project.settings
        log(f"=== ビルド開始: {s.get('name')} (C プロジェクト / {s.get('target', 'dmg').upper()}) ===")
        lcc = find_gbdk(project)
        if not lcc:
            raise BuildError("GBDK-2020 が見つかりません。`gbforge setup` (エディタでは「GBDK をセットアップ」) で自動インストールできます")
        gen = project.build_dir / "gen"
        obj = project.build_dir / "obj"
        gen.mkdir(parents=True, exist_ok=True)
        obj.mkdir(parents=True, exist_ok=True)

        # 日本語フォント (gbjp)
        jp = project.lib_dir / "gbjp"
        if (jp / "gbjp.h").exists():
            jf = s.get("jpfont", {})
            if jf.get("mode") == "full":
                chars = None
            else:
                chars = jpfont.scan_dirs([project.src_dir]) | set(jf.get("extra_chars", ""))
            r = jpfont.generate(jp, chars, s.get("font", "misaki_gothic"))
            log(f"日本語フォント: {r['glyphs']} 文字 ({'全文字' if chars is None else 'ソースから抽出'}, 約 {r['bytes'] / 1024:.1f}KB)")
            if r["missing"]:
                log("警告: フォントに無い文字 (？で表示): " + "".join(r["missing"]))

        # アセット
        png2asset = lcc.parent / ("png2asset.exe" if lcc.suffix == ".exe" else "png2asset")
        assets_c = import_assets(project, gen, png2asset, log) if project.res_dir.exists() else []

        sources = [project.root / f for f in project.source_files() if f.endswith(".c")]
        sources += sorted((jp / "font").glob("gbjp_*.c")) if jp.exists() else []
        sources += assets_c
        if not sources:
            raise BuildError("src/ に .c ファイルがありません")
        incs = [project.code_root, gen] + sorted({p.parent for p in project.lib_dir.rglob("*.h")})
        inc_flags = [f"-I{i}" for i in incs]
        for old in obj.glob("*.o"):
            old.unlink()
        objs = []
        for src in sources:
            rel = src.relative_to(project.root) if project.root in src.parents else Path(src.name)
            o = obj / (re.sub(r"[^A-Za-z0-9_]", "_", rel.with_suffix("").as_posix()) + ".o")
            _run([lcc, "-c", *inc_flags, "-o", o, src], log)
            objs.append(o)
        out = project.rom_path
        _run([lcc, "-autobank", f"-Wl-yt0x{CART_TYPE_MBC5_RAM_BATTERY:02X}", "-Wm-yoA", "-Wm-ya1",
              *TARGET_FLAGS.get(s.get("target", "dmg"), []), "-Wl-m", "-o", out, *objs], log)
        rom = bytearray(out.read_bytes())
        banks = rom_bank_count(max(1, (len(rom) + BANK_SIZE - 1) // BANK_SIZE) - 1)
        finalize_header(rom, s.get("rom_title", "GAME"), banks)
        out.write_bytes(rom)
        res.ok, res.rom = True, out
        res.report = {"banks": banks, "rom_size": len(rom), "glyphs": 0, "data_bytes": 0, "warnings": []}
        log(f"=== 成功: {out} ({len(rom) // 1024}KB) ===")
    except (BuildError, ProjectError) as e:
        log(f"エラー: {e}")
    except FileNotFoundError as e:
        log(f"エラー: 実行ファイルが見つかりません: {e.filename}")
    return res


if __name__ == "__main__":
    r = build(Project(Path(sys.argv[1])), toolchain=sys.argv[2] if len(sys.argv) > 2 else None)
    sys.exit(0 if r.ok else 1)
