"""GB Media Studio の HTTP API (gbforge/server.py から呼ばれる)。

プロジェクト内の配置
  media/<名前>.gbmedia        メディアプロジェクト (JSON)。保存のたびに 1 つ前を .gbmedia.bak に残す
  media/sources/              取り込んだ元の写真・動画 (コピー。元のファイルは書き換えない)
  media/.cache/               変換結果のキャッシュ (消しても作り直される)
  <ソースの場所>/gbmedia/      書き出した C ソース (GBForge のビルドで自動的にコンパイルされる)
  build/media_demo/           単体 ROM プレビュー

時間のかかる処理 (変換・ビルド・書き出し・デモ ROM・検証) はジョブとして実行する。
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import shutil
import threading
from pathlib import Path

import numpy as np
from PIL import Image

from . import BYTES_PER_UPDATE, GB_FPS, LY_STOP, RUN_COST, demo, encode, export, imageproc, studio, timeline, video
from .sim import Player, SimImage, SimScene, pal5_to_words

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif"}
VIDEO_EXT = {".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v"}
NAME_RE = re.compile(r"^[A-Za-z0-9_\-ぁ-んァ-ヶー一-龠々]{1,40}$")

_builds: dict[str, studio.Build] = {}       # .gbmedia の絶対パス → 最後のビルド
_lock = threading.Lock()


def media_dir(p) -> Path:
    return p.root / "media"


def _proj_file(p, rel: str) -> Path:
    f = p.resolve(rel)
    if f.suffix != ".gbmedia":
        raise ValueError("GB Media Studio のファイル (.gbmedia) ではありません")
    return f


def _root_of(f: Path) -> Path:
    """素材のパスの基準 (.gbmedia のあるフォルダ)"""
    return f.parent


def _cache_dir(f: Path) -> Path:
    return f.parent / ".cache" / f.stem


def out_dir(p, proj: dict) -> Path:
    rel = (proj.get("output") or {}).get("dir") or "gbmedia"
    if Path(rel).is_absolute() or ".." in Path(rel).parts:
        raise ValueError("出力先はプロジェクト内の相対パスにしてください")
    return p.code_root / rel


def _png_b64(a: np.ndarray) -> str:
    buf = io.BytesIO()
    Image.fromarray(a.astype(np.uint8)).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def list_projects(p) -> list[dict]:
    out = []
    d = media_dir(p)
    for f in sorted(d.glob("*.gbmedia")) if d.exists() else []:
        try:
            j = json.loads(f.read_text(encoding="utf-8"))
            out.append({"path": f.relative_to(p.root).as_posix(), "name": j.get("name", f.stem),
                        "target": j.get("target", "dmg"), "scenes": len(j.get("scenes", []))})
        except (OSError, ValueError):
            out.append({"path": f.relative_to(p.root).as_posix(), "name": f.stem, "error": "読み込めません"})
    return out


def info() -> dict:
    """UI が使う定数・選択肢 (ツールチップの説明もここから)。"""
    return {
        "fps": GB_FPS, "bytes_per_update": BYTES_PER_UPDATE, "run_cost": RUN_COST, "ly_stop": LY_STOP,
        "fx_types": timeline.FX_TYPES, "presets": {k: v["label"] for k, v in imageproc.PRESETS.items()},
        "preset_params": {k: v["params"] for k, v in imageproc.PRESETS.items()},
        "defaults": imageproc.DEFAULT_PARAMS,
        "ffmpeg": video.find_ffmpeg(None), "pyav": video.has_pyav(),
    }


# ---------------------------------------------------------------------------
# プレビュー (書き出すデータから再生ライブラリと同じ規則で描く)
# ---------------------------------------------------------------------------
def render_scene(b: studio.Build, si: int) -> dict:
    cgb = b.proj.get("target") == "cgb"
    pl = b.player()
    sc = b.sim_scene(si)
    pl.load_scene(sc)
    screens, sidx, pals, pidx, frames = [], {}, [], {}, []
    for _ in range(sc.length + 1):
        pl.update()
        idx, palm = pl.render_indices()
        raw = ((palm.astype(np.uint8) << 2) | idx.astype(np.uint8)).tobytes()
        k = hashlib.sha1(raw).digest()
        if k not in sidx:
            sidx[k] = len(screens)
            screens.append(base64.b64encode(raw).decode())
        pal = tuple(pl.cur_pal) if cgb else (pl.cur_bgp,)
        if pal not in pidx:
            pidx[pal] = len(pals)
            pals.append(list(pal))
        frames.append([sidx[k], pidx[pal], pl.video_frame if pl.vid is not None else -1])
    return {"target": b.proj.get("target"), "length": sc.length, "loop": sc.loop, "screens": screens,
            "palettes": pals, "frames": frames, "late": pl.late, "max_lag": pl.max_lag,
            "levels": b.scenes[si].levels}


def quick_preview(p, f: Path, proj: dict, asset_id: str, t: float = 0.0) -> dict:
    """設定画面用: 1 枚だけ変換して、元画像・変換結果・タイル数などを返す (動画は時刻 t の 1 コマ)。"""
    a = next((x for x in proj.get("assets", []) if x["id"] == asset_id), None)
    if not a:
        raise ValueError("素材がありません")
    root = _root_of(f)
    src = root / a["path"]
    target = proj.get("target", "dmg")
    params = a.get("params") or {}
    kind = a.get("kind") or video.kind_of(src)
    if kind == "image":
        im = imageproc.load_image(src)
    else:
        vp = a.get("video") or {}
        im = video.frame_at(src, float(vp.get("start", 0) or 0) + t * float(vp.get("speed", 1) or 1),
                            (proj.get("settings") or {}).get("ffmpeg") or None)
    ind = imageproc.convert_frame(im, params, target)
    cfg = encode.EncodeConfig(target=target, bytes_per_update=studio.bytes_per_update(proj), run_cost=studio.run_cost(proj))
    lim = int(imageproc.merged_params(params).get("tile_limit") or 0)
    st = encode.encode_still(ind, cfg, lim)
    # 実際に送るデータ (レコード) から描き直す
    pl = Player(target == "cgb", [SimImage(st.record, st.bgp, pal5_to_words(st.pals5) if target == "cgb" else None)],
                [], studio.bytes_per_update(proj), studio.run_cost(proj))
    pl.load_scene(SimScene(st.updates(studio.bytes_per_update(proj)) + 3, [(0, 1, 0)]))
    while pl.update():
        pass
    out = pl.render("auto")
    pals = None
    if target == "cgb":
        pals = [[[int(c) for c in col] for col in pal] for pal in st.pals5]
    return {"source": _png_b64(imageproc.source_preview(im, params)), "converted": _png_b64(out),
            "tiles": st.tiles, "merged": st.merged, "vram_bytes": st.vram_bytes, "rom_bytes": len(st.record),
            "load_frames": st.updates(studio.bytes_per_update(proj)), "warnings": st.warnings, "palettes": pals,
            "size": [im.width, im.height]}


# ---------------------------------------------------------------------------
# ジョブ
# ---------------------------------------------------------------------------
def do_build(p, f: Path, log) -> studio.Build:
    proj = studio.load(f)
    log(f"変換・エンコード中: {proj.get('name')} ({'カラー' if proj.get('target') == 'cgb' else '初代'})")
    b = studio.build(proj, _root_of(f), _cache_dir(f), log=log)
    with _lock:
        _builds[str(f)] = b
    return b


def get_build(f: Path) -> studio.Build | None:
    with _lock:
        return _builds.get(str(f))


def report_text(b: studio.Build) -> list[str]:
    r = b.report
    out = [f"ROM 約 {r['rom_bytes'] // 1024} KB ({r['rom_banks']} バンク) / 1 フレームの転送 {r['bytes_per_update']} バイト"]
    for a in r["assets"]:
        if a["kind"] == "image":
            out.append(f"  🖼 {a['name']}: タイル {a['tiles']}/{a['tile_limit']}" +
                       (f" (統合 {a['merged_tiles']})" if a["merged_tiles"] else "") +
                       f"、VRAM {a['vram_bytes']} バイト、読み込み {a['load_frames']} フレーム")
        else:
            out.append(f"  🎞 {a['name']}: {a['frames']} 枚、目標 {a['target_fps']} FPS → 実際 {a['achieved_fps']} FPS、"
                       f"1 枚平均 {a['avg_frame_bytes']} バイト (最大 {a['max_frame_bytes']})、CPU 最大 {a['max_cpu_percent']}%、"
                       f"ROM {a['rom_bytes'] // 1024} KB")
    for d in b.diagnostics:
        out.append(("✖ " if d["level"] == "error" else "⚠ ") + d["text"] + (f"\n    → {d['fix']}" if d.get("fix") else ""))
    return out


def do_export(p, f: Path, log) -> dict:
    b = do_build(p, f, log)
    if any(d["level"] == "error" for d in b.diagnostics):
        raise ValueError("エラーがあるため書き出せません")
    proj = b.proj
    out = out_dir(p, proj)
    files = export.export(b, out, (proj.get("output") or {}).get("bank_mode", "auto"),
                          int((proj.get("output") or {}).get("first_bank", 2)))
    rels = [x.relative_to(p.root).as_posix() for x in files]
    for x in rels:
        log("  書き出し: " + x)
    names = [s["name"] for s in proj.get("scenes", [])]
    inc = (out.relative_to(p.code_root).as_posix() + "/") if out != p.code_root else ""
    log("使い方 (C):")
    log(f'  #include "{inc}gbmedia.h"')
    log(f'  #include "{inc}gbmedia_data.h"')
    log("  gbmedia_init();")
    if names:
        log(f"  gbmedia_load_scene(GBM_SCENE_{export._c_ident(names[0])});")
    log("  gbmedia_play();")
    return {"files": rels, "c_files": [x for x in rels if x.endswith(".c")]}


def do_demo(p, f: Path, scene: int, log, run: bool, verify: bool) -> dict:
    from .. import actions, builder
    b = do_build(p, f, log)
    if any(d["level"] == "error" for d in b.diagnostics):
        raise ValueError("エラーがあるためデモ ROM を作れません")
    lcc = builder.find_gbdk(p)
    if not lcc:
        raise ValueError("GBDK-2020 が見つかりません (設定で GBDK のフォルダを指定してください)")
    work = p.root / "build" / "media_demo" / f.stem
    try:
        rom = demo.build_demo(b, scene, Path(lcc), work, log=log)
    except demo.DemoError as e:
        log(str(e))
        raise ValueError("デモ ROM のコンパイルに失敗しました (上のエラーを見てください)")
    res = {"rom": rom.relative_to(p.root).as_posix()}
    if verify:
        from .verify import VerifyUnavailable, summary, verify_rom
        try:
            log("エミュレーター (PyBoy) で再生してプレビューと比べています…")
            r = verify_rom(b, scene, rom)
            log(summary(r))
            res["verify"] = r
        except VerifyUnavailable as e:
            log(f"検証できません: {e}")
    if run:
        emu = ((b.proj.get("settings") or {}).get("emulator") or "").strip()
        if emu:
            import shlex
            import subprocess
            import sys
            cmd = shlex.split(emu, posix=(sys.platform != "win32"))
            cmd = [c.replace("{rom}", str(rom)) for c in cmd] if any("{rom}" in c for c in cmd) else cmd + [str(rom)]
            log("$ " + " ".join(cmd))
            try:
                subprocess.Popen(cmd, cwd=str(rom.parent))
            except OSError as e:
                log(f"エミュレーターを起動できません: {e}")
        else:
            actions.launch_emulator(p, rom, log=log)
    return res


# ---------------------------------------------------------------------------
# ルーティング
# ---------------------------------------------------------------------------
def handle(h, state, m: str, path: str, q: dict) -> bool:
    if not path.startswith("/api/media/"):
        return False
    p = state.project
    sub = path[len("/api/media/"):]

    if sub == "info" and m == "GET":
        h._json(info())
        return True
    if sub == "list" and m == "GET":
        h._json(list_projects(p))
        return True
    if sub == "new" and m == "POST":
        body = h._jbody()
        name = (body.get("name") or "").strip()
        if not NAME_RE.match(name):
            raise ValueError("名前は英数字・日本語・_ - で 40 文字までにしてください")
        f = media_dir(p) / f"{name}.gbmedia"
        if f.exists():
            raise ValueError("同じ名前のメディアプロジェクトがあります")
        tgt = body.get("target") or ("cgb" if p.settings.get("target") in ("cgb", "both") else "dmg")
        proj = studio.new_media_project(name, tgt)
        proj["scenes"][0]["name"] = "opening"
        studio.save(f, proj)
        (media_dir(p) / "sources").mkdir(parents=True, exist_ok=True)
        h._json({"path": f.relative_to(p.root).as_posix()})
        return True

    f = _proj_file(p, q.get("path", ""))

    if sub == "project":
        if m == "GET":
            h._json(studio.load(f))
            return True
        if m == "PUT":
            data = h._jbody()
            if data.get("format") != studio.FORMAT:
                raise ValueError("形式が違います")
            if f.exists():   # 1 つ前を残す (非破壊)
                shutil.copy2(f, f.with_suffix(".gbmedia.bak"))
            studio.save(f, data)
            h._json({"ok": True})
            return True

    if sub == "import" and m == "POST":
        name = Path(unquote_name(q.get("name", ""))).name
        ext = Path(name).suffix.lower()
        if ext not in IMAGE_EXT | VIDEO_EXT:
            raise ValueError("対応していない形式です (画像: PNG/JPEG/BMP/WebP/GIF、動画: MP4/WebM/MOV など)")
        data = h._body()
        if not data:
            raise ValueError("ファイルが空です")
        d = _root_of(f) / "sources"
        d.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"[^\w\-ぁ-んァ-ヶー一-龠々]", "_", Path(name).stem)[:40] or "media"
        dest = d / f"{stem}{ext}"
        n = 2
        while dest.exists() and dest.read_bytes() != data:
            dest = d / f"{stem}_{n}{ext}"
            n += 1
        dest.write_bytes(data)
        kind = "video" if ext in VIDEO_EXT else video.kind_of(dest)
        h._json({"path": dest.relative_to(_root_of(f)).as_posix(), "kind": kind})
        return True

    if sub == "probe" and m == "GET":
        src = (_root_of(f) / q.get("asset_path", "")).resolve()
        if p.root not in src.parents:
            raise ValueError("プロジェクト外のファイルです")
        proj = studio.load(f)
        k = video.kind_of(src)
        if k == "image":
            im = Image.open(src)
            h._json({"kind": "image", "width": im.width, "height": im.height})
        else:
            try:
                vi = video.probe(src, (proj.get("settings") or {}).get("ffmpeg") or None)
                h._json({"kind": k, "duration": vi.duration, "fps": vi.fps, "width": vi.width, "height": vi.height,
                         "backend": vi.backend})
            except video.VideoError as e:
                h._json({"kind": k, "error": str(e)})
        return True

    if sub == "thumb" and m == "GET":
        src = (_root_of(f) / q.get("asset_path", "")).resolve()
        if p.root not in src.parents:
            raise ValueError("プロジェクト外のファイルです")
        proj = studio.load(f)
        try:
            a0 = next((x for x in proj.get("assets", []) if x["id"] == q.get("asset")), None)
            t0 = float(q.get("t", 0)) + float(((a0 or {}).get("video") or {}).get("start", 0) or 0)
            im = imageproc.load_image(src) if video.kind_of(src) == "image" else \
                video.frame_at(src, t0, (proj.get("settings") or {}).get("ffmpeg") or None)
        except (OSError, video.VideoError):
            im = Image.new("RGB", (16, 16), (60, 60, 60))
        im = im.convert("RGB")
        a = next((x for x in proj.get("assets", []) if x["id"] == q.get("asset")), None) if q.get("adjusted") else None
        if a is not None:   # 比較用: 切り抜き・明るさ等の調整をした 160x144 (減色前)
            im = Image.fromarray(imageproc.source_preview(im, a.get("params") or {}))
        else:
            im.thumbnail((int(q.get("w", 160)), int(q.get("w", 160)) * 2))
        buf = io.BytesIO()
        im.save(buf, "PNG")
        h.send_response(200)
        h.send_header("Content-Type", "image/png")
        h.send_header("Content-Length", str(len(buf.getvalue())))
        h.send_header("Cache-Control", "max-age=60")
        h.end_headers()
        h.wfile.write(buf.getvalue())
        return True

    if sub == "preview" and m == "POST":
        body = h._jbody()
        proj = body.get("project") or studio.load(f)
        h._json(quick_preview(p, f, proj, body.get("asset"), float(body.get("t", 0))))
        return True

    if sub == "frames" and m == "GET":
        b = get_build(f)
        if not b:
            raise ValueError("まだビルドしていません")
        si = int(q.get("scene", 0))
        if si >= len(b.scenes):
            raise ValueError("シーンがありません")
        h._json(render_scene(b, si))
        return True

    if sub in ("build", "export", "demo") and m == "POST":
        body = h._jbody()

        def job_fn(job):
            try:
                if sub == "build":
                    b = do_build(p, f, job.write)
                    for line in report_text(b):
                        job.write(line)
                    job.result = {"report": b.report, "diagnostics": b.diagnostics,
                                  "scenes": [{"length": c.length, "warnings": c.warnings} for c in b.scenes]}
                    return not any(d["level"] == "error" for d in b.diagnostics)
                if sub == "export":
                    job.result = do_export(p, f, job.write)
                    return True
                job.result = do_demo(p, f, int(body.get("scene", 0)), job.write, bool(body.get("run")),
                                     bool(body.get("verify")))
                return True
            except (ValueError, studio.MediaError, video.VideoError, OSError) as e:
                job.write(f"エラー: {e}")
                return False
        h._json(state.jobs.start("media_" + sub, job_fn).snapshot())
        return True

    raise ValueError("not found")


def unquote_name(s: str) -> str:
    from urllib.parse import unquote
    return unquote(s)
