"""タイムライン → 再生イベント。

シーン (JSON)
  {"name": "opening", "length": 360, "loop": false, "fade_obj": false,
   "media":   [{"id": "c1", "asset": "a1", "start": 60, "duration": 240}],
   "fx":      [{"id": "f1", "type": "fade_in_black", "start": 60, "duration": 60, ...}],
   "palette": [{"id": "k1", "frame": 0, "level": -31, "interp": "linear"}]}

時間はすべてゲームボーイのフレーム (VBlank, 約 59.73 回/秒) 単位。
明るさ (level) は -31 (真っ黒) 〜 0 (元の色) 〜 +31 (真っ白)。
  - 初代: BGP の 4 階調で表すので実際の段階は -3〜+3 (level を 31/3 ごとに丸める)
  - カラー: RGB555 の各成分を 31 段階で黒/白に近づける
演出の結果はコンパイル時に「何フレーム目に明るさをいくつにするか」というイベント列に決まる
(ランダムな明滅も乱数の種で決まるので、プレビューと ROM は同じになる)。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import GB_FPS

FX_TYPES = {
    "fade_in_black": "黒からフェードイン",
    "fade_out_black": "黒へフェードアウト",
    "fade_in_white": "白からフェードイン",
    "fade_out_white": "白へフェードアウト",
    "flash_white": "白フラッシュ",
    "flash_black": "黒フラッシュ (一瞬暗転)",
    "flicker": "画面の点滅",
    "film": "古いフィルムの明滅",
    "random": "不規則な輝度変化",
    "horror": "ホラー (パレットの急な切り替え)",
}
# 終わった後もその明るさが続く演出 (次のキーフレームか別のフェードまで)
PERSIST = {"fade_out_black": -31, "fade_out_white": 31, "fade_in_black": 0, "fade_in_white": 0}

OP_IMAGE, OP_VIDEO, OP_LEVEL, OP_VIDEO_STOP = 1, 2, 3, 4


def sec(frames: int) -> float:
    return frames / GB_FPS


def frames(seconds: float) -> int:
    return int(round(seconds * GB_FPS))


def dmg_step(level: int) -> int:
    """初代で実際に使われる段階 -3..3 (再生ライブラリと同じ丸め)。"""
    return -(((-level) * 3 + 15) // 31) if level < 0 else (level * 3 + 15) // 31


class _Rand:
    """C 側と無関係な、コンパイル時だけの決まった乱数 (種が同じなら毎回同じ)。"""

    def __init__(self, seed: int):
        self.s = (seed * 2654435761 + 12345) & 0xFFFFFFFF or 1

    def next(self) -> float:
        self.s ^= (self.s << 13) & 0xFFFFFFFF
        self.s ^= self.s >> 17
        self.s ^= (self.s << 5) & 0xFFFFFFFF
        return (self.s & 0xFFFF) / 65536.0


def _keyframe_level(kfs: list[dict], f: int) -> int | None:
    if not kfs:
        return None
    ks = sorted(kfs, key=lambda k: k["frame"])
    if f < ks[0]["frame"]:
        return None
    for a, b in zip(ks, ks[1:]):
        if a["frame"] <= f < b["frame"]:
            if b.get("interp", "linear") == "linear" and b["frame"] > a["frame"]:
                t = (f - a["frame"]) / (b["frame"] - a["frame"])
                return int(round(a["level"] + (b["level"] - a["level"]) * t))
            return int(a["level"])
    return int(ks[-1]["level"])


def _fx_level(fx: dict, f: int, base: int, rnd_cache: dict) -> int:
    s, d = int(fx["start"]), max(1, int(fx["duration"]))
    p = (f - s) / d
    t = fx["type"]
    if t == "fade_in_black":
        return int(round(-31 * (1 - p)))
    if t == "fade_out_black":
        return int(round(-31 * ((f - s + 1) / d)))
    if t == "fade_in_white":
        return int(round(31 * (1 - p)))
    if t == "fade_out_white":
        return int(round(31 * ((f - s + 1) / d)))
    if t in ("flash_white", "flash_black"):
        peak = 31 if t == "flash_white" else -31
        decay = int(fx.get("decay", 0))           # 最後の decay フレームで元に戻る
        hold = d - decay
        if f - s < hold:
            return peak
        q = (f - s - hold + 1) / max(1, decay)
        return int(round(peak + (base - peak) * q))
    inten = int(fx.get("intensity", 16))
    period = max(1, int(fx.get("period", 4)))
    key = (fx.get("id"), t)
    if key not in rnd_cache:
        rnd_cache[key] = _Rand(int(fx.get("seed", 1)) + s)
    r = rnd_cache[key]
    if (f - s) % period:
        return rnd_cache.get((key, "last"), base)
    if t == "flicker":
        v = base - inten if ((f - s) // period) % 2 == 0 else base
    elif t == "film":
        v = base - int(r.next() * inten * 0.4) - (inten if r.next() < 0.12 else 0)
    elif t == "random":
        v = base + int((r.next() * 2 - 1) * inten)
    else:  # horror
        x = r.next()
        v = -31 if x < 0.25 else (31 if x > 0.93 else base - int(r.next() * inten * 0.5))
    v = max(-31, min(31, v))
    rnd_cache[(key, "last")] = v
    return v


def level_track(scene: dict) -> list[int]:
    """各フレームの明るさ (-31..31)。

    - パレットトラックのキーフレームが基本の明るさ (間は直線補間 / ステップ)
    - 「黒/白へフェードアウト」は終わった後もその明るさが続く (次のフェードイン・キーフレームまで)
    - 演出は開始の早い順に重ねる (後の演出が前の結果を上書き・変調する)
    """
    n = scene_length(scene)
    fxs = sorted(scene.get("fx", []), key=lambda x: (int(x["start"]), x.get("id", "")))
    kfs = scene.get("palette", [])
    kf_frames = {int(k["frame"]) for k in kfs}
    out = []
    persist = None
    rnd: dict = {}
    for f in range(n + 1):
        for fx in fxs:
            s, d = int(fx["start"]), max(1, int(fx["duration"]))
            if s + d == f and fx["type"] in PERSIST:
                persist = PERSIST[fx["type"]]
        if f in kf_frames:
            persist = None
        kl = _keyframe_level(kfs, f)
        base = persist if persist is not None else (kl if kl is not None else 0)
        lv = base
        for fx in fxs:
            s, d = int(fx["start"]), max(1, int(fx["duration"]))
            if s <= f < s + d:
                lv = _fx_level(fx, f, lv, rnd)
        out.append(max(-31, min(31, int(lv))))
    return out


def scene_length(scene: dict) -> int:
    ends = [int(scene.get("length") or 0)]
    for c in scene.get("media", []):
        ends.append(int(c["start"]) + int(c["duration"]))
    for fx in scene.get("fx", []):
        ends.append(int(fx["start"]) + int(fx["duration"]))
    for k in scene.get("palette", []):
        ends.append(int(k["frame"]))
    return max(1, max(ends))


@dataclass
class Compiled:
    length: int
    loop: bool
    fade_obj: bool
    events: list[tuple[int, int, int]]
    levels: list[int]
    warnings: list[str] = field(default_factory=list)


def compile_scene(scene: dict, target: str, assets_info: dict, bytes_per_update: int) -> Compiled:
    """assets_info[asset_id] = {"kind": "image"/"video", "index": n, "vram": bytes, "lead_frames": n, "frames": n, "length": vblank数}"""
    n = scene_length(scene)
    levels = level_track(scene)
    warnings: list[str] = []
    events: list[tuple[int, int, int]] = []
    # 明るさ: 初代は実際に変わる段階が変わった時だけ
    last = None
    for f, lv in enumerate(levels[:n + 1]):
        key = dmg_step(lv) if target == "dmg" else lv
        if key != last:
            events.append((f, OP_LEVEL, lv & 0xFF))
            last = key
    # 映像
    clips = sorted(scene.get("media", []), key=lambda c: int(c["start"]))
    prev_start = -1
    for i, c in enumerate(clips):
        info = assets_info.get(c["asset"])
        if info is None:
            warnings.append(f"クリップ {c.get('id')}: 素材がありません")
            continue
        start = int(c["start"])
        lead = int(info.get("lead_frames", 1))
        ev = max(0, start - lead, prev_start + 1)
        op = OP_IMAGE if info["kind"] == "image" else OP_VIDEO
        events.append((ev, op, int(info["index"])))
        # 切り替え中 (読み込み〜表示) に前の絵が見えていると、タイルが重なって乱れることがある
        if i > 0:
            window = levels[ev:start + 2]
            dark = all((dmg_step(v) in (-3, 3)) if target == "dmg" else abs(v) >= 31 for v in window)
            if not dark:
                warnings.append(
                    f"{sec(start):.2f} 秒の切り替え ({info.get('name', c['asset'])}): 読み込み中に前の画面が見えているため、"
                    "VRAM のタイルが重なって一瞬乱れることがあります。暗転・ホワイトアウト中に切り替えると確実です")
        if start - lead < (prev_start + 1 if prev_start >= 0 else 0):
            warnings.append(f"{sec(start):.2f} 秒のクリップ: 読み込みに {lead} フレームかかるため、"
                            f"表示が約 {sec(max(0, (prev_start + 1) - (start - lead))):.2f} 秒遅れます")
        if info["kind"] == "video":
            end = start + int(c["duration"])
            if not info.get("loop") and info.get("length", 0) < int(c["duration"]):
                pass   # 最後のフレームで止まる
            nxt = clips[i + 1]["start"] if i + 1 < len(clips) else None
            if end < scene_length(scene) and (nxt is None or int(nxt) > end):
                events.append((end, OP_VIDEO_STOP, 0))
        prev_start = ev
    events.sort(key=lambda e: (e[0], 0 if e[1] == OP_LEVEL else 1))
    return Compiled(n, bool(scene.get("loop")), bool(scene.get("fade_obj")), events, levels, warnings)


def lead_frames(vram: int, bytes_per_update: int) -> int:
    """読み込みに何フレームかかるか (+1 で次の VBlank に切り替え)。"""
    return int(math.ceil(vram / max(1, bytes_per_update))) + 1
