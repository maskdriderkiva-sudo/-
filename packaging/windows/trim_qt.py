"""Windows 版に入れる PySide6 (Qt) を、GBForge が使う部分だけに減らす。

GBForge のネイティブアプリは QtCore / QtGui / QtWidgets だけを使う。
残すファイルは「入口 (QtCore.pyd など) と必要なプラグインから、DLL の import テーブルを辿って届くもの全部」。
どこかの DLL が必要とする DLL が見つからなければエラーにする (足りないまま配布しないため)。
"""
from __future__ import annotations

import shutil
from pathlib import Path

ENTRY = ["QtCore.pyd", "QtGui.pyd", "QtWidgets.pyd"]
PLUGINS = ["platforms/qwindows.dll", "styles/qmodernwindowsstyle.dll", "imageformats/qjpeg.dll", "imageformats/qgif.dll",
           "imageformats/qico.dll", "imageformats/qwebp.dll", "imageformats/qsvg.dll"]
KEEP_ALWAYS = ["__init__.py", "_config.py", "_git_pyside_version.py", "_built_modules.py", "qt.conf", "support"]
TRANSLATIONS = ["qtbase_ja.qm"]


def _imports(path: Path) -> list[str]:
    import pefile
    pe = pefile.PE(str(path), fast_load=True)
    pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                                           pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"]])
    names = []
    for attr in ("DIRECTORY_ENTRY_IMPORT", "DIRECTORY_ENTRY_DELAY_IMPORT"):
        for e in getattr(pe, attr, []):
            names.append(e.dll.decode("ascii", "replace"))
    pe.close()
    return names


def _system(name: str, python_dir: Path) -> bool:
    """Windows に最初からある DLL、または Python 本体が持っている DLL"""
    n = name.lower()
    if n.startswith(("api-ms-win-", "ext-ms-win-")):
        return True
    if (python_dir / name).exists() or any(p.name.lower() == n for p in python_dir.glob("*.dll")):
        return True
    return n in {
        "kernel32.dll", "user32.dll", "gdi32.dll", "advapi32.dll", "shell32.dll", "ole32.dll", "oleaut32.dll", "comdlg32.dll",
        "ws2_32.dll", "winmm.dll", "imm32.dll", "uxtheme.dll", "dwmapi.dll", "version.dll", "netapi32.dll", "userenv.dll",
        "shlwapi.dll", "mpr.dll", "crypt32.dll", "dwrite.dll", "d3d11.dll", "d3d12.dll", "dxgi.dll", "d2d1.dll",
        "d3d9.dll", "opengl32.dll", "setupapi.dll", "authz.dll", "comctl32.dll", "wtsapi32.dll", "rpcrt4.dll", "msvcrt.dll",
        "secur32.dll", "bcrypt.dll", "ncrypt.dll", "iphlpapi.dll", "dnsapi.dll", "dcomp.dll", "shcore.dll", "propsys.dll",
        "winspool.drv", "hid.dll", "cfgmgr32.dll", "dxcore.dll", "msimg32.dll", "usp10.dll", "windowscodecs.dll",
        "powrprof.dll", "wininet.dll", "dbghelp.dll", "ntdll.dll", "user32", "mscoree.dll", "d3dcompiler_47.dll",
        "mf.dll", "mfplat.dll", "mfreadwrite.dll", "evr.dll", "strmiids.dll", "dsound.dll", "ucrtbase.dll", "oleacc.dll",
        # Windows 10 (1703 以降) / 11 に最初からある: ICU (Qt 6 はこれを使う)、色管理、UI オートメーション
        "icuuc.dll", "icuin.dll", "icu.dll", "mscms.dll", "uiautomationcore.dll",
    }


def trim(site: Path, python_dir: Path, log=print) -> dict:
    pyside = site / "PySide6"
    shib = site / "shiboken6"
    search = [pyside, shib]
    by_name: dict[str, Path] = {}
    for d in search:
        for f in d.rglob("*"):
            if f.suffix.lower() in (".dll", ".pyd"):
                by_name.setdefault(f.name.lower(), f)

    keep: set[Path] = set()
    todo = [pyside / e for e in ENTRY] + [pyside / "plugins" / p for p in PLUGINS if (pyside / "plugins" / p).exists()] + \
        [f for f in shib.glob("*") if f.suffix.lower() in (".pyd", ".dll")]
    missing: dict[str, list[str]] = {}
    while todo:
        f = todo.pop()
        if f in keep:
            continue
        keep.add(f)
        for dep in _imports(f):
            p = by_name.get(dep.lower())
            if p is not None:
                todo.append(p)
            elif not _system(dep, python_dir):
                missing.setdefault(dep, []).append(f.name)
    if missing:
        raise SystemExit("Qt の DLL が足りません: " + ", ".join(f"{k} ← {v}" for k, v in missing.items()))

    before = sum(f.stat().st_size for f in pyside.rglob("*") if f.is_file())
    keep_rel = {k.relative_to(pyside).as_posix().lower() for k in keep if pyside in k.parents}
    for f in sorted(pyside.rglob("*"), reverse=True):
        rel = f.relative_to(pyside).as_posix()
        top = rel.split("/")[0]
        if f.is_dir():
            if not any(f.iterdir()):
                f.rmdir()
            continue
        if top in KEEP_ALWAYS:
            continue
        if top == "translations" and f.name in TRANSLATIONS:
            continue
        if rel.lower() in keep_rel:
            continue
        if f.suffix == ".py" and "/" not in rel:      # PySide6/*.py (小さいので残す)
            continue
        f.unlink()
    for d in sorted((p for p in pyside.rglob("*") if p.is_dir()), reverse=True):
        if not any(d.iterdir()):
            d.rmdir()
    for junk in ("include", "lib"):
        if (shib / junk).exists():
            shutil.rmtree(shib / junk)
    after = sum(f.stat().st_size for f in pyside.rglob("*") if f.is_file())
    log(f"Qt (PySide6): {before / 1e6:.0f} MB → {after / 1e6:.0f} MB  (残した DLL/pyd: {len(keep)})")
    return {"before": before, "after": after, "kept": sorted(k.name for k in keep)}
