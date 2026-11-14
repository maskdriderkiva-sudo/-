"""GBDK-2020 用 C ソースの書き出し。

出力 (output_dir):
  gbmedia.c / gbmedia.h       再生ライブラリ (runtime/ からコピー)
  gbmedia_config.h            設定 (1 フレームの転送量・CGB 倍速など)
  gbmedia_data.c / .h         画像・動画・シーン (イベント列) の表 (バンク 0)
  gbmedia_chunk<N>.c          更新レコードの本体 (ROM バンクに配置)

バンクの置き方
  auto  : #pragma bank 255 (lcc -autobank が空いているバンクに配置。GBForge の C プロジェクトはこれ)
  fixed : first_bank から 1 バンクずつ #pragma bank N (-autobank を使わない既存の build.bat 用)
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from . import LY_STOP
from .studio import Build, bytes_per_update, mode_key, run_cost
from .sim import cgb_level, pal5_to_words

RUNTIME = Path(__file__).parent / "runtime"
CHUNK_LIMIT = 16000
HEAD = "/* GB Media Studio で自動生成 — 編集しないでください */\n"


def _c_ident(s: str) -> str:
    s = re.sub(r"\W", "_", s).upper()
    return s if not s[:1].isdigit() else "_" + s


def _bytes_c(data: bytes, per: int = 24) -> str:
    return "\n".join("    " + ",".join(str(b) for b in data[i:i + per]) + "," for i in range(0, len(data), per)) or "    0,"


def pack_chunks(blobs: list[bytes]) -> tuple[list[bytearray], list[tuple[int, int]]]:
    chunks: list[bytearray] = []
    refs = []
    for b in blobs:
        if len(b) > CHUNK_LIMIT:
            raise ValueError(f"1 つのレコードが {len(b)} バイトあり、1 バンクに入りません")
        if not chunks or len(chunks[-1]) + len(b) > CHUNK_LIMIT:
            chunks.append(bytearray())
        refs.append((len(chunks) - 1, len(chunks[-1])))
        chunks[-1] += b
    return chunks, refs


def fade_table(words: list[int]) -> bytes:
    """明るさ -31〜+31 の 63 行 x 64 バイト (RGB555 リトルエンディアン)。プレビューと同じ cgb_level() で計算。"""
    out = bytearray()
    for lv in range(-31, 32):
        for w in words:
            v = cgb_level(w, lv)
            out += bytes((v & 0xFF, v >> 8))
    return bytes(out)


def export(build: Build, out_dir: Path, bank_mode: str = "auto", first_bank: int = 2) -> list[Path]:
    proj = build.proj
    cgb = proj.get("target") == "cgb"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("gbmedia_chunk*.c"):
        old.unlink()
    blobs = [st.record for _, st in build.images]
    for _, vd in build.videos:
        blobs += vd.records
    n_rec = len(blobs)
    if cgb:   # 63 段階のパレット表 (フェードを表の 1 行を書くだけにする)
        blobs += [fade_table(pal5_to_words(st.pals5)) for _, st in build.images]
        blobs += [fade_table(pal5_to_words(vd.pals5)) for _, vd in build.videos]
    chunks, refs = pack_chunks(blobs)
    fade_refs = refs[n_rec:]
    files: list[Path] = []
    for i, ch in enumerate(chunks):
        bank = "255" if bank_mode == "auto" else str(first_bank + i)
        src = HEAD + f"#pragma bank {bank}\n#include <gbdk/platform.h>\n#include <stdint.h>\n\n"
        if bank_mode == "auto":
            src += f"BANKREF(gbm_chunk{i})\n"
        src += f"const uint8_t gbm_chunk{i}[{max(1, len(ch))}] = {{\n{_bytes_c(bytes(ch))}\n}};\n"
        f = out_dir / f"gbmedia_chunk{i}.c"
        f.write_text(src, encoding="utf-8")
        files.append(f)

    d = [HEAD, "#include <gbdk/platform.h>", "#include <stdint.h>", '#include "gbmedia.h"', '#include "gbmedia_data.h"', ""]
    for i in range(len(chunks)):
        if bank_mode == "auto":
            d.append(f"BANKREF_EXTERN(gbm_chunk{i})")
        d.append(f"extern const uint8_t gbm_chunk{i}[];")
    d.append("")
    d.append("const uint8_t *const gbm_chunk_ptr[] = {" + ", ".join(f"gbm_chunk{i}" for i in range(len(chunks))) + ("" if chunks else "0") + "};")
    d.append("uint8_t gbm_chunk_bank(uint8_t chunk) {\n    switch (chunk) {")
    for i in range(len(chunks)):
        d.append(f"        case {i}: return " + (f"BANK(gbm_chunk{i});" if bank_mode == "auto" else f"{first_bank + i};"))
    d.append("    }\n    return 1;\n}\n")

    ri = 0
    img_rows = []
    for k, (aid, st) in enumerate(build.images):
        c, o = refs[ri]
        ri += 1
        pal = "0"
        if cgb:
            d.append(f"static const uint16_t gbm_img_pal{k}[32] = {{ {', '.join(str(w) for w in pal5_to_words(st.pals5))} }};")
            pal = f"gbm_img_pal{k}"
        fc, fo = fade_refs[k] if cgb else (255, 0)
        img_rows.append(f"    {{ {{ {c}, {o} }}, 0x{st.bgp:02X}, {pal}, {{ {fc}, {fo} }} }},   /* {aid} */")
    d.append("const gbm_image_t gbm_images[] = {")
    d += img_rows or ["    { { 0, 0 }, 0xE4, 0, { 255, 0 } },"]
    d.append("};\n")

    vid_rows = []
    for k, (aid, vd) in enumerate(build.videos):
        n = len(vd.records)
        cs, os_ = [], []
        for _ in range(n):
            c, o = refs[ri]
            ri += 1
            cs.append(c)
            os_.append(o)
        d.append(f"static const uint8_t gbm_v{k}_chunk[{n}] = {{ {', '.join(map(str, cs))} }};")
        d.append(f"static const uint16_t gbm_v{k}_off[{n}] = {{ {', '.join(map(str, os_))} }};")
        d.append(f"static const uint8_t gbm_v{k}_hold[{n}] = {{ {', '.join(str(min(255, h)) for h in vd.holds)} }};")
        pal = "0"
        if cgb:
            d.append(f"static const uint16_t gbm_vid_pal{k}[32] = {{ {', '.join(str(w) for w in pal5_to_words(vd.pals5))} }};")
            pal = f"gbm_vid_pal{k}"
        fc, fo = fade_refs[len(build.images) + k] if cgb else (255, 0)
        vid_rows.append(f"    {{ {n}, {1 if vd.loop else 0}, 0x{vd.bgp:02X}, {pal}, {{ {fc}, {fo} }}, "
                        f"gbm_v{k}_chunk, gbm_v{k}_off, gbm_v{k}_hold }},   /* {aid} */")
    d.append("const gbm_video_t gbm_videos[] = {")
    d += vid_rows or ["    { 0, 0, 0xE4, 0, { 255, 0 }, 0, 0, 0 },"]
    d.append("};\n")

    sc_rows = []
    names = []
    for k, (sc, comp) in enumerate(zip(proj.get("scenes", []), build.scenes)):
        evs = comp.events or [(0, 3, 0)]
        d.append(f"static const gbm_event_t gbm_ev{k}[{len(evs)}] = {{")
        d += [f"    {{ {f}, {op}, {arg} }}," for f, op, arg in evs]
        d.append("};")
        sc_rows.append(f"    {{ {comp.length}, {1 if comp.loop else 0}, {1 if comp.fade_obj else 0}, {len(evs)}, gbm_ev{k} }},")
        names.append(sc["name"])
    d.append("const gbm_scene_t gbm_scenes[] = {")
    d += sc_rows or ["    { 1, 0, 0, 0, 0 },"]
    d.append("};")
    f = out_dir / "gbmedia_data.c"
    f.write_text("\n".join(d) + "\n", encoding="utf-8")
    files.append(f)

    h = [HEAD, "#ifndef GBMEDIA_DATA_H", "#define GBMEDIA_DATA_H", '#include "gbmedia.h"', ""]
    for i, n in enumerate(names):
        h.append(f"#define GBM_SCENE_{_c_ident(n)} {i}")
    h += [f"#define GBM_SCENE_COUNT {len(names)}", f"#define GBM_IMAGE_COUNT {len(build.images)}",
          f"#define GBM_VIDEO_COUNT {len(build.videos)}", "", "#endif", ""]
    (out_dir / "gbmedia_data.h").write_text("\n".join(h), encoding="utf-8")
    files.append(out_dir / "gbmedia_data.h")

    cfg = [HEAD, "#ifndef GBMEDIA_CONFIG_H", "#define GBMEDIA_CONFIG_H",
           f"#define GBM_BYTES_PER_UPDATE {bytes_per_update(proj)}",
           f"#define GBM_RUN_COST {run_cost(proj)}",
           f"#define GBM_LY_STOP {LY_STOP[mode_key(proj)]}"]
    if mode_key(proj) == "cgb_fast":
        cfg.append("#define GBM_CPU_FAST 1        /* カラー: CPU 倍速モードにする */")
    if cgb:
        cfg.append("#define GBM_TARGET_CGB 1")
    cfg += ["#endif", ""]
    (out_dir / "gbmedia_config.h").write_text("\n".join(cfg), encoding="utf-8")
    files.append(out_dir / "gbmedia_config.h")

    for n in ("gbmedia.c", "gbmedia.h"):
        shutil.copy(RUNTIME / n, out_dir / n)
        files.append(out_dir / n)
    return files


def c_sources(out_dir: Path) -> list[Path]:
    out_dir = Path(out_dir)
    return [out_dir / "gbmedia.c", out_dir / "gbmedia_data.c"] + sorted(out_dir.glob("gbmedia_chunk*.c"),
                                                                          key=lambda p: int(re.findall(r"\d+", p.stem)[0]))
