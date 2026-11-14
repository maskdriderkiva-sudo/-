"""デモ ROM をエミュレーター (PyBoy) で動かし、プレビュー (シミュレーター) と 1 フレームずつ比べる。

プレビューの画像だけで「ROM で再生できた」と判断しないための検証。PyBoy (pip install pyboy) がある時だけ使える。
報告する値:
  compared / matched   比べたフレーム数 / 完全に一致したフレーム数 (DMG は色番号、CGB は RGB555 で比較)
  late, max_lag        予定より遅れて表示された動画フレームの数と最大の遅れ (実機での実測)
  cut                  時間切れで転送を打ち切った回数 (0 ならプレビューと同じ進み方が保証される)
  overruns             update が次の VBlank にはみ出した回数 (フレーム落ち)
"""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np

from .demo import symbol_addresses
from .studio import Build


class VerifyUnavailable(Exception):
    pass


def _levels(img: np.ndarray) -> np.ndarray:
    g = img.astype(np.int32)
    lum = (g[..., 0] * 299 + g[..., 1] * 587 + g[..., 2] * 114) // 1000
    return np.digitize(lum, [43, 128, 213])


def verify_rom(build: Build, scene_index: int, rom: Path, max_frames: int | None = None, log=None) -> dict:
    try:
        from pyboy import PyBoy
    except ImportError as e:  # noqa: F841
        raise VerifyUnavailable("PyBoy がインストールされていません (pip install pyboy)")
    cgb = build.proj.get("target") == "cgb"
    syms = symbol_addresses(rom)
    need = ["_gbmedia_frame", "_gbmedia_late_frames", "_gbmedia_max_lag", "_gbmedia_cut_updates", "_gbm_demo_done"]
    if any(n not in syms for n in need):
        raise VerifyUnavailable("ROM のシンボル (.noi) が見つかりません。デモ ROM を作り直してください")
    # プレビュー側
    pl = build.player()
    sc = build.sim_scene(scene_index)
    pl.load_scene(sc)
    length = sc.length if max_frames is None else min(sc.length, max_frames)
    sim = []
    for _ in range(length + 1):
        pl.update()
        sim.append(pl.render("dmg_gray" if not cgb else "auto"))
    # エミュレーター側
    tmp = Path(str(rom) + ".verify" + (".gbc" if cgb else ".gb"))
    shutil.copy(rom, tmp)
    pb = PyBoy(str(tmp), window="null", sound_emulated=False, cgb=cgb)
    try:
        pb.set_emulation_speed(0)
        m = pb.memory
        rd = lambda n: m[syms[n]] | (m[syms[n] + 1] << 8)  # noqa: E731
        done0 = m[syms["_gbm_demo_done"]]
        compared = matched = overruns = 0
        mism = []
        late = lag = cut = 0
        prev = None
        started = False
        for _ in range((length + 200) * 2):
            pb.tick()
            if m[syms["_gbm_demo_done"]] != done0:
                break
            now = rd("_gbmedia_frame")
            if not started:
                # 最初の update が済むまでは gbmedia_frame が 0 のまま (VBlank 待ち) なので比べない
                if now >= 1:
                    started = True
                prev = now
                continue
            late, lag, cut = rd("_gbmedia_late_frames"), rd("_gbmedia_max_lag"), rd("_gbmedia_cut_updates")
            if now == prev:
                continue
            if now == prev + 2:
                overruns += 1
            prev = now
            if now >= length:
                break
            # update(now) の結果は次のフレームに描かれる
            pb.tick()
            emu = np.asarray(pb.screen.image.convert("RGB"))
            s = sim[now]
            if cgb:
                ok = np.all((emu.astype(np.int32) >> 3) == (s.astype(np.int32) >> 3), axis=2)
            else:
                ok = _levels(emu) == _levels(s)
            r = float(np.mean(ok))
            compared += 1
            if r == 1.0:
                matched += 1
            else:
                mism.append((now, round(r * 100, 2)))
            n2 = rd("_gbmedia_frame")
            if n2 == now + 2:
                overruns += 1
            prev = n2
            if n2 >= length:
                break
    finally:
        pb.stop(save=False)
        tmp.unlink(missing_ok=True)
    res = {"compared": compared, "matched": matched, "mismatches": mism[:20], "late": late, "max_lag": lag,
           "cut": cut, "overruns": overruns, "sim_late": pl.late, "sim_max_lag": pl.max_lag}
    if log:
        log(summary(res))
    return res


def summary(res: dict) -> str:
    ok = res["compared"] > 0 and res["matched"] == res["compared"] and res["late"] == res["sim_late"] and \
        res["cut"] == 0 and res["overruns"] == 0
    head = "✔ ROM の再生はプレビューと一致しました" if ok else "⚠ ROM の再生にプレビューとの違いがあります"
    s = (f"{head}: {res['matched']}/{res['compared']} フレームが一致、"
         f"遅れた動画フレーム {res['late']} (最大 {res['max_lag']} フレーム遅れ、プレビューでは {res['sim_late']})、"
         f"転送の打ち切り {res['cut']} 回、フレーム落ち {res['overruns']} 回")
    if res["mismatches"]:
        s += "\n  一致しなかったフレーム (番号, 一致率%): " + ", ".join(f"{a}:{b}" for a, b in res["mismatches"][:8])
    return s
