"""既存の GBDK フォルダの取り込み / C 配列の読み書き / 変換ツール / ステージ / バージョン のテスト。"""
import shutil
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gbforge import adopt, builder, carray, convert, stages, versions  # noqa: E402
from gbforge.project import Project  # noqa: E402

ASSETS_C = """// assets.c (手書き風)
#include <gb/gb.h>
#include <gb/cgb.h>

/* パレット */
const palette_color_t road_palettes[4] = {
    RGB(20, 28, 12), RGB(12, 22, 6), RGB(10, 10, 12), RGB(31, 31, 31),
};

const unsigned char road_tiles[32] = {
    0x20,0x00,0x02,0x00,0x40,0x00,0x04,0x00,0x10,0x00,0x01,0x00,0x80,0x00,0x08,0x00, // 草
    0x00,0xFF,0x00,0xFF,0x00,0xFF,0x00,0xFF,0x00,0xFF,0x00,0xFF,0x00,0xFF,0x00,0xFF,
};

const unsigned char road_map[40] = {
    0,0,1,1,1,1,1,1,0,0,
    0,0,1,1,1,1,1,1,0,0,
    0,0,1,1,1,1,1,1,0,0,
    0,0,1,1,1,1,1,1,0,0,
};
const unsigned char lookup[3] = { 1, 2, 3 };
"""

MAIN_C = """#include <gb/gb.h>
#include <gb/cgb.h>
extern const palette_color_t road_palettes[4];
extern const unsigned char road_tiles[32];
extern const unsigned char road_map[40];
void main(void) {
    set_bkg_palette(0, 1, road_palettes);
    set_bkg_data(0, 2, road_tiles);
    set_bkg_tiles(0, 0, 10, 4, road_map);
    SHOW_BKG; DISPLAY_ON;
    while (1) vsync();
}
"""


