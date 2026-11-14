"""メディアプロジェクト (.gbmedia) の読み書き、素材の変換 (キャッシュ付き)、ビルド (エンコード + コンパイル + 診断)。

.gbmedia (JSON)
{
  "format": "gbforge-media", "version": 1, "name": "opening",
  "target": "dmg" | "cgb", "cgb_fast": true,
  "assets": [{"id": "a1", "name": "旅館", "path": "media/sources/ryokan.jpg", "kind": "image",
              "params": {...画像変換...},
              "video": {"start": 0, "end": 0, "fps": 10, "speed": 1, "threshold": 8, "tile_limit": 0, "loop": false}}],
  "scenes": [{"name": "opening", "length": 360, "loop": false, "fade_obj": false,
              "media": [...], "fx": [...], "palette": [...]}],
  "output": {"dir": "gbmedia", "bank_mode": "auto", "first_bank": 2},
  "settings": {"ffmpeg": ""}
}
素材ファイル (元の写真・動画) は参照するだけで、書き換えない (非破壊編集)。
"""
from __future__ import annotations

import hashlib
import json
import math
import pickle
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from . import BYTES_PER_UPDATE, GB_FPS, MEASURED_VRAM_BYTES_PER_FRAME, RUN_COST, imageproc, timeline, video
from .encode import EncodeConfig, Still, Video, encode_still, encode_video
from .sim import Player, SimImage, SimScene, SimVideo, pal5_to_words

FORMAT = "gbforge-media"
CACHE_VERSION = 5


class MediaError(Exception):
    pass


def new_media_project(name: str, target: str = "dmg") -> dict:
    return {
        "format": FORMAT, "version": 1, "name": name, "target": target, "cgb_fast": True,
        "assets": [], "scenes": [{"name": "scene1", "length": 360, "loop": False, "fade_obj": False,
                                  "media": [], "fx": [], "palette": []}],
        "output": {"dir": "gbmedia", "bank_mode": "auto", "first_bank": 2},
        "settings": {"ffmpeg": ""},
    }


def load(path: Path) -> dict:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if d.get("format") != FORMAT:
        raise MediaError(f"{Path(path).name} は GB Media Studio のファイルではありません")
    base = new_media_project(d.get("name", Path(path).stem), d.get("target", "dmg"))
    base.update(d)
    return base


def save(path: Path, data: dict):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def mode_key(proj: dict) -> str:
    if proj.get("target") == "cgb":
        return "cgb_fast" if proj.get("cgb_fast", True) else "cgb"
    return "dmg"


def bytes_per_update(proj: dict) -> int:
    return BYTES_PER_UPDATE[mode_key(proj)]


def run_cost(proj: dict) -> int:
    return RUN_COST[mode_key(proj)]


# ---------------------------------------------------------------------------
# 素材の変換 (キャッシュ)
# ---------------------------------------------------------------------------
def _src_sig(path: Path) -> str:
    p = Path(path)
    if p.is_dir():
        return "|".join(f"{f.name}:{f.stat().st_size}:{int(f.stat().st_mtime)}" for f in video.sequence_files(p))
    st = p.stat()
    return f"{st.st_size}:{int(st.st_mtime)}"


