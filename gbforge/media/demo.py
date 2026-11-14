"""単体 ROM プレビュー: 編集中のシーンだけを再生するデモ ROM を作り、エミュレーターで動かす。"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from .export import c_sources, export
from .studio import Build

MAIN_C = r'''/* GB Media Studio デモ ROM (自動生成) */
#include <gb/gb.h>
#include "gbmedia.h"
#include "gbmedia_data.h"

volatile uint8_t gbm_demo_done;     /* 検証用: 1 周再生し終えた回数 */

void main(void) {
    SHOW_BKG;
    DISPLAY_ON;
    gbmedia_init();
    while (1) {
        gbmedia_load_scene(GBM_DEMO_SCENE);
        gbmedia_play();
        gbm_demo_done++;
    }
}
'''


class DemoError(Exception):
    pass


def build_demo(build: Build, scene_index: int, lcc: Path, work: Path, log=print) -> Path:
    """デモ ROM を作って ROM のパスを返す。コンパイルエラーは DemoError。"""
    work = Path(work)
    if work.exists():
        shutil.rmtree(work)
    gen = work / "gbmedia"
    export(build, gen, "auto")
    (work / "main.c").write_text(MAIN_C, encoding="utf-8")
    cgb = build.proj.get("target") == "cgb"
    objs = []
    srcs = [work / "main.c"] + c_sources(gen)
    for s in srcs:
        o = work / (s.stem + ".o")
        cmd = [str(lcc), "-c", f"-I{gen}", f"-DGBM_DEMO_SCENE={scene_index}", "-o", str(o), str(s)]
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
        if r.returncode != 0:
            raise DemoError(f"{s.name} のコンパイルに失敗しました:\n{(r.stdout + r.stderr)[-1500:]}")
        objs.append(str(o))
    name = re.sub(r"\W", "_", build.proj.get("name", "media"))
    rom = work / f"{name}_demo.{'gbc' if cgb else 'gb'}"
    cmd = [str(lcc), "-autobank", "-Wl-yt0x19", "-Wm-yoA", "-Wl-m", "-Wl-j"] + (["-Wm-yC"] if cgb else []) + \
        ["-o", str(rom)] + objs
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0 or not rom.exists():
        raise DemoError("リンクに失敗しました:\n" + out[-1500:])
    if out:
        for line in out.splitlines()[-8:]:
            log(line)
    log(f"デモ ROM: {rom} ({rom.stat().st_size // 1024} KB)")
    return rom


def symbol_addresses(rom: Path) -> dict[str, int]:
    """リンカーの .noi から変数のアドレス (検証用)。"""
    noi = Path(rom).with_suffix(".noi")
    out = {}
    if noi.exists():
        for line in noi.read_text(errors="replace").splitlines():
            m = re.match(r"DEF\s+(\S+)\s+0x([0-9A-Fa-f]+)", line)
            if m:
                out[m.group(1)] = int(m.group(2), 16)
    return out
