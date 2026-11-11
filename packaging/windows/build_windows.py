"""GBForge Windows 版 (インストーラー + ポータブル zip) を作る。

Linux / macOS / Windows のどこからでもクロスビルドできる:
    python packaging/windows/build_windows.py

必要なもの:
    - x86_64-w64-mingw32-gcc / windres (ランチャー GBForge.exe のビルド。Windows なら gcc でも可)
    - makensis (NSIS, インストーラーの作成)
    - インターネット (NuGet の公式 Windows 版 Python, PyPI の Windows 用 wheel, GBDK-2020 の公式リリース)

出力:
    dist/GBForge-<版>-Setup.exe            インストーラー (管理者権限不要)
    dist/GBForge-<版>-windows-x64.zip       ポータブル版 (展開して GBForge.exe を実行)
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from gbforge import __version__, toolchain  # noqa: E402

PY_VERSION = "3.12.10"
PY_NUGET = f"https://api.nuget.org/v3-flatcontainer/python/{PY_VERSION}/python.{PY_VERSION}.nupkg"
WHEELS = ["pillow", "pyserial", "pyboy"]
# 配布に不要な標準ライブラリ (サイズ削減)
STDLIB_DROP = ["test", "idlelib", "tkinter", "turtledemo", "ensurepip", "lib2to3", "pydoc_data", "unittest/test",
               "venv", "sqlite3", "site-packages/pip"]
DLL_DROP = ["sqlite3.dll", "_sqlite3.pyd", "_tkinter.pyd", "tcl86t.dll", "tk86t.dll"]


def run(cmd, **kw):
    print("$", " ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def fetch(url: str, dest: Path):
    if dest.exists():
        return dest
    print("ダウンロード:", url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "gbforge-build"}), timeout=120) as r:
        dest.write_bytes(r.read())
    return dest


def build_launcher(out: Path):
    gcc = shutil.which("x86_64-w64-mingw32-gcc") or (shutil.which("gcc") if sys.platform == "win32" else None)
    windres = shutil.which("x86_64-w64-mingw32-windres") or (shutil.which("windres") if sys.platform == "win32" else None)
    if not gcc or not windres:
        raise SystemExit("MinGW-w64 (x86_64-w64-mingw32-gcc / windres) が必要です")
    res = out.parent / "launcher.res"
    run([windres, HERE / "launcher.rc", "-O", "coff", "-o", res], cwd=HERE)
    run([gcc, "-O2", "-municode", "-finput-charset=UTF-8", "-static", "-o", out, HERE / "launcher.c", res])
    res.unlink()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--python-nupkg", help="ダウンロード済みの python.<版>.nupkg")
    ap.add_argument("--gbdk-archive", help="ダウンロード済みの gbdk-win64.zip")
    ap.add_argument("--wheels", help="ダウンロード済みの Windows 用 wheel のフォルダ")
    ap.add_argument("--no-installer", action="store_true", help="NSIS を使わずポータブル zip だけ作る")
    ap.add_argument("--no-emulator", action="store_true", help="PyBoy を同梱しない軽量版 (約 1/2 のサイズ)")
    a = ap.parse_args()

    work = ROOT / "build" / "windows"
    stage = work / "GBForge"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    cache = work / "cache"

    # 1. Python (NuGet の公式ビルド)
    print("== Python", PY_VERSION, "(Windows x64) ==")
    nupkg = Path(a.python_nupkg) if a.python_nupkg else fetch(PY_NUGET, cache / f"python.{PY_VERSION}.nupkg")
    with zipfile.ZipFile(nupkg) as z:
        for info in z.infolist():
            if info.filename.startswith("tools/") and not info.is_dir():
                rel = info.filename[len("tools/"):]
                if rel.split("/")[0] in ("include", "libs"):
                    continue
                dst = stage / "python" / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(z.read(info))
    lib = stage / "python" / "Lib"
    for d in STDLIB_DROP:
        shutil.rmtree(lib / d, ignore_errors=True)
    for d in lib.glob("site-packages/pip-*.dist-info"):
        shutil.rmtree(d, ignore_errors=True)
    for f in DLL_DROP:
        (stage / "python" / "DLLs" / f).unlink(missing_ok=True)
    site = lib / "site-packages"
    site.mkdir(exist_ok=True)

    # 2. 依存ライブラリ (Windows 用 wheel を展開)
    print("== Windows 用ライブラリ ==")
    wheels = Path(a.wheels) if a.wheels else cache / "wheels"
    if not a.wheels:
        wheels.mkdir(parents=True, exist_ok=True)
        run([sys.executable, "-m", "pip", "download", "--only-binary=:all:", "--platform", "win_amd64",
             "--python-version", PY_VERSION.rsplit(".", 1)[0], "--implementation", "cp", "-d", wheels, *WHEELS])
    for whl in sorted(wheels.glob("*.whl")):
        if a.no_emulator and re.match(r"(pyboy|numpy|pysdl2|PySDL2)", whl.name, re.I):
            continue
        with zipfile.ZipFile(whl) as z:
            z.extractall(site)
    for junk in list(site.rglob("*.pyx")) + list(site.rglob("*.pxd")) + list(site.rglob("*.c")):
        junk.unlink()

    # 3. GBForge 本体
    shutil.copytree(ROOT / "gbforge", site / "gbforge", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    # 4. GBDK-2020 (Windows 版, チェックサム検証)
    print("== GBDK-2020 (Windows) ==")
    lcc = toolchain.install(archive=Path(a.gbdk_archive) if a.gbdk_archive else None,
                            target=work / "gbdk-pkg", system="Windows", machine="AMD64")
    gbdk = lcc.parent.parent
    for junk in ("examples", "gbdk_manual.pdf", "sdccman.pdf"):
        p = gbdk / junk
        shutil.rmtree(p) if p.is_dir() else p.unlink(missing_ok=True)
    shutil.copytree(gbdk, stage / "gbdk")

    # 5. ランチャー・付属ファイル
    print("== GBForge.exe ==")
    build_launcher(stage / "GBForge.exe")
    (stage / "gbforge-shell.cmd").write_text(
        "@echo off\r\nchcp 65001 > nul\r\nset \"PATH=%~dp0;%PATH%\"\r\n"
        "echo GBForge コマンドライン  (例: GBForge new MyGame / GBForge build MyGame / GBForge doctor)\r\n",
        encoding="utf-8")
    shutil.copy(HERE / "LICENSE.txt", stage / "LICENSE.txt")
    shutil.copy(HERE / "README.html", stage / "README.html")
    lic = stage / "licenses"
    lic.mkdir()
    shutil.copy(stage / "python" / "LICENSE.txt", lic / "Python-LICENSE.txt")
    shutil.copy(ROOT / "gbforge" / "fonts" / "MISAKI_LICENSE.txt", lic / "MisakiFont-LICENSE.txt")

    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    # 6. ポータブル zip
    lite = "-lite" if a.no_emulator else ""
    portable = dist / f"GBForge-{__version__}-windows-x64{lite}"
    shutil.make_archive(str(portable), "zip", work, "GBForge")
    print("完成:", f"{portable}.zip")

    # 7. インストーラー
    if not a.no_installer:
        makensis = shutil.which("makensis")
        if not makensis:
            raise SystemExit("makensis (NSIS) が必要です。ポータブル版だけなら --no-installer")
        setup = dist / f"GBForge-{__version__}{lite}-Setup.exe"
        run([makensis, "-V2", f"-DVERSION={__version__}", f"-DSRCDIR={stage}", f"-DOUTFILE={setup}",
             HERE / "installer.nsi"], cwd=HERE)
        print("完成:", setup)


if __name__ == "__main__":
    main()