def _cache_key(asset: dict, proj: dict, root: Path) -> str:
    src = root / asset["path"]
    payload = json.dumps([CACHE_VERSION, asset.get("kind"), asset.get("params"), asset.get("video"), proj.get("target"),
                          proj.get("cgb_fast"), _src_sig(src)], sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(payload.encode()).hexdigest()[:20]


@dataclass
class AssetResult:
    asset: dict
    kind: str                      # image / video
    still: Still | None = None
    video: Video | None = None
    frames: list = field(default_factory=list)       # 変換後の Indexed (プレビュー・比較用)
    sources: list = field(default_factory=list)      # 調整後 (減色前) の RGB uint8
    fps: float = 0.0
    hold: int = 0


def convert_asset(asset: dict, proj: dict, root: Path, cache_dir: Path | None = None, log=None) -> AssetResult:
    root = Path(root)
    key = _cache_key(asset, proj, root)
    cf = (cache_dir / f"{asset['id']}_{key}.pkl") if cache_dir else None
    if cf and cf.exists():
        try:
            return pickle.loads(cf.read_bytes())
        except Exception:  # noqa: BLE001
            pass
    target = proj.get("target", "dmg")
    src = root / asset["path"]
    kind = asset.get("kind") or video.kind_of(src)
    params = asset.get("params") or {}
    cfg = EncodeConfig(target=target, bytes_per_update=bytes_per_update(proj), run_cost=run_cost(proj))
    if kind == "image":
        im = imageproc.load_image(src)
        ind = imageproc.convert_frame(im, params, target)
        lim = int(imageproc.merged_params(params).get("tile_limit") or 0)
        st = encode_still(ind, cfg, lim)
        res = AssetResult(asset, "image", still=st, frames=[ind],
                          sources=[imageproc.source_preview(im, params)])
    else:
        vp = asset.get("video") or {}
        fps = float(vp.get("fps", 10))
        hold = max(1, int(round(GB_FPS / fps)))
        real_fps = GB_FPS / hold
        imgs = video.extract(src, real_fps, float(vp.get("start", 0) or 0), float(vp.get("end") or 0) or None,
                             float(vp.get("speed", 1) or 1), (proj.get("settings") or {}).get("ffmpeg") or None,
                             float(vp.get("seq_fps", 10)), int(vp.get("max_frames", 600)))
        if not imgs:
            raise MediaError(f"{asset.get('name') or asset['path']}: フレームを取り出せませんでした")
        if log:
            log(f"{asset.get('name') or asset['path']}: {len(imgs)} フレームを変換しています…")
        p = imageproc.merged_params(params)
        pals5 = None
        if target == "cgb":
            # 動画全体で共通のパレット (途中で色が変わってちらつかないように)
            sample = imgs[::max(1, len(imgs) // 8)][:8]
            arrs = [imageproc.adjust(imageproc.fit_image(i, p), p) for i in sample]
            pals5 = imageproc.build_cgb_palettes(arrs, max(1, min(8, int(p.get("palettes", 8)))))
        inds = [imageproc.convert_frame(i, params, target, pals5) for i in imgs]
        cfg.threshold = float(vp.get("threshold", 8))
        cfg.frame_tile_limit = int(vp.get("tile_limit", 0) or 0)
        cfg.strict_fps = bool(vp.get("strict_fps", False))
        vd = encode_video(inds, hold, cfg, loop=bool(vp.get("loop", False)))
        res = AssetResult(asset, "video", video=vd, frames=inds, fps=real_fps, hold=hold,
                          sources=[imageproc.source_preview(imgs[0], params)])
    if cf:
        cf.parent.mkdir(parents=True, exist_ok=True)
        for old in cf.parent.glob(f"{asset['id']}_*.pkl"):
            old.unlink()
        cf.write_bytes(pickle.dumps(res))
    return res


# ---------------------------------------------------------------------------
# ビルド
# ---------------------------------------------------------------------------
@dataclass
class Build:
    proj: dict
    results: dict                  # asset id → AssetResult
    images: list                   # [(asset_id, Still)]
    videos: list                   # [(asset_id, Video)]
    scenes: list                   # [timeline.Compiled]
    diagnostics: list              # [{"level": "error|warn|info", "text": ..., "fix": ...}]
    report: dict

    def player(self) -> Player:
        cgb = self.proj.get("target") == "cgb"
        ims = [SimImage(s.record, s.bgp, pal5_to_words(s.pals5) if cgb else None) for _, s in self.images]
        vds = [SimVideo(v.records, v.holds, v.loop, v.bgp, pal5_to_words(v.pals5) if cgb else None)
               for _, v in self.videos]
        return Player(cgb, ims, vds, bytes_per_update(self.proj), run_cost(self.proj))

    def sim_scene(self, i: int) -> SimScene:
        c = self.scenes[i]
        return SimScene(c.length, c.events, c.loop, c.fade_obj)


def build(proj: dict, root: Path, cache_dir: Path | None = None, log=None, only_assets: set | None = None) -> Build:
    root = Path(root)
    target = proj.get("target", "dmg")
    bpu = bytes_per_update(proj)
    used = {c["asset"] for sc in proj.get("scenes", []) for c in sc.get("media", [])}
    results = {}
    diags = []
    for a in proj.get("assets", []):
        if only_assets is not None and a["id"] not in only_assets:
            continue
        if a["id"] not in used and only_assets is None:
            continue
        try:
            results[a["id"]] = convert_asset(a, proj, root, cache_dir, log)
        except (OSError, MediaError, video.VideoError) as e:
            diags.append({"level": "error", "text": f"素材「{a.get('name') or a['path']}」: {e}", "fix": ""})
    images, videos, info = [], [], {}
    for aid, r in results.items():
        name = r.asset.get("name") or Path(r.asset["path"]).name
        if r.kind == "image":
            info[aid] = {"kind": "image", "index": len(images), "vram": r.still.vram_bytes, "name": name,
                         "lead_frames": r.still.updates(bpu) + 1}
            images.append((aid, r.still))
        else:
            first = r.video.stats[0].vram_bytes if r.video.stats else 0
            info[aid] = {"kind": "video", "index": len(videos), "vram": first, "name": name,
                         "lead_frames": (r.video.stats[0].updates if r.video.stats else 1) + 1, "loop": r.video.loop,
                         "length": sum(r.video.holds)}
            videos.append((aid, r.video))
    if len(images) > 255 or len(videos) > 255:
        diags.append({"level": "error", "text": "1 つのメディアプロジェクトに画像・動画はそれぞれ 255 個までです", "fix": ""})
    scenes = []
    for sc in proj.get("scenes", []):
        comp = timeline.compile_scene(sc, target, info, bpu)
        scenes.append(comp)
        for w in comp.warnings:
            diags.append({"level": "warn", "text": f"シーン「{sc['name']}」: {w}", "fix": ""})
    report = make_report(proj, results, images, videos, scenes, diags)
    return Build(proj, results, images, videos, scenes, diags, report)


# ---------------------------------------------------------------------------
# ハードウェア制約の診断
# ---------------------------------------------------------------------------
def make_report(proj, results, images, videos, scenes, diags) -> dict:
    target = proj.get("target", "dmg")
    mk = mode_key(proj)
    bpu = BYTES_PER_UPDATE[mk]
    measured = MEASURED_VRAM_BYTES_PER_FRAME[mk]
    slots = 512 if target == "cgb" else 256
    rom = 0
    assets_rep = []
    for aid, st in images:
        r = results[aid]
        rom += len(st.record) + (64 if target == "cgb" else 0)
        pal_used = len(set(int(x) for x in r.frames[0].tile_pal.reshape(-1))) if target == "cgb" else 1
        frames_to_load = st.updates(bpu)
        assets_rep.append({
            "id": aid, "kind": "image", "name": r.asset.get("name"), "tiles": st.tiles, "tile_limit": slots,
            "palettes": pal_used, "vram_bytes": st.vram_bytes, "rom_bytes": len(st.record),
            "load_frames": frames_to_load, "merged_tiles": st.merged})
        for w in st.warnings:
            diags.append({"level": "warn", "text": f"「{r.asset.get('name')}」: {w}",
                          "fix": "ディザリングを弱くする・コントラストを下げると固有タイルが減り、統合による劣化が減ります"})
    for aid, vd in videos:
        r = results[aid]
        n = len(vd.records)
        rec_bytes = vd.total_bytes
        rom += rec_bytes + n * 4 + (64 if target == "cgb" else 0)
        target_fps = GB_FPS / vd.target_hold
        real_time = sum(vd.holds)
        achieved = n * GB_FPS / max(1, real_time)
        per = [s.vram_bytes for s in vd.stats]
        over = sum(1 for s in vd.stats if s.over)
        max_need = max(s.updates for s in vd.stats) if vd.stats else 0
        # 予算の何 % を使うか (計測値に対する CPU 時間の目安)
        cpu = [min(999, int(100 * (s.cost or s.vram_bytes) / (measured * s.hold))) for s in vd.stats]
        assets_rep.append({
            "id": aid, "kind": "video", "name": r.asset.get("name"), "frames": n, "target_fps": round(target_fps, 2),
            "achieved_fps": round(achieved, 2), "over_frames": over, "rom_bytes": rec_bytes,
            "avg_frame_bytes": int(np.mean(per)) if per else 0, "max_frame_bytes": max(per) if per else 0,
            "avg_new_tiles": round(float(np.mean([s.new_tiles for s in vd.stats])), 1) if vd.stats else 0,
            "max_cpu_percent": max(cpu) if cpu else 0, "avg_cpu_percent": int(np.mean(cpu)) if cpu else 0,
            "bgp_only_frames": sum(1 for s in vd.stats if s.bgp_only), "max_updates": max_need,
            "per_frame_bytes": per, "per_frame_hold": vd.holds, "per_frame_new_tiles": [s.new_tiles for s in vd.stats],
            "feasible": over == 0})
        if over:
            diags.append({
                "level": "warn",
                "text": f"「{r.asset.get('name')}」: 目標 {target_fps:.1f} FPS (1 枚 {vd.target_hold} フレーム表示) に対し、"
                        f"{over}/{n} 枚は VRAM 転送に最大 {max_need} フレームかかるため表示時間を延ばしました "
                        f"(1 フレームに送れるのは {bpu} バイト、区切り 1 つごとに {RUN_COST[mk]} バイト分を差し引く)。"
                        f"平均は約 {achieved:.2f} FPS になります",
                "fix": "改善するには: ①目標 FPS を下げる ②差分しきい値を上げる (画質↓・容量↓) ③ディザリングを弱める・"
                       "「イラスト」系の設定にする (細かな変化が減る)" + (" ④カラーなら CPU 倍速モードを使う" if target == "cgb" and
                                                                         not proj.get("cgb_fast", True) else "")})
        for w in vd.warnings:
            if "表示時間を延ば" not in w:
                diags.append({"level": "warn", "text": f"「{r.asset.get('name')}」: {w}", "fix": ""})
    rom += 2600   # 再生ライブラリ
    banks = math.ceil(rom / 16384) + 1
    if rom > 4 * 1024 * 1024:
        diags.append({"level": "error", "text": f"ROM が約 {rom // 1024} KB になり、MBC5 の上限 (8MB) に近い・超えています",
                      "fix": "動画の長さ・FPS を減らすか、差分しきい値を上げてください"})
    return {"target": target, "mode": mk, "bytes_per_update": bpu, "measured_bytes_per_frame": measured,
            "vram_slots": slots, "rom_bytes": rom, "rom_banks": banks, "assets": assets_rep,
            "scenes": [{"length": s.length, "events": len(s.events)} for s in scenes]}
