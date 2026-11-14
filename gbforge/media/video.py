"""動画の読み込み (フレームの取り出し)。

使う順番:
  1. ffmpeg (ユーザーが入れたもの: 設定のパス → PATH → C:\\ffmpeg\\bin など)
     ※ GBForge には ffmpeg を同梱しない (ライセンス上の配布条件を避けるため)。
       Windows では「winget install Gyan.FFmpeg」などで入れる。
  2. PyAV (Python に av が入っていれば)
  3. GIF / アニメーション WebP / 連番画像は Pillow だけで読める
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .imageproc import IMAGE_EXTS, gif_frames

VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v"}
DECODE_W = 320          # 取り出すときの横幅 (この後 160x144 に変換する)


class VideoError(Exception):
    pass


@dataclass
class VideoInfo:
    duration: float
    fps: float
    width: int
    height: int
    backend: str


def find_ffmpeg(configured: str | None = None) -> str | None:
    cands = []
    if configured:
        cands.append(configured)
    for n in ("ffmpeg", "ffmpeg.exe"):
        w = shutil.which(n)
        if w:
            cands.append(w)
    if os.name == "nt":
        la = os.environ.get("LOCALAPPDATA", "")
        cands += [r"C:\ffmpeg\bin\ffmpeg.exe", r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
                  os.path.join(la, "Microsoft", "WinGet", "Links", "ffmpeg.exe")]
    for c in cands:
        if c and Path(c).is_file():
            return c
    return None


def _ffprobe_for(ffmpeg: str) -> str | None:
    p = Path(ffmpeg)
    probe = p.with_name("ffprobe.exe" if p.suffix.lower() == ".exe" else "ffprobe")
    return str(probe) if probe.exists() else shutil.which("ffprobe")


def has_pyav() -> bool:
    try:
        import av  # noqa: F401
        return True
    except ImportError:
        return False


def kind_of(path: Path) -> str:
    """image / gif / video / sequence"""
    path = Path(path)
    if path.is_dir():
        return "sequence"
    ext = path.suffix.lower()
    if ext in (".gif", ".webp"):
        try:
            if getattr(Image.open(path), "n_frames", 1) > 1:
                return "gif"
        except OSError:
            pass
        return "image"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    raise VideoError(f"対応していないファイルです: {path.name}")


def sequence_files(folder: Path) -> list[Path]:
    files = [f for f in Path(folder).iterdir() if f.suffix.lower() in IMAGE_EXTS]

    def key(f: Path):
        nums = re.findall(r"\d+", f.stem)
        return (int(nums[-1]) if nums else 0, f.name)
    return sorted(files, key=key)


def probe(path: Path, ffmpeg_path: str | None = None, seq_fps: float = 10.0) -> VideoInfo:
    k = kind_of(path)
    if k == "gif":
        frames, durs = gif_frames(path)
        return VideoInfo(sum(durs), len(durs) / max(1e-6, sum(durs)), frames[0].width, frames[0].height, "pillow")
    if k == "sequence":
        fs = sequence_files(path)
        if not fs:
            raise VideoError("フォルダに画像がありません")
        im = Image.open(fs[0])
        return VideoInfo(len(fs) / seq_fps, seq_fps, im.width, im.height, "pillow")
    ff = find_ffmpeg(ffmpeg_path)
    if ff:
        probe_exe = _ffprobe_for(ff)
        if probe_exe:
            r = subprocess.run([probe_exe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                                "stream=width,height,r_frame_rate,duration:format=duration", "-of", "json", str(path)],
                               capture_output=True, text=True)
            if r.returncode == 0:
                j = json.loads(r.stdout)
                st = j["streams"][0]
                num, den = (st.get("r_frame_rate") or "30/1").split("/")
                dur = float(st.get("duration") or j.get("format", {}).get("duration") or 0)
                return VideoInfo(dur, float(num) / float(den or 1), int(st["width"]), int(st["height"]), "ffmpeg")
        r = subprocess.run([ff, "-hide_banner", "-i", str(path)], capture_output=True, text=True, errors="replace")
        m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
        f = re.search(r"([\d.]+) fps", r.stderr)
        s = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", r.stderr)
        if m and s:
            dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
            return VideoInfo(dur, float(f.group(1)) if f else 30.0, int(s.group(1)), int(s.group(2)), "ffmpeg")
        raise VideoError("ffmpeg で動画の情報を読めませんでした")
    if has_pyav():
        import av
        with av.open(str(path)) as c:
            st = c.streams.video[0]
            dur = float(c.duration / 1e6) if c.duration else float(st.duration * st.time_base) if st.duration else 0
            return VideoInfo(dur, float(st.average_rate or 30), st.codec_context.width, st.codec_context.height, "pyav")
    raise VideoError("動画を読むには ffmpeg が必要です。設定で ffmpeg.exe の場所を指定するか、"
                     "「winget install Gyan.FFmpeg」でインストールしてください")


def extract(path: Path, fps: float, start: float = 0.0, end: float | None = None, speed: float = 1.0,
            ffmpeg_path: str | None = None, seq_fps: float = 10.0, max_frames: int = 2000) -> list[Image.Image]:
    """start〜end 秒 (元の動画の時間) を、再生時 fps になるように取り出す。
    speed = 2 なら元の 2 倍速 (元の 2/fps 秒ごとに 1 枚)。"""
    k = kind_of(path)
    step = speed / fps                      # 元の動画で何秒ごとに 1 枚取るか
    if k in ("gif", "sequence"):
        if k == "gif":
            frames, durs = gif_frames(path)
        else:
            fs = sequence_files(path)
            frames, durs = [Image.open(f).convert("RGBA") for f in fs], [1.0 / seq_fps] * len(fs)
        total = sum(durs)
        end = total if end is None else min(end, total)
        out, t = [], start
        starts = []
        acc = 0.0
        for d in durs:
            starts.append(acc)
            acc += d
        while t < end - 1e-6 and len(out) < max_frames:
            i = max(j for j in range(len(starts)) if starts[j] <= t + 1e-9)
            out.append(frames[i])
            t += step
        return out
    ff = find_ffmpeg(ffmpeg_path)
    info = probe(path, ffmpeg_path)
    end = info.duration if end is None or end <= 0 else min(end, info.duration or end)
    dur = max(0.0, end - start)
    n_expected = min(max_frames, max(1, int(dur / step + 1e-6)))
    if ff:
        h = round(info.height * DECODE_W / max(1, info.width) / 2) * 2
        vf = f"fps={fps / speed:.6f},scale={DECODE_W}:{h}:flags=area"
        cmd = [ff, "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(path), "-vf", vf,
               "-frames:v", str(n_expected), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode != 0:
            raise VideoError("ffmpeg の実行に失敗しました: " + r.stderr.decode(errors="replace")[-300:])
        size = DECODE_W * h * 3
        data = r.stdout
        return [Image.frombytes("RGB", (DECODE_W, h), data[i:i + size])
                for i in range(0, len(data) - size + 1, size)][:n_expected]
    if has_pyav():
        import av
        out = []
        with av.open(str(path)) as c:
            st = c.streams.video[0]
            want = start
            for fr in c.decode(st):
                t = float(fr.pts * st.time_base) if fr.pts is not None else 0
                if t + 1e-6 < want:
                    continue
                if t > end or len(out) >= n_expected:
                    break
                out.append(fr.to_image())
                want += step
        return out
    raise VideoError("動画を読むには ffmpeg が必要です。設定で ffmpeg.exe の場所を指定するか、"
                     "「winget install Gyan.FFmpeg」でインストールしてください")


def frame_at(path: Path, t: float, ffmpeg_path: str | None = None) -> Image.Image:
    """サムネイル・プレビュー用に 1 枚だけ取り出す。"""
    fr = extract(path, fps=1.0, start=t, end=t + 1.0, ffmpeg_path=ffmpeg_path, max_frames=1)
    if not fr:
        raise VideoError("フレームを取り出せませんでした")
    return fr[0]
