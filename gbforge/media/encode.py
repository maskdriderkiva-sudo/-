"""静止画・動画を、再生ライブラリ gbmedia が読む「更新レコード」に変換する。

レコード形式は runtime/gbmedia.c の先頭の説明と同じ。
- タイルは VRAM の「スロット」(BG タイル番号 0〜255、カラーはバンク 1 を含めて 0〜511) に置く
- マップは BG マップ 2 枚 (0x9800 / 0x9C00) を交互に使う。フレーム k は (k の偶奇) 側に書き、
  差分は同じ側の 2 フレーム前の内容と比べる
- 動画では、前のフレームから見た目がほとんど変わらないタイルは送らない (差分しきい値)。
  同じ・似たタイルが VRAM に残っていれば再利用し、新しいタイルだけを空きスロットに送る
- 1 フレームに送るバイト数 ÷ 1 回の転送量 (bytes_per_update) が表示時間 (hold) を超えると、
  まず しきい値を上げて送る量を減らし、それでも足りなければそのフレームの表示時間を延ばす (= FPS が落ちる)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import DEFAULT_RUN_COST
from .imageproc import Indexed
from .tiles import encode_2bpp, flip_variants, merge_to_limit, split_tiles, visual

TILE_BYTES = 16
MAP_W = 32


@dataclass
class EncodeConfig:
    target: str = "dmg"                  # dmg / cgb
    slot_base: int = 0                   # 使い始めるスロット
    slot_count: int = 0                  # 使えるスロット数 (0 = DMG 256 / CGB 512)
    bytes_per_update: int = 512          # 1 フレームに VRAM へ送るバイト数
    frame_tile_limit: int = 0            # 動画 1 フレームの固有タイル上限 (0 = スロット数の半分)
    threshold: float = 0.0               # 差分しきい値 0〜100 (0 = 完全に同じ時だけ再利用)
    max_threshold: float = 60.0          # 予算オーバー時に自動で上げる上限
    strict_fps: bool = False             # True: FPS を守るため画質を落としてでも予算内に収める
    run_cost: int = DEFAULT_RUN_COST     # 区間 1 つの手間 (バイト換算。再生ライブラリの GBM_RUN_COST と同じ値)

    def slots(self) -> int:
        return self.slot_count or (512 if self.target == "cgb" else 256)

    def tile_limit(self) -> int:
        return self.frame_tile_limit or self.slots() // 2


# ---------------------------------------------------------------------------
# レコード
# ---------------------------------------------------------------------------
def tile_runs(slot_data: dict[int, bytes]) -> list[tuple[int, bytes]]:
    """連続するスロットをまとめる。番地が飛ぶ所 (128, 256, 384) と 255 枚で区切る。"""
    runs = []
    for s in sorted(slot_data):
        if runs:
            start, buf = runs[-1]
            n = len(buf) // TILE_BYTES
            if s == start + n and s % 128 != 0 and n < 255:
                runs[-1] = (start, buf + slot_data[s])
                continue
        runs.append((s, slot_data[s]))
    return runs


def map_runs(new: np.ndarray, old: np.ndarray | None, gap: int = DEFAULT_RUN_COST) -> list[tuple[int, bytes]]:
    """変わった位置だけを (pos=y*32+x, bytes) の連続区間に。
    区間を分けるより、間の変わっていない gap バイトまでは一緒に送る方が速い (区間 1 つの手間 = run_cost バイト分)。"""
    runs = []
    for y in range(18):
        row_new = new[y * 20:(y + 1) * 20]
        changed = np.ones(20, bool) if old is None else (row_new != old[y * 20:(y + 1) * 20])
        x = 0
        while x < 20:
            if not changed[x]:
                x += 1
                continue
            start = x
            end = x
            while True:
                nxt = end + 1
                while nxt < 20 and not changed[nxt] and nxt - end <= gap:
                    nxt += 1
                if nxt < 20 and changed[nxt] and nxt - end <= gap + 1:
                    end = nxt
                else:
                    break
            runs.append((y * MAP_W + start, bytes(int(v) & 0xFF for v in row_new[start:end + 1])))
            x = end + 1
    return runs


def build_record(tiles: list[tuple[int, bytes]], maps: list[tuple[int, bytes]], attrs: list[tuple[int, bytes]] | None,
                 pal: tuple[int, np.ndarray] | None = None, bgp: int | None = None) -> bytes:
    flags = (1 if tiles else 0) | (2 if maps else 0) | (4 if attrs else 0) | (8 if pal is not None else 0) | \
        (16 if bgp is not None else 0)
    out = bytearray([flags])
    if tiles:
        out.append(len(tiles))
        for slot, data in tiles:
            out += bytes((slot & 0xFF, slot >> 8, len(data) // TILE_BYTES)) + data
    for runs in (maps, attrs):
        if runs:
            assert len(runs) < 256
            out.append(len(runs))
            for pos, data in runs:
                out += bytes((pos & 0xFF, pos >> 8, len(data))) + data
    if pal is not None:
        first, pals5 = pal
        out += bytes((first, len(pals5)))
        for p in pals5:
            for c in p:
                v = int(c[0]) | (int(c[1]) << 5) | (int(c[2]) << 10)
                out += bytes((v & 0xFF, v >> 8))
    if bgp is not None:
        out.append(bgp & 0xFF)
    return bytes(out)


def upload_updates(tiles, maps, attrs, bytes_per_update: int, run_cost: int = DEFAULT_RUN_COST) -> int:
    """再生ライブラリの up_step() と同じ規則で、転送に何回の update (フレーム) が要るかを数える。
    規則: 連続区間を始めるには run_cost が必要 (足りなければ次のフレームへ)。データは 1 バイト 1。"""
    runs = [len(d) for _, d in tiles] + [len(d) for _, d in maps] + [len(d) for _, d in (attrs or [])]
    if not runs:
        return 1
    n, budget = 1, bytes_per_update
    for ln in runs:
        while budget < run_cost:
            n += 1
            budget = bytes_per_update
        budget -= run_cost
        while ln > 0:
            if budget <= 0:
                n += 1
                budget = bytes_per_update
            k = min(ln, budget)
            ln -= k
            budget -= k
    return n


def vram_bytes(tiles, maps, attrs) -> int:
    return sum(len(d) for _, d in tiles) + sum(len(d) for _, d in maps) + sum(len(d) for _, d in (attrs or []))


# ---------------------------------------------------------------------------
# 静止画
# ---------------------------------------------------------------------------
@dataclass
class Still:
    record: bytes
    tiles: int
    vram_bytes: int
    bgp: int
    pals5: np.ndarray | None
    map: np.ndarray            # (360,) スロット
    attr: np.ndarray           # (360,) 属性 (CGB)
    merged: int = 0            # タイル数の上限のために統合したタイル数
    warnings: list[str] = field(default_factory=list)

    runs: tuple = ()           # (tile_runs, map_runs, attr_runs) — 読み込みにかかるフレーム数の計算用
    run_cost: int = DEFAULT_RUN_COST

    def updates(self, bytes_per_update: int) -> int:   # 何フレームで読み込めるか
        return upload_updates(*self.runs, bytes_per_update, self.run_cost) if self.runs else 1


def _unique_tiles(ind: Indexed) -> tuple[list, list, np.ndarray, np.ndarray]:
    """重複を除いたタイル (カラーは反転も利用)。戻り: uniq_tiles, uniq_pal, pos_uid(360), pos_flip(360)"""
    t = split_tiles(ind.idx)
    pal = ind.tile_pal.reshape(-1) if ind.target == "cgb" else np.zeros(360, np.uint8)
    keymap: dict[bytes, tuple[int, int]] = {}
    uniq, upal = [], []
    uid = np.zeros(360, np.int32)
    flip = np.zeros(360, np.uint8)
    for i in range(360):
        hit = None
        variants = flip_variants(t[i]) if ind.target == "cgb" else [(t[i], 0)]
        for v, f in variants:
            k = v.tobytes() + bytes([int(pal[i])])
            if k in keymap:
                hit = (keymap[k][0], f)
                break
        if hit is None:
            keymap[t[i].tobytes() + bytes([int(pal[i])])] = (len(uniq), 0)
            uniq.append(t[i])
            upal.append(int(pal[i]))
            hit = (len(uniq) - 1, 0)
        uid[i], flip[i] = hit
    return uniq, upal, uid, flip


def encode_still(ind: Indexed, cfg: EncodeConfig, tile_limit: int = 0) -> Still:
    uniq, upal, uid, flip = _unique_tiles(ind)
    limit = min(cfg.slots(), tile_limit or cfg.slots())
    merged = 0
    warnings = []
    if len(uniq) > limit:
        # 似たタイルをまとめて上限に収める
        vis_all = visual(ind)
        first_pos = {int(u): int(np.where(uid == u)[0][0]) for u in range(len(uniq))}
        vis = np.stack([vis_all[first_pos[u]] for u in range(len(uniq))])
        counts = np.bincount(uid, minlength=len(uniq))
        target = merge_to_limit(vis, counts, limit)
        merged = len(uniq) - len(set(target.tolist()))
        warnings.append(f"固有タイルが {len(uniq)} 枚あり上限 {limit} 枚を超えたため、似たタイル {merged} 枚を統合しました")
        remap = {}
        new_uniq, new_pal = [], []
        for u in range(len(uniq)):
            r = int(target[u])
            if r not in remap:
                remap[r] = len(new_uniq)
                new_uniq.append(uniq[r])
                new_pal.append(upal[r])
        uid = np.array([remap[int(target[u])] for u in uid], np.int32)
        # 反転情報はそのまま (統合先は元のタイルと似ているので、同じ向きで表示すれば似た見た目になる)
        uniq, upal = new_uniq, new_pal
    slots = np.array([cfg.slot_base + u for u in range(len(uniq))], np.int32)
    data = encode_2bpp(np.stack(uniq)) if uniq else np.zeros((0, 16), np.uint8)
    slot_data = {int(slots[u]): data[u].tobytes() for u in range(len(uniq))}
    pos_slot = slots[uid]
    mp = (pos_slot & 0xFF).astype(np.int32)
    attr = np.zeros(360, np.int32)
    if ind.target == "cgb":
        attr = np.array([upal[uid[i]] | (0x08 if pos_slot[i] >= 256 else 0) | int(flip[i]) for i in range(360)], np.int32)
    tr = tile_runs(slot_data)
    mr = map_runs(mp, None, cfg.run_cost)
    ar = map_runs(attr, None, cfg.run_cost) if ind.target == "cgb" else None
    rec = build_record(tr, mr, ar)
    return Still(rec, len(uniq), vram_bytes(tr, mr, ar), 0xE4, ind.pals5 if ind.target == "cgb" else None,
                 pos_slot, attr, merged, warnings, (tr, mr, ar), cfg.run_cost)


# ---------------------------------------------------------------------------
# 動画
# ---------------------------------------------------------------------------
@dataclass
class FrameStat:
    new_tiles: int
    map_bytes: int
    attr_bytes: int
    vram_bytes: int
    updates: int          # 転送に必要なフレーム数
    hold: int             # 実際の表示 VBlank 数
    threshold: float
    bgp_only: bool = False
    cost: int = 0         # 転送の手間 (バイト + 区切りの数 x RUN_COST)。CPU 時間の目安
    over: bool = False    # 指定 FPS を守れなかった


@dataclass
class Video:
    records: list[bytes]
    holds: list[int]
    stats: list[FrameStat]
    pals5: np.ndarray | None
    bgp: int
    loop: bool
    target_hold: int
    warnings: list[str] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(len(r) for r in self.records)


class _Cache:
    def __init__(self, cfg: EncodeConfig, dim: int):
        n = cfg.slots()
        self.base = cfg.slot_base
        self.n = n
        self.vis = np.full((n, dim), np.inf, np.float32)     # スロットの見た目 (未使用は inf)
        self.key: list[bytes | None] = [None] * n
        self.pal = np.zeros(n, np.int32)
        self.used_at = np.full(n, -1, np.int64)
        self.key2slot: dict[bytes, tuple[int, int]] = {}

    def put(self, s: int, key: bytes, vis: np.ndarray, pal: int, variants: list[tuple[bytes, int]]):
        old = self.key[s]
        if old is not None:
            for k in [k for k, (ss, _) in self.key2slot.items() if ss == s]:
                del self.key2slot[k]
        self.key[s] = key
        self.vis[s] = vis
        self.pal[s] = pal
        for k, f in variants:
            self.key2slot.setdefault(k, (s, f))


def _keys(t: np.ndarray, pal: int, cgb: bool) -> list[tuple[bytes, int]]:
    vs = flip_variants(t) if cgb else [(t, 0)]
    # 反転したタイルがスロット s の内容と一致 → 属性に反転ビットを付けて s を使う
    return [(v.tobytes() + bytes([pal]), f) for v, f in vs]


def encode_video(frames: list[Indexed], hold: int, cfg: EncodeConfig, loop: bool = False,
                 log=None) -> Video:
    cgb = cfg.target == "cgb"
    if loop and len(frames) % 2:
        frames = frames + [frames[-1]]          # マップの裏表を揃えるため偶数枚に
    forbid0: set[int] = set()
    video = None
    for attempt in range(3 if loop else 1):
        video, last_slots, first_slots = _encode_pass(frames, hold, cfg, loop, forbid0)
        if not loop or not (last_slots & first_slots):
            break
        forbid0 = set(last_slots)
    if loop and (last_slots & first_slots):
        video.warnings.append("ループのつなぎ目で先頭フレームの読み込みが最後のフレームと重なり、1 フレーム乱れる可能性があります")
    return video


def _encode_pass(frames, hold, cfg, loop, forbid0):
    cgb = cfg.target == "cgb"
    bpu = cfg.bytes_per_update
    limit = cfg.tile_limit()
    dim = 64 * (3 if cgb else 1)
    cache = _Cache(cfg, dim)
    buf_map = [None, None]          # 各マップの内容 (360,) スロット下位 8 ビット
    buf_attr = [None, None]
    prev_slot = None                # 直前フレームの各位置のスロット
    prev_attr = None
    visible: set[int] = set()
    records, holds, stats = [], [], []
    first_slots: set[int] = set()
    last_bgp = 0xE4
    base_idx = None                 # パレットだけのフレームの基準 (直前の通常フレームの表示内容)
    for k, ind in enumerate(frames):
        t = split_tiles(ind.idx)
        vis = visual(ind)
        pal = ind.tile_pal.reshape(-1).astype(np.int32) if cgb else np.zeros(360, np.int32)

        # --- 初代: パレット (BGP) だけで表せるフレーム ---
        if not cgb and k > 0 and base_idx is not None:
            f = _bgp_mapping(base_idx, ind.idx)
            if f is not None:
                bgp = sum((f[c] & 3) << (2 * c) for c in range(4))
                rec = build_record([], [], None, bgp=bgp)
                records.append(rec)
                holds.append(hold)
                stats.append(FrameStat(0, 0, 0, 0, 1, hold, cfg.threshold, bgp_only=True))
                last_bgp = bgp
                continue

        thr = cfg.threshold
        while True:
            res = _plan_frame(k, t, vis, pal, cache, prev_slot, prev_attr, visible, buf_map, buf_attr,
                              thr, limit, cfg, forbid0 if k == 0 else set())
            need = upload_updates(res["tile_runs"], res["map_runs"], res["attr_runs"], bpu, cfg.run_cost)
            if need <= hold or thr >= cfg.max_threshold * (2.0 if cfg.strict_fps else 1.0):
                break
            thr = max(4.0, thr * 1.6 + 4)
        over = need > hold
        h = max(hold, need)
        # 確定
        for s, key, v, p, variants in res["puts"]:
            cache.put(s, key, v, p, variants)
        for s in res["slots_used"]:
            cache.used_at[s - cache.base] = k
        b = k & 1
        buf_map[b] = res["map"]
        buf_attr[b] = res["attr"]
        prev_slot, prev_attr = res["pos_slot"], res["attr"]
        visible = set(res["slots_used"])
        if k == 0:
            first_slots = set(visible)
        bgp = 0xE4 if (not cgb and last_bgp != 0xE4) else None
        last_bgp = 0xE4
        rec = build_record(res["tile_runs"], res["map_runs"], res["attr_runs"], bgp=bgp)
        records.append(rec)
        holds.append(h)
        nruns = len(res["tile_runs"]) + len(res["map_runs"]) + len(res["attr_runs"] or [])
        stats.append(FrameStat(res["new"], res["map_bytes"], res["attr_bytes"], res["vram"], need, h, thr, over=over,
                               cost=res["vram"] + nruns * cfg.run_cost))
        if not cgb:
            # 表示されている内容 (送ったタイルから再現) をパレットだけのフレームの基準にする
            base_idx = _display_idx(cache, res["pos_slot"])
    warnings = []
    n_over = sum(1 for s in stats if s.over)
    if n_over:
        warnings.append(f"{n_over} / {len(stats)} フレームは転送量が多く、指定の表示時間に間に合わないため表示時間を延ばしました")
    v = Video(records, holds, stats, frames[0].pals5 if cgb else None, 0xE4, loop, hold, warnings)
    return v, visible, first_slots


def _display_idx(cache: _Cache, pos_slot: np.ndarray) -> np.ndarray:
    tiles = np.zeros((360, 8, 8), np.uint8)
    for i, s in enumerate(pos_slot):
        key = cache.key[int(s) - cache.base]
        if key is not None:
            tiles[i] = np.frombuffer(key[:64], np.uint8).reshape(8, 8)
    return tiles.reshape(18, 20, 8, 8).transpose(0, 2, 1, 3).reshape(144, 160)


def _bgp_mapping(base: np.ndarray, cur: np.ndarray, tol: float = 0.003):
    """cur ≒ f(base) となる色番号の対応 f があれば返す (初代のパレット変更だけで表せるフレーム)。"""
    f = []
    for c in range(4):
        m = cur[base == c]
        f.append(int(np.bincount(m, minlength=4).argmax()) if len(m) else c)
    f = np.array(f, np.uint8)
    if np.array_equal(f, np.arange(4)):
        return None                 # 何も変わらない (通常の差分フレームで十分)
    err = np.mean(f[base] != cur)
    return f.tolist() if err <= tol else None


def _plan_frame(k, t, vis, pal, cache: _Cache, prev_slot, prev_attr, visible, buf_map, buf_attr, thr, limit, cfg,
                forbid):
    cgb = cfg.target == "cgb"
    thr_abs = thr * 0.64 * (1.0 if not cgb else 3.0)     # 1 タイル (64 画素) あたりの差の合計
    pos_slot = np.full(360, -1, np.int64)
    pos_attr = np.zeros(360, np.int32)
    flips = np.zeros(360, np.int32)
    # 1) 前のフレームと見た目がほぼ同じ位置はそのまま
    if prev_slot is not None and thr > 0:
        pv = cache.vis[prev_slot - cache.base]
        same = np.abs(pv - vis).sum(1) <= thr_abs
        pos_slot[same] = prev_slot[same]
        pos_attr[same] = prev_attr[same]
        flips[same] = prev_attr[same] & 0x60
    # 2) 完全に同じタイルが VRAM にある
    keys = {}
    for i in np.where(pos_slot < 0)[0]:
        ks = _keys(t[i], int(pal[i]), cgb)
        keys[i] = ks
        hit = cache.key2slot.get(ks[0][0])
        if hit is None and cgb:
            for kk, f in ks[1:]:
                h2 = cache.key2slot.get(kk)
                if h2 is not None:
                    hit = (h2[0], h2[1] ^ f)
                    break
        if hit is not None:
            pos_slot[i] = hit[0]
            flips[i] = hit[1]
    # 3) 似たタイルが VRAM にある
    rest = np.where(pos_slot < 0)[0]
    if len(rest) and thr > 0:
        valid = np.isfinite(cache.vis[:, 0])
        if valid.any():
            vs = np.where(valid)[0]
            d = np.abs(vis[rest][:, None, :] - cache.vis[vs][None]).sum(-1)
            j = np.argmin(d, axis=1)
            ok = d[np.arange(len(rest)), j] <= thr_abs
            pos_slot[rest[ok]] = vs[j[ok]] + cache.base
    # 4) 新しいタイル: 同じもの・似たものをまとめる
    rest = np.where(pos_slot < 0)[0]
    groups: list[list[int]] = []
    gkey: dict[bytes, int] = {}
    reps: list[int] = []
    for i in rest:
        k0 = keys[i][0][0] if i in keys else t[i].tobytes() + bytes([int(pal[i])])
        if k0 in gkey:
            groups[gkey[k0]].append(int(i))
            continue
        if thr > 0 and reps:
            d = np.abs(vis[reps] - vis[i]).sum(1)
            j = int(np.argmin(d))
            if d[j] <= thr_abs:
                groups[j].append(int(i))
                continue
        gkey[k0] = len(groups)
        groups.append([int(i)])
        reps.append(int(i))
    # 5) 1 フレームの固有タイル上限
    used = sorted(set(int(s) for s in pos_slot[pos_slot >= 0]))
    if len(used) + len(groups) > limit and groups:
        fixed_vis = cache.vis[np.array(used, np.int64) - cache.base] if used else np.zeros((0, vis.shape[1]), np.float32)
        allv = np.concatenate([fixed_vis, vis[reps]]) if len(used) else vis[reps]
        counts = np.array([1] * len(used) + [len(g) for g in groups])
        target = merge_to_limit(allv, counts, limit, fixed=len(used))
        merged: dict[int, list[int]] = {}
        for gi, g in enumerate(groups):
            tgt = int(target[len(used) + gi])
            if tgt < len(used):                    # 既に VRAM にあるスロットで代用
                for i in g:
                    pos_slot[i] = used[tgt]
            else:                                  # 別の新しいタイルで代用 (代表を先頭に)
                if tgt not in merged:
                    merged[tgt] = list(groups[tgt - len(used)])
                if tgt != len(used) + gi:
                    merged[tgt].extend(g)
        groups = list(merged.values())
        reps = [g[0] for g in groups]           # 統合先の代表タイルで描く
    # 6) 空きスロットの割り当て (表示中・このフレームで使うスロット・禁止スロット以外、古い順)
    in_use = set(visible) | set(int(s) for s in pos_slot[pos_slot >= 0]) | set(forbid)
    free = [s for s in range(cache.base, cache.base + cache.n) if s not in in_use]
    free.sort(key=lambda s: (cache.used_at[s - cache.base], s))
    if len(free) < len(groups):
        raise RuntimeError("VRAM のスロットが足りません (1 フレームの固有タイル上限を下げてください)")
    chosen = sorted(free[:len(groups)])
    puts = []
    slot_data = {}
    for g, s in zip(groups, chosen):
        r = g[0]
        data = encode_2bpp(t[r][None])[0].tobytes()
        slot_data[s] = data
        ks = _keys(t[r], int(pal[r]), cgb)
        puts.append((s - cache.base, ks[0][0], vis[r], int(pal[r]), ks))
        for i in g:
            pos_slot[i] = s
            flips[i] = 0
    # マップと属性
    mp = (pos_slot & 0xFF).astype(np.int32)
    attr = np.zeros(360, np.int32)
    if cgb:
        # パレットはスロットに書いたタイルのパレットを使う (見た目はそれで比較済み)
        slot_pal = cache.pal.copy()
        for s, _, _, p, _ in puts:
            slot_pal[s] = p
        attr = np.array([int(slot_pal[pos_slot[i] - cache.base]) | (0x08 if pos_slot[i] >= 256 else 0) | int(flips[i])
                         for i in range(360)], np.int32)
    b = k & 1
    mr = map_runs(mp, buf_map[b], cfg.run_cost)
    ar = map_runs(attr, buf_attr[b], cfg.run_cost) if cgb else None
    tr = tile_runs(slot_data)
    mb = sum(len(d) for _, d in mr)
    ab = sum(len(d) for _, d in (ar or []))
    return {"pos_slot": pos_slot, "attr": attr, "map": mp, "tile_runs": tr, "map_runs": mr, "attr_runs": ar,
            "new": len(groups), "map_bytes": mb, "attr_bytes": ab, "vram": len(groups) * 16 + mb + ab,
            "puts": puts, "slots_used": sorted(set(int(s) for s in pos_slot))}
