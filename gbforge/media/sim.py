"""再生ライブラリ gbmedia.c と同じ手順で動く Python シミュレーター。

プレビューは「書き出すデータ (更新レコード・イベント)」をこのシミュレーターで再生して作る。
PC 側だけの効果 (アルファブレンドなど) は使わない。ROM と同じ VRAM・パレットの状態から画面を描く。
1 回の update() = 1 VBlank (vsync の直後に呼ばれる前提)。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import DEFAULT_RUN_COST

OP_IMAGE, OP_VIDEO, OP_LEVEL, OP_VIDEO_STOP = 1, 2, 3, 4

DARK = [[(v * k) // 31 for v in range(32)] for k in range(32)]

DMG_GREEN = np.array([[224, 248, 208], [136, 192, 112], [52, 104, 86], [8, 24, 32]], np.uint8)
DMG_GRAY = np.array([[255, 255, 255], [170, 170, 170], [85, 85, 85], [0, 0, 0]], np.uint8)


def dmg_level(pal: int, lv: int) -> int:
    if lv < 0:
        d = -(((-lv) * 3 + 15) // 31)
    else:
        d = (lv * 3 + 15) // 31
    out = 0
    for i in range(0, 8, 2):
        c = (pal >> i) & 3
        if d < 0:
            c = min(3, c - d)
        elif d > 0:
            c = c - d if c > d else 0
        out |= c << i
    return out


def cgb_level(col: int, lv: int) -> int:
    r, g, b = col & 31, (col >> 5) & 31, (col >> 10) & 31
    if lv < 0:
        k = 31 + lv
        r, g, b = DARK[k][r], DARK[k][g], DARK[k][b]
    elif lv > 0:
        k = 31 - lv
        r, g, b = 31 - DARK[k][31 - r], 31 - DARK[k][31 - g], 31 - DARK[k][31 - b]
    return r | (g << 5) | (b << 10)


def pal5_to_words(pals5) -> list[int]:
    out = []
    for p in range(8):
        for c in range(4):
            if pals5 is not None and p < len(pals5):
                r, g, b = (int(x) for x in pals5[p][c])
                out.append(r | (g << 5) | (b << 10))
            else:
                out.append(0)
    return out


@dataclass
class SimImage:
    record: bytes
    bgp: int = 0xE4
    pal: list[int] | None = None       # 32 語 (RGB555)


@dataclass
class SimVideo:
    records: list[bytes]
    holds: list[int]
    loop: bool = False
    bgp: int = 0xE4
    pal: list[int] | None = None


@dataclass
class SimScene:
    length: int
    events: list[tuple[int, int, int]]   # (frame, op, arg)
    loop: bool = False
    fade_obj: bool = False


@dataclass
class Player:
    cgb: bool
    images: list[SimImage]
    videos: list[SimVideo]
    bytes_per_update: int = 512
    run_cost: int = DEFAULT_RUN_COST
    vram: list[bytearray] = field(default_factory=lambda: [bytearray(0x2000), bytearray(0x2000)])

    def __post_init__(self):
        self.visible = 0
        self.level = 0
        self.bgp_base = 0xE4
        self.pal_base = [0] * 32
        self.pal_next = [0] * 32
        self.pal_pending = False
        self.bgp_next = 0xE4
        self.bgp_pending = False
        self.cur_bgp = 0xE4                 # 実際の BGP レジスタ
        self.cur_pal = [0] * 32             # 実際の BG パレット RAM
        self.scene = None
        self.flips = 0

    # ---- 状態 ----
    def load_scene(self, sc: SimScene):
        self.scene = sc
        self.ev_i = 0
        self.vid = None
        self.up = None
        self.pending_flip = False
        self.now = 0
        self.late = 0
        self.max_lag = 0
        self.video_frame = 0

    def apply_level(self):
        if self.cgb:
            self.cur_pal = [cgb_level(c, self.level) for c in self.pal_base]
        else:
            self.cur_bgp = dmg_level(self.bgp_base, self.level)

    def flip(self):
        self.visible ^= 1
        if self.pal_pending:
            self.pal_base = list(self.pal_next)
            self.pal_pending = False
        if self.bgp_pending:
            self.bgp_base = self.bgp_next
            self.bgp_pending = False
        self.apply_level()
        self.flips += 1

    def load_base_palette(self, pal, bgp):
        self.bgp_next = bgp
        self.bgp_pending = True
        self.pal_next = list(pal) if pal else list(self.pal_base)
        self.pal_pending = True

    # ---- 転送 ----
    def up_begin(self, rec: bytes, buf: int, is_image: bool):
        self.up = {"ops": list(self._parse(rec, buf)), "i": 0, "off": 0, "is_image": is_image}

    def _parse(self, rec: bytes, buf: int):
        """レコードを (種類, 番地, VRAMバンク, データ) の列に。順番は C と同じ。"""
        p = 0
        flags = rec[p]
        p += 1
        if flags & 1:
            n = rec[p]
            p += 1
            for _ in range(n):
                slot = rec[p] | (rec[p + 1] << 8)
                cnt = rec[p + 2]
                p += 3
                b = slot & 0xFF
                addr = 0x9000 + (b << 4) if b < 128 else 0x8800 + ((b - 128) << 4)
                yield ("vram", addr, (slot >> 8) & 1, rec[p:p + cnt * 16])
                p += cnt * 16
        for bit, vbk in ((2, 0), (4, 1)):
            if flags & bit:
                n = rec[p]
                p += 1
                base = 0x9C00 if buf else 0x9800
                for _ in range(n):
                    pos = rec[p] | (rec[p + 1] << 8)
                    ln = rec[p + 2]
                    p += 3
                    yield ("vram", base + pos, vbk, rec[p:p + ln])
                    p += ln
        if flags & 8:
            first, cnt = rec[p], rec[p + 1]
            p += 2
            words = [rec[p + 2 * i] | (rec[p + 2 * i + 1] << 8) for i in range(cnt * 4)]
            p += cnt * 8
            yield ("pal", first, 0, words)
        if flags & 16:
            yield ("bgp", rec[p], 0, b"")
            p += 1

    def up_step(self, budget: int) -> bool:
        u = self.up
        if u is None:
            return True
        ops = u["ops"]
        while u["i"] < len(ops):
            kind, a, vbk, data = ops[u["i"]]
            if kind == "pal":
                if not self.pal_pending:
                    self.pal_next = list(self.pal_base)
                self.pal_next[a * 4:a * 4 + len(data)] = data
                self.pal_pending = True
                u["i"] += 1
                continue
            if kind == "bgp":
                self.bgp_next = a
                self.bgp_pending = True
                u["i"] += 1
                continue
            if u["off"] == 0 and not u.get("paid"):
                if budget < self.run_cost:          # 区間を始めるには RUN_COST 必要 (再生ライブラリと同じ規則)
                    return False
                budget -= self.run_cost
                u["paid"] = True
            if budget <= 0:
                return False
            off = u["off"]
            n = min(len(data) - off, budget)
            start = a + off - 0x8000
            self.vram[vbk if self.cgb else 0][start:start + n] = data[off:off + n]
            budget -= n
            u["off"] += n
            if u["off"] >= len(data):
                u["i"] += 1
                u["off"] = 0
                u["paid"] = False
        self.up = None
        return True

    # ---- 1 フレーム ----
    def update(self) -> bool:
        sc = self.scene
        if sc is None:
            return False
        now = self.now
        if self.pending_flip:
            self.flip()
            self.pending_flip = False
        elif self.vid is not None and self.vready and now - self.due >= 0:
            if self.vfirst:                 # 最初のフレームを表示した時刻を基準にする (先読みの分ずらさない)
                self.due = now
                self.vfirst = False
            if self.vframe > 0 and now != self.due:
                self.late += 1
                self.max_lag = max(self.max_lag, now - self.due)
            self.flip()
            self.video_frame = self.vframe
            self.due += self.vid.holds[self.vframe]
            self.vframe += 1
            self.vready = False
            if self.vframe >= len(self.vid.records):
                if self.vid.loop:
                    self.vframe = 0
                else:
                    self.vid = None
        while self.ev_i < len(sc.events) and sc.events[self.ev_i][0] <= now:
            self._event(sc.events[self.ev_i])
            self.ev_i += 1
        if self.up is not None:
            is_img = self.up["is_image"]
            if self.up_step(self.bytes_per_update):
                if is_img:
                    self.pending_flip = True
                else:
                    self.vready = True
        elif self.vid is not None and not self.vready:
            buf = (self.vbase ^ 1) if (self.vframe & 1) else self.vbase
            self.up_begin(self.vid.records[self.vframe], buf, False)
            if self.up_step(self.bytes_per_update):
                self.vready = True
        alive = True
        if now >= sc.length:
            if sc.loop:
                self.load_scene(sc)     # C と同じ: 次の update の now は 1 から
            else:
                self.scene = None
                alive = False
        self.now += 1
        return alive

    def _event(self, ev):
        frame, op, arg = ev
        if op == OP_IMAGE:
            im = self.images[arg]
            self.vid = None
            self.up_begin(im.record, self.visible ^ 1, True)
            self.load_base_palette(im.pal, im.bgp)
        elif op == OP_VIDEO:
            v = self.videos[arg]
            self.vid = v
            self.vframe = 0
            self.vfirst = True
            self.vready = False
            self.vbase = self.visible ^ 1
            self.due = frame
            self.load_base_palette(v.pal, v.bgp)
            self.up = None
        elif op == OP_LEVEL:
            self.level = arg - 256 if arg > 127 else arg
            self.apply_level()
        elif op == OP_VIDEO_STOP:
            self.vid = None

    # ---- 描画 ----
    def render_indices(self) -> tuple[np.ndarray, np.ndarray]:
        """(144,160) 色番号と (144,160) パレット番号 (DMG は 0)。"""
        base = 0x1C00 if self.visible else 0x1800
        v0 = self.vram[0]
        v1 = self.vram[1]
        mp = np.frombuffer(bytes(v0[base:base + 0x400]), np.uint8).reshape(32, 32)[:18, :20]
        at = np.frombuffer(bytes(v1[base:base + 0x400]), np.uint8).reshape(32, 32)[:18, :20] if self.cgb \
            else np.zeros((18, 20), np.uint8)
        idx = np.zeros((144, 160), np.uint8)
        palm = np.zeros((144, 160), np.uint8)
        for ty in range(18):
            for tx in range(20):
                b = int(mp[ty, tx])
                a = int(at[ty, tx])
                addr = (0x1000 + (b << 4)) if b < 128 else (0x0800 + ((b - 128) << 4))
                bank = v1 if (self.cgb and a & 0x08) else v0
                raw = bank[addr:addr + 16]
                rows = np.frombuffer(bytes(raw), np.uint8).reshape(8, 2)
                sh = np.arange(7, -1, -1)
                tile = ((rows[:, 0:1] >> sh) & 1) | (((rows[:, 1:2] >> sh) & 1) << 1)
                if a & 0x20:
                    tile = tile[:, ::-1]
                if a & 0x40:
                    tile = tile[::-1, :]
                idx[ty * 8:ty * 8 + 8, tx * 8:tx * 8 + 8] = tile
                palm[ty * 8:ty * 8 + 8, tx * 8:tx * 8 + 8] = a & 7
        return idx, palm

    def render(self, mode: str = "auto") -> np.ndarray:
        idx, palm = self.render_indices()
        if self.cgb and mode != "dmg_gray" and mode != "dmg_green":
            words = np.array(self.cur_pal, np.int32).reshape(8, 4)
            w = words[palm, idx]
            r, g, b = w & 31, (w >> 5) & 31, (w >> 10) & 31
            return np.stack([(c << 3) | (c >> 2) for c in (r, g, b)], axis=-1).astype(np.uint8)
        shade_of = np.array([(self.cur_bgp >> (2 * c)) & 3 for c in range(4)], np.uint8)
        lut = DMG_GRAY if mode in ("dmg_gray", "gray") else DMG_GREEN
        return lut[shade_of[idx]]


def run(player: Player, scene: SimScene, frames: int | None = None, mode="auto", every: int = 1):
    """シーンを再生して画面 (RGB) の列を返す。"""
    player.load_scene(scene)
    out = []
    n = frames if frames is not None else scene.length + 1
    for f in range(n):
        alive = player.update()
        if f % every == 0:
            out.append(player.render(mode))
        if not alive:
            break
    return out
