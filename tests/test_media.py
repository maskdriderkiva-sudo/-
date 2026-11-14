"""GB Media Studio のテスト。

前半は Python だけ (変換・エンコード・プレビューと再生ライブラリの規則が一致しているか)。
後半は GBDK-2020 と PyBoy がある時だけ: サンプルからデモ ROM を作り、エミュレーターとプレビューを 1 フレームずつ比べる。
"""
import random
import re
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gbforge import builder  # noqa: E402
from gbforge.media import BYTES_PER_UPDATE, DEFAULT_RUN_COST, encode, imageproc, studio, timeline  # noqa: E402
from gbforge.media.export import fade_table  # noqa: E402
from gbforge.media.sim import DARK, Player, cgb_level, dmg_level  # noqa: E402

SAMPLES = ROOT / "gbforge" / "media" / "samples"
RUNTIME_C = (ROOT / "gbforge" / "media" / "runtime" / "gbmedia.c").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# プレビューと再生ライブラリ (C) の規則が同じか
# ---------------------------------------------------------------------------
def _c_array(name):
    m = re.search(name + r"\[[^\]]*\](?:\[[^\]]*\])?\s*=\s*\{(.*?)\};", RUNTIME_C, re.S)
    assert m, name
    return [int(x) for x in re.findall(r"-?\d+", m.group(1))]


def test_runtime_tables_match_preview():
    dark = _c_array("dark_tab")
    assert dark == [v for row in DARK for v in row]
    steps = _c_array("dmg_step")
    maps = _c_array("dmg_map")
    for pal in range(256):
        for lv in range(-31, 32):
            row = maps[(steps[lv + 31] + 3) * 4:(steps[lv + 31] + 4) * 4]
            got = row[pal & 3] | row[(pal >> 2) & 3] << 2 | row[(pal >> 4) & 3] << 4 | row[pal >> 6] << 6
            assert got == dmg_level(pal, lv)


def test_runtime_constants_match_python(tmp_path):
    h = (ROOT / "gbforge" / "media" / "runtime" / "gbmedia.h").read_text(encoding="utf-8")
    assert re.search(r"#define GBM_RUN_COST %d\b" % DEFAULT_RUN_COST, h)
    from gbforge.media.export import export
    for target, fast in (("dmg", False), ("cgb", False), ("cgb", True)):
        proj = studio.new_media_project("t", target)
        proj["cgb_fast"] = fast
        out = tmp_path / f"{target}{fast}"
        export(studio.build(proj, tmp_path), out)
        cfg = (out / "gbmedia_config.h").read_text(encoding="utf-8")
        assert f"#define GBM_RUN_COST {studio.run_cost(proj)}" in cfg
        assert f"#define GBM_BYTES_PER_UPDATE {studio.bytes_per_update(proj)}" in cfg


def test_fade_table_rows():
    words = [random.Random(i).randrange(0x8000) for i in range(32)]
    t = fade_table(words)
    assert len(t) == 63 * 64
    for lv in (-31, -7, 0, 12, 31):
        row = t[(lv + 31) * 64:(lv + 32) * 64]
        assert [row[i] | row[i + 1] << 8 for i in range(0, 64, 2)] == [cgb_level(w, lv) for w in words]
    assert [t[31 * 64 + i] | t[31 * 64 + i + 1] << 8 for i in range(0, 64, 2)] == words


def test_upload_updates_matches_preview_player():
    """エンコーダーが見積もる転送フレーム数 = プレビュー (と再生ライブラリ) で実際にかかるフレーム数。"""
    rnd = random.Random(1)
    for trial in range(60):
        tiles = [(s, bytes(16 * rnd.randint(1, 6))) for s in sorted(rnd.sample(range(0, 120, 8), rnd.randint(0, 8)))]
        maps = []
        y = 0
        for _ in range(rnd.randint(0, 10)):
            maps.append((y * 32 + rnd.randint(0, 5), bytes(rnd.randint(1, 14))))
            y += 1
        if not tiles and not maps:
            continue
        rec = encode.build_record(tiles, maps, None)
        bpu = rnd.choice([64, 100, 384, 448])
        expect = encode.upload_updates(tiles, maps, None, bpu)
        pl = Player(False, [], [], bpu)
        pl.up_begin(rec, 1, True)
        n = 1
        while not pl.up_step(bpu):
            n += 1
        assert n == expect, (trial, n, expect)


