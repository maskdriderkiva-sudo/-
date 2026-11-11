"""gbjp (GBDK 用日本語フォントライブラリ) と C プロジェクト / IDE 連携のテスト。"""
import json
import os
import shutil
import subprocess
import sys
import xml.dom.minidom
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gbforge import builder, jpfont  # noqa: E402
from gbforge.ide import generate_ide_files  # noqa: E402
from gbforge.project import Project  # noqa: E402
from gbforge.templates import create_c_project  # noqa: E402


def test_chars_in_source_ignores_comments():
    code = '/* 漢字コメント */\n// 注釈\njp_print(0, 0, "日本語ABC");\nchar c = \'字\';'
    got = jpfont.chars_in_source(code)
    assert {"日", "本", "語", "A", "字"} <= got
    assert "漢" not in got and "注" not in got


def test_generate_scan_and_full(tmp_path):
    r = jpfont.generate(tmp_path, set("こんにちはA1"))
    codes = (tmp_path / "font" / "gbjp_codes.c").read_text(encoding="utf-8")
    assert "0xFF21" in codes          # A → 全角Ａ
    assert "0x3053" in codes          # こ
    assert "#pragma bank 255" in codes and "BANKREF(gbjp_codes)" in codes
    assert r["glyphs"] < 40
    full = jpfont.generate(tmp_path / "full", None)
    assert full["glyphs"] > 6000 and full["chunks"] == (full["glyphs"] + 1023) // 1024
    idx = (tmp_path / "full" / "font" / "gbjp_font_index.c").read_text(encoding="utf-8")
    assert f"case {full['chunks'] - 1}: return BANK(gbjp_glyphs_{full['chunks'] - 1});" in idx
    # コードポイント表は昇順 (二分探索のため)
    vals = [int(v, 16) for v in __import__("re").findall(r"0x([0-9A-F]{4})", (tmp_path / "full" / "font" / "gbjp_codes.c").read_text())]
    assert vals == sorted(vals)


def test_ide_files(tmp_path):
    p = create_c_project(tmp_path / "app", "IDE Test", target="cgb")
    files = generate_ide_files(p)
    vcx = (p.root / "IDE_TEST.vcxproj").read_text(encoding="utf-8-sig")
    xml.dom.minidom.parseString(vcx.encode())
    xml.dom.minidom.parse(str(p.root / "IDE_TEST.vcxproj.filters"))
    assert "NMakeBuildCommandLine" in vcx and "gbjp" in vcx and 'ClCompile Include="src\\main.c"' in vcx
    cfg = json.loads((p.root / ".vscode" / "c_cpp_properties.json").read_text(encoding="utf-8"))
    assert any(i.endswith("lib/gbjp") for i in cfg["configurations"][0]["includePath"])
    tasks = json.loads((p.root / ".vscode" / "tasks.json").read_text(encoding="utf-8"))
    assert tasks["tasks"][0]["group"]["isDefault"] is True
    assert (p.root / "IDE_TEST.sln").exists() and files


GBDK = builder.find_gbdk(None)


@pytest.mark.skipif(not GBDK, reason="GBDK-2020 が必要")
def test_intellisense_config_parses_with_clang(tmp_path):
    """生成したインクルードパス・定義で GBDK のヘッダーが普通の C パーサーで読めること (IntelliSense 相当)。"""
    if not shutil.which("clang"):
        pytest.skip("clang が必要")
    p = create_c_project(tmp_path / "app", "Parse Test")
    cfg = json.loads((p.root / ".vscode" / "c_cpp_properties.json").read_text(encoding="utf-8"))["configurations"][0]
    incs = [i.replace("${workspaceFolder}", str(p.root)) for i in cfg["includePath"]]
    builder.build(p, log=lambda m: None)    # player.h などの生成物を作る
    args = ["clang", "-fsyntax-only", "-std=c11", "-target", "i386-unknown-none", "-ffreestanding", "-Wno-everything"]
    args += ["-I" + i for i in incs] + ["-D" + d for d in cfg["defines"]]
    for f in ("src/main.c", "lib/gbjp/gbjp.c"):
        r = subprocess.run(args + [str(p.root / f)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr[:2000]


@pytest.mark.skipif(not GBDK, reason="GBDK-2020 が必要")
def test_c_project_runs_on_dmg_and_cgb(tmp_path):
    pyboy = pytest.importorskip("pyboy")
    p = create_c_project(tmp_path / "app", "Color Test", target="both")
    res = builder.build(p, log=lambda m: None)
    assert res.ok, "\n".join(res.log[-15:])
    rom = res.rom.read_bytes()
    assert rom[0x143] == 0x80                     # CGB 対応フラグ
    colors = {}
    for cgb in (False, True):
        tmp = tmp_path / f"run{int(cgb)}.gb"
        shutil.copy(res.rom, tmp)
        pb = pyboy.PyBoy(str(tmp), window="null", sound_emulated=False, cgb=cgb)
        for _ in range(90):
            pb.tick()
        m = pb.memory
        title = [m[0x9800 + 32 + x] for x in range(1, 11)]
        assert sum(t >= 129 for t in title) >= 9, title   # タイトル行に文字タイル (空白は 128)
        img = pb.screen.image.convert("RGB")
        colors[cgb] = {img.getpixel((x, y)) for x in range(8, 90) for y in range(8, 16)}
        # A ボタンでメッセージウィンドウ (タイプライター表示)
        pb.button_press("a"); [pb.tick() for _ in range(4)]; pb.button_release("a")
        for _ in range(150):
            pb.tick()
        assert m[0xFF40] & 0x20, "ウィンドウが表示されていない"
        pb.stop(save=False)
    assert all(r == g == b for r, g, b in colors[False])          # DMG は灰色
    assert any(r > g + 40 for r, g, b in colors[True])            # GBC では見出しが赤系パレット
