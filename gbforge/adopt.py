"""既存の GBDK プロジェクトフォルダを GBForge で開く (取り込み)。

フォルダの中身は動かさず、project.gbproj を 1 つ追加するだけ。
- ビルドは今までの build.bat (Windows) / build.sh / Makefile をそのまま使う
- GBDK は親フォルダをさかのぼって探す (例: C:\\gbdk-win64\\gbdk\\Project\\X → C:\\gbdk-win64\\gbdk)
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .project import DEFAULT_SETTINGS, PROJECT_FILE, Project

SKIP_DIRS = {".git", ".vs", ".vscode", "build", "obj", "__pycache__", ".gbforge", "node_modules"}


def find_gbdk_upwards(folder: Path) -> Path | None:
    """フォルダから親へさかのぼって bin/lcc(.exe) を持つ GBDK を探す。"""
    for d in [folder, *folder.parents][:6]:
        for cand in (d, d / "gbdk"):
            if (cand / "bin" / "lcc.exe").exists() or (cand / "bin" / "lcc").exists():
                return cand
    return None


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def inspect(folder: Path) -> dict:
    """フォルダを調べて、取り込み時の設定案を返す。"""
    folder = Path(folder).expanduser().resolve()
    if not folder.is_dir():
        raise FileNotFoundError(f"フォルダがありません: {folder}")
    c_files = [f for f in folder.rglob("*.c") if not (SKIP_DIRS & set(f.relative_to(folder).parts))]
    h_files = [f for f in folder.rglob("*.h") if not (SKIP_DIRS & set(f.relative_to(folder).parts))]
    pngs = [f for f in folder.rglob("*.png") if not (SKIP_DIRS & set(f.relative_to(folder).parts))]
    roms = sorted(folder.glob("*.gb")) + sorted(folder.glob("*.gbc"))
    scripts = {n: folder / n for n in ("build.bat", "build.cmd", "build.sh", "Makefile", "makefile") if (folder / n).exists()}

    script_text = "".join(_read(p) for p in scripts.values())
    out = None
    m = re.search(r"-o\s+\"?([\w\-. \\/]+?\.gbc?)\"?(?:\s|$)", script_text)
    if m:
        out = Path(m.group(1).replace("\\", "/")).name
    if not out and roms:
        out = max(roms, key=lambda r: r.stat().st_mtime).name
    target = "cgb" if re.search(r"-Wm-yC|-yC\b", script_text) else "both" if re.search(r"-Wm-yc|-yc\b", script_text) else "dmg"
    if not re.search(r"-y[cC]", script_text) and out and (folder / out).exists():
        flag = (folder / out).read_bytes()[0x143:0x144]
        target = {b"\xC0": "cgb", b"\x80": "both"}.get(flag, target)

    win = os.name == "nt"
    script = None
    if win and ("build.bat" in scripts or "build.cmd" in scripts):
        script = "build.bat" if "build.bat" in scripts else "build.cmd"
    elif not win and "build.sh" in scripts:
        script = "build.sh"
    elif "Makefile" in scripts or "makefile" in scripts:
        script = "make"
    elif "build.bat" in scripts:
        script = "build.bat"
    gbdk = find_gbdk_upwards(folder)
    name = folder.name
    return {
        "path": str(folder), "name": name, "already_project": (folder / PROJECT_FILE).exists(),
        "c_files": len(c_files), "h_files": len(h_files), "png_files": len(pngs),
        "scripts": sorted(scripts), "build_script": script, "build_script_unix": "build.sh" if "build.sh" in scripts else None,
        "build_script_windows": next((s for s in ("build.bat", "build.cmd") if s in scripts), None),
        "output": out, "roms": [r.name for r in roms], "target": target,
        "gbdk": str(gbdk) if gbdk else None,
        "has_main": any(f.name == "main.c" for f in c_files),
    }


def adopt(folder: Path, info: dict | None = None, overrides: dict | None = None) -> Project:
    """project.gbproj を作って既存フォルダを GBForge プロジェクトにする (ソースは変更しない)。"""
    info = info or inspect(folder)
    folder = Path(info["path"])
    if (folder / PROJECT_FILE).exists():
        return Project(folder)
    s = json.loads(json.dumps(DEFAULT_SETTINGS))
    title = re.sub(r"[^A-Z0-9 ]", "", info["name"].upper().replace("_", " "))[:11].strip() or "GAME"
    s.update({
        "type": "c", "layout": "flat", "name": info["name"], "rom_title": title, "target": info["target"],
        "adopted": True,
    })
    s["build"].update({
        "mode": "script" if (info.get("build_script_windows") or info.get("build_script_unix") or info.get("build_script") == "make") else "gbforge",
        "script_windows": info.get("build_script_windows") or "",
        "script_unix": info.get("build_script_unix") or ("make" if info.get("build_script") == "make" else ""),
        "output": info.get("output") or "",
        "gbdk_home": info.get("gbdk") or "",
    })
    s["flasher"]["mbc"] = "ROM"        # ROM ヘッダーのカートリッジ種別に合わせる
    s["jpfont"]["mode"] = "scan"
    s["object_types"] = [
        {"name": "enemy", "color": "#e5484d", "fields": ["speed", "kind"]},
        {"name": "item", "color": "#f5d90a", "fields": ["value"]},
        {"name": "start", "color": "#30a46c", "fields": []},
    ]
    s.update(overrides or {})
    (folder / PROJECT_FILE).write_text(json.dumps(s, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return Project(folder)


# ---------------------------------------------------------------------------
# ビルドスクリプト (build.bat / build.sh) へのソース追加
# ---------------------------------------------------------------------------
def _scripts(p: Project) -> list[Path]:
    b = p.settings.get("build", {})
    out = []
    for key in ("script_windows", "script_unix"):
        s = b.get(key)
        if s and s != "make" and (p.root / s).exists():
            out.append(p.root / s)
    return out


def script_has(p: Project, filename: str) -> bool:
    return any(re.search(r"(?<![\w.])" + re.escape(filename) + r"(?![\w.])", _read(s)) for s in _scripts(p))


def add_to_build_scripts(p: Project, files: list[str]) -> list[str]:
    """lcc でリンクしている行 (-o *.gb がある行) の末尾に .c ファイルを追加する。変更したスクリプト名を返す。"""
    changed = []
    for s in _scripts(p):
        raw = s.read_bytes()
        text = raw.decode("latin-1")      # 1 バイト = 1 文字で読む (Shift_JIS のコメントも壊さない)
        crlf = "\r\n" in text
        lines = text.replace("\r\n", "\n").split("\n")
        hit = False
        for i, line in enumerate(lines):
            if "lcc" in line and re.search(r"-o\s+\S+\.gbc?", line):
                for f in files:
                    if not re.search(r"(?<![\w.])" + re.escape(f) + r"(?![\w.])", lines[i]):
                        lines[i] = lines[i].rstrip() + " " + f
                        hit = True
        if hit:
            s.with_suffix(s.suffix + ".bak").write_bytes(raw)
            s.write_bytes(("\r\n" if crlf else "\n").join(lines).encode("latin-1"))
            changed.append(s.name)
    return changed