@pytest.fixture
def legacy(tmp_path):
    """C:\\gbdk-win64\\gbdk\\Project\\Game_v1.9_HW のような既存フォルダ"""
    gbdk_root = tmp_path / "gbdk"
    lcc = builder.find_gbdk(None)
    if lcc:
        (gbdk_root).mkdir()
        for sub in ("bin", "include", "lib", "libexec"):
            src = lcc.parent.parent / sub
            if src.exists():
                (gbdk_root / sub).symlink_to(src)
    proj = gbdk_root / "Project" / "Game_v1.9_HW"
    proj.mkdir(parents=True)
    (proj / "assets.c").write_text(ASSETS_C, encoding="utf-8")
    (proj / "main.c").write_text(MAIN_C, encoding="utf-8")
    (proj / "assets.h").write_text("#define ROAD_MAP_W 10\n#define ROAD_MAP_H 4\n", encoding="utf-8")
    (proj / "build.bat").write_bytes(b"@echo off\r\nREM \x83e\x83X\x83g\r\n..\\..\\bin\\lcc -Wm-yc -o game.gb main.c assets.c\r\npause\r\n")
    (proj / "build.sh").write_text("#!/bin/sh\nset -e\n../../bin/lcc -Wm-yc -o game.gb main.c assets.c\n", encoding="utf-8")
    im = Image.new("RGBA", (32, 16), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle([2, 1, 13, 14], fill=(230, 40, 40), outline=(20, 20, 20))
    d.rectangle([18, 1, 29, 14], fill=(40, 90, 230), outline=(20, 20, 20))
    im.save(proj / "cars.png")
    return proj


def test_carray_parse_and_write_back(legacy):
    arrs = {a.name: a for a in carray.scan_file(legacy / "assets.c", "assets.c")}
    assert arrs["road_palettes"].kind == "palette" and arrs["road_palettes"].values[3] == 0x7FFF
    assert arrs["road_tiles"].kind == "tiles" and arrs["road_tiles"].count == 32
    assert arrs["road_map"].kind == "map" and arrs["lookup"].kind == "data"
    before = (legacy / "assets.c").read_text(encoding="utf-8")
    vals = arrs["road_map"].values[:]
    vals[0] = 5
    carray.write_back(legacy, "assets.c", "road_map", vals, per_line=10)
    after = (legacy / "assets.c").read_text(encoding="utf-8")
    assert carray.find(legacy, "assets.c", "road_map").values[0] == 5
    # 他の部分 (コメント・他の配列) はそのまま
    assert after.split("const unsigned char road_map")[0] == before.split("const unsigned char road_map")[0]
    assert after.rstrip().endswith("const unsigned char lookup[3] = { 1, 2, 3 };")


def test_tiles_png_roundtrip(legacy):
    a = carray.find(legacy, "assets.c", "road_tiles")
    pal = convert.palette_from_values(carray.find(legacy, "assets.c", "road_palettes").values)
    im = convert.tiles_to_image(bytes(a.values), pal, 16)
    assert convert.image_to_tiles_with_palette(im, pal) == bytes(a.values)


def test_convert_sprite_cgb(legacy):
    r = convert.convert_image(legacy / "cars.png", "cars", "sprite8x16", "cgb")
    assert r.frames == 2 and r.tile_count == 8 and len(r.attrs) == 8
    # 赤・青・枠の 3 色は 1 本のパレットに収まる (透明 + 3 色)
    assert len(r.palettes) == 1 and (231, 41, 41) in r.palettes[0] and (41, 90, 231) in r.palettes[0]
    c, h = convert.to_c(r)
    assert "cars_tiles[128]" in c and "CARS_FRAMES 2" in h and "palette_color_t cars_palettes[4]" in c


def test_convert_map_dedupe(legacy):
    im = Image.new("RGB", (64, 16), (255, 255, 255))
    ImageDraw.Draw(im).rectangle([0, 0, 7, 7], fill=(0, 0, 0))
    r = convert.convert_image(im, "bg", "map", "dmg")
    assert r.tile_count == 2 and r.map_w == 8 and r.map.count(0) == 1


def test_adopt_build_stage_and_versions(legacy):
    info = adopt.inspect(legacy)
    assert info["build_script_windows"] == "build.bat" and info["output"] == "game.gb"
    assert info["target"] == "both"
    p = adopt.adopt(legacy, info)
    assert p.is_flat and p.rom_path.name == "game.gb" and set(p.source_files()) == {"assets.c", "assets.h", "main.c"}

    # 既存のマップ配列をステージとして編集 → 書き戻し + オブジェクトは stage_data.c へ
    p.settings["object_types"] = [{"name": "car", "color": "#f00", "fields": ["speed"]}]
    p.save_settings()
    st = stages.create_from_array(p, "course", "assets.c", "road_map", None,
                                  {"kind": "carray", "file": "assets.c", "symbol": "road_tiles"})
    assert st["width"] == 10 and st["height"] == 4
    st["tiles"][0] = 1
    st["objects"].append({"type": "car", "x": 16, "y": 8, "props": {"speed": 3}})
    stages.save(p, "course", st)
    stages.export(p, lambda m: None, write_arrays=True)
    assert carray.find(legacy, "assets.c", "road_map").values[0] == 1
    sd = (legacy / "stage_data.c").read_text(encoding="utf-8")
    assert "extern const unsigned char road_map[];" in sd and "{ 0, 16, 8, { 3, 0, 0, 0 } }" in sd
    im, n = stages.tileset_image(p, st["tileset"])
    assert n == 2

    # build.bat に追加 (CRLF と Shift_JIS のコメントを壊さない)
    assert not adopt.script_has(p, "stage_data.c")
    adopt.add_to_build_scripts(p, ["stage_data.c"])
    raw = (legacy / "build.bat").read_bytes()
    assert b"assets.c stage_data.c\r\n" in raw and b"\x83e\x83X\x83g" in raw and (legacy / "build.bat.bak").exists()

    # 既存の build.sh でビルド (GBDK があれば)
    if builder.find_gbdk(p) and shutil.which("sh"):
        res = builder.build(p, log=lambda m: None)
        assert res.ok, "\n".join(res.log[-10:])
        assert res.rom.read_bytes()[0x143] == 0x80

    # バージョン
    assert versions.suggest_next(legacy)[:2] == ["Game_v2.0", "Game_v1.10_HW"]
    r = versions.save_new_version(legacy, "Game_v2.0")
    assert (Path(r["path"]) / "assets.c").exists() and Path(r["zip"]).exists()
    names = [v["name"] for v in versions.siblings(legacy)]
    assert "Game_v1.9_HW" in names and "Game_v2.0" in names
