"""GBForge 配布版 (GBDK-2020 同梱のオールインワン) をビルドする。

    python packaging/build_app.py [--gbdk-archive gbdk-win64.zip] [--no-emulator]

dist/GBForge/ と dist/GBForge-<os>-<arch>.zip ができる。ビルドしたい OS 上で実行すること
(Windows 版は Windows で、macOS 版は macOS で)。GitHub Actions 用の設定は
.github/workflows/release.yml を参照。
"""
from __future__ import annotations

import argparse
import os
import importlib.util
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gbforge import __version__, toolchain  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gbdk-archive", help="ダウンロード済みの GBDK-2020 公式アーカイブ")
    ap.add_argument("--no-emulator", action="store_true", help="PyBoy を同梱しない (サイズ削減)")
    a = ap.parse_args()

    stage = ROOT / "build" / "app-stage"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    print("== GBDK-2020 を取得 ==")
    lcc = toolchain.install(archive=Path(a.gbdk_archive) if a.gbdk_archive else None,
                            target=stage / "gbdk-pkg")
    gbdk_dir = lcc.parent.parent
    # 配布に不要な大きいファイル (マニュアル PDF・サンプル) を省く
    for junk in ("examples", "gbdk_manual.pdf", "sdccman.pdf"):
        p = gbdk_dir / junk
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()

    import PyInstaller.__main__ as pyi
    args = [
        str(ROOT / "packaging" / "launcher.py"),
        "--name", "GBForge", "--onedir", "--noconfirm", "--clean", "--console",
        "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build" / "pyi"),
        "--specpath", str(ROOT / "build"),
        "--paths", str(ROOT),
        "--collect-submodules", "gbforge",
        "--collect-data", "gbforge",
        "--hidden-import", "serial.tools.list_ports",
        "--add-data", f"{gbdk_dir}{os.pathsep}gbdk",
        "--exclude-module", "cryptography", "--exclude-module", "yaml", "--exclude-module", "tkinter",
        "--exclude-module", "pytest", "--exclude-module", "PyInstaller",
    ]
    if not a.no_emulator and importlib.util.find_spec("pyboy"):
        args += ["--collect-all", "pyboy", "--collect-all", "sdl2", "--collect-all", "sdl2dll"]
    print("== PyInstaller ==")
    pyi.run(args)

    # Cython のソース等 (実行に不要) を削除してサイズを減らす
    internal = ROOT / "dist" / "GBForge" / "_internal"
    removed = 0
    for pat in ("*.c", "*.pxd", "*.pyx", "*.pyi"):
        for f in internal.rglob(pat):
            if f.relative_to(internal).parts[0] in ("gbforge", "gbdk"):
                continue   # エンジンの C ソースと GBDK は必要
            removed += f.stat().st_size
            f.unlink()
    print(f"不要ファイルを削除: {removed // (1 << 20)}MB")

    key = "-".join(toolchain.platform_key())
    out = ROOT / "dist" / f"GBForge-{__version__}-{key}"
    shutil.make_archive(str(out), "zip", ROOT / "dist", "GBForge")
    print(f"完成: {out}.zip")


if __name__ == "__main__":
    main()
