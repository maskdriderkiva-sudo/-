"""GBDK-2020 の検出・自動インストール。

探す順番:
  1. プロジェクト設定 build.gbdk_home
  2. 環境変数 GBDK_HOME
  3. アプリに同梱された GBDK (配布版 GBForge)
  4. gbforge setup でインストールしたもの (~/.gbforge/gbdk/<版>/gbdk)
  5. PATH 上の lcc
"""
from __future__ import annotations

import hashlib
import os
import platform
import shutil
import stat
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

GBDK_VERSION = "4.3.0"
RELEASE_URL = "https://github.com/gbdk-2020/gbdk-2020/releases/download/{ver}/{file}"

# 公式リリースの SHA-256 (改ざん・破損チェック用)
ASSETS = {
    ("windows", "x86_64"): ("gbdk-win64.zip", "e95b63f9ad86dfd275c3fc7d96678b4934e75c8ecccc36d3e3be84f5d59b50de"),
    ("windows", "x86"): ("gbdk-win32.zip", "17530b88ceda10ed8159f791831381639676efe2a3183fb0bd024e6259ffd207"),
    ("darwin", "x86_64"): ("gbdk-macos.tar.gz", "c796089c03f64153cf67c49568416ce05ac1865d2eac59ae4c58445b974eb619"),
    ("darwin", "arm64"): ("gbdk-macos-arm64.tar.gz", "d8300880ef65e291b226db6c82e116f669aff294f1bea506c41729e8cc86a1f6"),
    ("linux", "x86_64"): ("gbdk-linux64.tar.gz", "5032e4e4edcef3f0ed232c895bbd211973963329304ff2c030ea34d0b2d964a4"),
}

Log = Callable[[str], None]
Progress = Callable[[int, int, str], None]


class ToolchainError(Exception):
    pass


def gbforge_home() -> Path:
    return Path(os.environ.get("GBFORGE_HOME") or (Path.home() / ".gbforge"))


def platform_key(system: str | None = None, machine: str | None = None) -> tuple[str, str]:
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    if machine in ("amd64", "x64", "x86_64"):
        machine = "x86_64"
    elif machine in ("aarch64", "arm64"):
        machine = "arm64"
    elif machine in ("i386", "i686", "x86"):
        machine = "x86"
    if system.startswith("win"):
        system = "windows"
    return system, machine


def asset_for(system: str | None = None, machine: str | None = None) -> tuple[str, str]:
    key = platform_key(system, machine)
    if key not in ASSETS:
        raise ToolchainError(f"この環境 ({key[0]} / {key[1]}) 向けの GBDK-2020 公式ビルドはありません。"
                             "手動でビルドして GBDK_HOME を設定してください")
    return ASSETS[key]


def _lcc_in(home: Path) -> Path | None:
    for exe in ("lcc.exe", "lcc"):
        p = home / "bin" / exe
        if p.is_file():
            return p
    return None


def bundled_dirs() -> list[Path]:
    """配布版 (PyInstaller) やパッケージに同梱された GBDK の候補。"""
    out = []
    if os.environ.get("GBFORGE_APP_DIR"):          # Windows 版ランチャー (GBForge.exe)
        out.append(Path(os.environ["GBFORGE_APP_DIR"]) / "gbdk")
    if getattr(sys, "frozen", False):
        out.append(Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "gbdk")
        out.append(Path(sys.executable).resolve().parent / "gbdk")
    out.append(Path(__file__).resolve().parent / "vendor" / "gbdk")
    return out


def installed_dir(version: str = GBDK_VERSION) -> Path:
    return gbforge_home() / "gbdk" / version / "gbdk"


def find(gbdk_home: str | None = None) -> tuple[Path | None, str]:
    """(lcc のパス, 見つかった場所の説明) を返す。"""
    cands: list[tuple[Path, str]] = []
    if gbdk_home:
        cands.append((Path(gbdk_home).expanduser(), "プロジェクト設定"))
    if os.environ.get("GBDK_HOME"):
        cands.append((Path(os.environ["GBDK_HOME"]).expanduser(), "環境変数 GBDK_HOME"))
    for d in bundled_dirs():
        cands.append((d, "同梱"))
    cands.append((installed_dir(), "GBForge が管理"))
    lcc = shutil.which("lcc")
    if lcc:
        cands.append((Path(lcc).resolve().parent.parent, "PATH"))
    for home, src in cands:
        exe = _lcc_in(home)
        if exe:
            return exe, src
    return None, ""


def status(gbdk_home: str | None = None) -> dict:
    lcc, src = find(gbdk_home)
    return {
        "installed": bool(lcc), "lcc": str(lcc) if lcc else None, "home": str(lcc.parent.parent) if lcc else None,
        "source": src, "expected_version": GBDK_VERSION,
    }


# ---------------------------------------------------------------------------
# インストール
# ---------------------------------------------------------------------------
def _download(url: str, dest: Path, progress: Progress, log: Log):
    log(f"ダウンロード: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "gbforge-setup"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                progress(done, total, "GBDK ダウンロード")
    except OSError as e:
        raise ToolchainError(f"ダウンロードに失敗しました: {e}") from e


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_extract(archive: Path, dest: Path):
    dest = dest.resolve()

    def check(name: str):
        target = (dest / name).resolve()
        if dest != target and dest not in target.parents:
            raise ToolchainError(f"アーカイブに不正なパスがあります: {name}")

    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            for n in z.namelist():
                check(n)
            z.extractall(dest)
    else:
        with tarfile.open(archive, "r:gz") as t:
            members = []
            for m in t.getmembers():
                check(m.name)
                if m.issym() or m.islnk():
                    check(os.path.join(os.path.dirname(m.name), m.linkname))
                if m.isdev():
                    continue
                members.append(m)
            t.extractall(dest, members=members)


def install(archive: Path | None = None, progress: Progress | None = None, log: Log = print,
            target: Path | None = None, system: str | None = None, machine: str | None = None) -> Path:
    """GBDK-2020 をダウンロード (または指定アーカイブから) して展開し、lcc のパスを返す。"""
    progress = progress or (lambda d, t, m: None)
    fname, sha = asset_for(system, machine)
    target = Path(target) if target else installed_dir().parent      # .../gbdk/<版>/
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
        tmp = Path(tmp)
        arc = tmp / fname
        if archive:
            shutil.copy(archive, arc)
        else:
            _download(RELEASE_URL.format(ver=GBDK_VERSION, file=fname), arc, progress, log)
        digest = _sha256(arc)
        if digest != sha:
            raise ToolchainError(f"チェックサムが一致しません (期待 {sha[:12]}…, 実際 {digest[:12]}…)。"
                                 "ダウンロードが壊れている可能性があります")
        log("チェックサム OK。展開しています…")
        out = tmp / "out"
        out.mkdir()
        _safe_extract(arc, out)
        if not (out / "gbdk" / "bin").is_dir():
            raise ToolchainError("アーカイブの中に gbdk/bin がありません")
        if os.name != "nt":
            for d in ("bin", "libexec"):
                for f in (out / "gbdk" / d).rglob("*"):
                    if f.is_file():
                        f.chmod(f.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(out), str(target))
    lcc = _lcc_in(target / "gbdk")
    if not lcc:
        raise ToolchainError("インストール後に lcc が見つかりません")
    log(f"GBDK-2020 {GBDK_VERSION} をインストールしました: {target / 'gbdk'}")
    return lcc
