"""サンプルプロジェクトをビルドし、PyBoy で実際に遊んで検証する。

- GBDK-2020 が見つかれば本物の GBDK でビルドした ROM をテストする
- sdcc (sm83) があればテスト用シム (tests/shim) でビルドした ROM もテストする
環境変数 GBFORGE_SHOTS にディレクトリを指定するとスクリーンショットを保存する。
"""
import os
import re
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

pyboy = pytest.importorskip("pyboy")

from gbforge import builder  # noqa: E402
from gbforge.project import Project  # noqa: E402
from gbforge.templates import create_project  # noqa: E402

TOOLCHAINS = []
if builder.find_gbdk(None):
    TOOLCHAINS.append("gbdk")
if shutil.which("sdcc") and shutil.which("sdldgb"):
    TOOLCHAINS.append("sdcc-shim")
if not TOOLCHAINS:
    pytest.skip("GBDK-2020 または sdcc (sm83) が必要です", allow_module_level=True)

TILE_TEXT_BASE = 192


@pytest.fixture(scope="module", params=TOOLCHAINS)
def rom(request, tmp_path_factory):
    root = tmp_path_factory.mktemp("proj") / "sample"
    create_project(root, "Sample Game")
    res = builder.build(Project(root), log=lambda m: None, toolchain=request.param)
    assert res.ok, "\n".join(res.log[-20:])
    data = res.rom.read_bytes()
    maps = list((root / "build").glob("*.map")) + list((root / "build" / "obj").glob("*.map"))
    mp = maps[0].read_text(errors="replace")
    m = re.search(r"([0-9A-F]{8})\s+_vars\b", mp)
    assert m
    return res.rom, int(m.group(1), 16), data


class Game:
    def __init__(self, path, shots):
        tmp = Path(str(path) + ".run.gb")
        shutil.copy(path, tmp)
        self.pb = pyboy.PyBoy(str(tmp), window="null", sound_emulated=False)
        self.pb.set_emulation_speed(0)
        self.shots = shots
        self.n = 0

    def tick(self, n=1):
        for _ in range(n):
            self.pb.tick()

    def press(self, b, after=30):
        self.pb.button_press(b); self.tick(4); self.pb.button_release(b); self.tick(after)

    def hold(self, b, frames):
        self.pb.button_press(b); self.tick(frames); self.pb.button_release(b); self.tick(4)

    def shot(self, name):
        if self.shots:
            self.n += 1
            self.pb.screen.image.convert("RGB").resize((320, 288)).save(f"{self.shots}/{self.n:02d}_{name}.png")

    def window_open(self):
        m = self.pb.memory
        return bool(m[0xFF40] & 0x20) and m[0xFF4A] == 104

    def window_text_tiles(self):
        m = self.pb.memory
        return [m[0x9C00 + (1 + r) * 32 + 1 + c] for r in range(3) for c in range(18)]

    def var(self, base, i):
        m = self.pb.memory
        v = m[base + i * 2] | (m[base + i * 2 + 1] << 8)
        return v - 0x10000 if v & 0x8000 else v


def test_header(rom):
    _, _, data = rom
    assert data[0x147] == 0x1B
    assert data[0x134:0x13F].rstrip(b"\x00") == b"SAMPLE GAME"
    x = 0
    for b in data[0x134:0x14D]:
        x = (x - b - 1) & 0xFF
    assert data[0x14D] == x
    assert len(data) == 64 * 1024


def test_play_through(rom):
    path, vars_addr, _ = rom
    g = Game(path, os.environ.get("GBFORGE_SHOTS"))
    g.tick(150)
    # 初回メッセージ (日本語) が表示されている
    assert g.window_open()
    tiles = g.window_text_tiles()
    assert sum(1 for t in tiles if t >= TILE_TEXT_BASE + 9) >= 10
    g.shot("welcome")
    g.press("a", 120); g.press("a", 60)
    assert not g.window_open()
    assert g.var(vars_addr, 0) == 1           # VAR_VISITED
    g.shot("town")

    # ドアへ: 左へ 8px, 上へ
    g.hold("left", 8)
    g.hold("up", 60)
    g.tick(120)                                # フェード + シーン切替
    g.shot("house")
    # 家の中: 上へ移動して右を向き、長老に話しかける
    g.hold("up", 70)
    g.hold("right", 20)
    g.shot("before_talk")
    g.press("a", 90)
    assert g.window_open()
    g.shot("choice")
    # 選択肢カーソル (▶) が「はい」の行頭に出ている
    assert g.pb.memory[0x9C00 + 2 * 32 + 1] == TILE_TEXT_BASE + 63
    g.press("a", 120)                          # 「はい」
    g.shot("elder_yes")
    assert g.var(vars_addr, 1) == 1            # VAR_TALKS
    g.press("a", 150)
    g.shot("number")
    for _ in range(4):           # 残りのページを送って閉じる
        if not g.window_open():
            break
        g.press("a", 150)
    assert not g.window_open()
    g.pb.stop(save=False)