def test_timeline_levels_spec_example():
    """指示書の例: 1 秒暗転 → 1 秒でフェードイン → 表示 → 白フラッシュ → 0.8 秒でフェードアウト。"""
    f = timeline.frames
    sc = {"name": "s", "length": f(6.2), "loop": False, "fade_obj": False, "media": [],
          "fx": [{"id": "a", "type": "fade_in_black", "start": f(1.0), "duration": f(1.0)},
                 {"id": "b", "type": "flash_white", "start": f(5.0), "duration": f(0.2)},
                 {"id": "c", "type": "fade_out_black", "start": f(5.2), "duration": f(0.8)}],
          "palette": [{"id": "k", "frame": 0, "level": -31, "interp": "step"}]}
    lv = timeline.level_track(sc)
    assert lv[0] == -31 and lv[f(0.9)] == -31
    assert -31 < lv[f(1.5)] < 0
    assert lv[f(3.0)] == 0
    assert lv[f(5.0)] > 0          # フラッシュ
    assert lv[f(6.1)] == -31


def _test_image():
    """タイルが 256 枚以内に収まる単純な画像 (統合が起きないので変換結果と完全に一致するはず)。"""
    from PIL import Image
    a = np.zeros((144, 160, 3), np.uint8)
    for y in range(144):
        for x in range(160):
            a[y, x] = ((x // 16) * 25, (y // 16) * 28, ((x + y) // 24) * 20)
    return Image.fromarray(a)


@pytest.mark.parametrize("target", ["dmg", "cgb"])
def test_still_record_roundtrip(target):
    ind = imageproc.convert_frame(_test_image(), {"dither": "none", "fit": "stretch"}, target)
    cfg = encode.EncodeConfig(target=target, bytes_per_update=BYTES_PER_UPDATE[target])
    st = encode.encode_still(ind, cfg)
    assert st.merged == 0
    from gbforge.media.sim import SimImage, SimScene, pal5_to_words
    pl = Player(target == "cgb", [SimImage(st.record, st.bgp, pal5_to_words(st.pals5) if target == "cgb" else None)],
                [], BYTES_PER_UPDATE[target])
    pl.load_scene(SimScene(60, [(0, 1, 0)], False, False))
    for _ in range(60):
        pl.update()
    got = pl.render("dmg_gray" if target == "dmg" else "auto")
    want = ind.rgb()
    if target == "cgb":
        assert np.array_equal(got >> 3, want >> 3)
    else:
        assert np.array_equal(got, want)


def test_video_preview_has_no_late_frames_when_encoder_says_feasible(tmp_path):
    proj = studio.load(SAMPLES / "sample.gbmedia")
    proj["target"] = "dmg"
    b = studio.build(proj, SAMPLES, tmp_path / "cache", only_assets={"v1"})
    vd = b.videos[0][1]
    assert all(not s.over for s in vd.stats)
    proj2 = dict(proj, scenes=[proj["scenes"][2]])
    b = studio.build(proj2, SAMPLES, tmp_path / "cache")
    pl = b.player()
    sc = b.sim_scene(0)
    pl.load_scene(sc)
    while pl.update():
        pass
    assert pl.late == 0 and pl.flips >= len(vd.records)
    rep = b.report["assets"][0]
    assert rep["achieved_fps"] == pytest.approx(rep["target_fps"], abs=0.01)


def test_sample_project_loads_and_does_not_modify_sources(tmp_path):
    before = {p.name: p.stat().st_mtime for p in SAMPLES.iterdir()}
    proj = studio.load(SAMPLES / "sample.gbmedia")
    assert [s["name"] for s in proj["scenes"]] == ["opening", "slideshow", "movie"]
    studio.build(proj, SAMPLES, tmp_path / "cache")
    after = {p.name: p.stat().st_mtime for p in SAMPLES.iterdir()}
    assert before == after


# ---------------------------------------------------------------------------
# ROM (GBDK-2020 + PyBoy)
# ---------------------------------------------------------------------------
GBDK = builder.find_gbdk(None)


def _lcc():
    p = Path(GBDK)          # find_gbdk() は lcc 本体のパスを返す
    return p if p.is_file() else p / "bin" / ("lcc.exe" if sys.platform == "win32" else "lcc")


@pytest.mark.skipif(not GBDK, reason="GBDK-2020 が必要")
@pytest.mark.parametrize("target,fast", [("dmg", False), ("cgb", False), ("cgb", True)])
def test_rom_matches_preview(tmp_path, target, fast):
    pytest.importorskip("pyboy")
    from gbforge.media import demo
    from gbforge.media.verify import verify_rom
    work = tmp_path / "proj"
    shutil.copytree(SAMPLES, work)
    proj = studio.load(work / "sample.gbmedia")
    proj["target"] = target
    proj["cgb_fast"] = fast
    b = studio.build(proj, work, work / "cache")
    for si in (0, 2):      # opening (静止画 + フェード) と movie (動画)
        rom = demo.build_demo(b, si, _lcc(), tmp_path / f"demo{si}", log=lambda m: None)
        r = verify_rom(b, si, rom)
        assert r["compared"] > 100
        assert r["matched"] == r["compared"], r
        assert r["cut"] == 0 and r["overruns"] == 0, r
        assert r["late"] == r["sim_late"] == 0, r
