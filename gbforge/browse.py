"""PC 内の GBDK プロジェクトを探す / フォルダを選ぶ (エディタのスタート画面用)。

- GBDK の Project フォルダ (例: C:\\gbdk-win64\\gbdk\\Project) を自動で見つけ、中のプロジェクトを
  名前ごと (HighwayStar_GBC_v0.3 … v2.0) にまとめて一覧にする
- zip を展開して HighwayStar_GBC_v2.0\\HighwayStar_GBC_v2.0 のように二重になったフォルダも、
  中の本当のプロジェクトフォルダを自動で見つける
- フォルダブラウザ (ドライブ → フォルダ) で GBDK プロジェクトらしいフォルダに印を付ける
"""
from __future__ import annotations

import json
import os
import re
import string
from pathlib import Path

from .toolchain import gbforge_home
from .versions import parse

BUILD_FILES = ("build.bat", "build.cmd", "build.sh", "Makefile", "makefile", "project.gbproj")
SKIP = {".git", ".vs", ".vscode", "node_modules", "__pycache__", "$RECYCLE.BIN", "System Volume Information"}


def _settings_file() -> Path:
    return gbforge_home() / "settings.json"


def load_user_settings() -> dict:
    try:
        return json.loads(_settings_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_user_settings(s: dict):
    _settings_file().parent.mkdir(parents=True, exist_ok=True)
    _settings_file().write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")


def _safe_iterdir(d: Path) -> list[Path]:
    try:
        return [p for p in d.iterdir() if p.name not in SKIP and not p.name.startswith(".")]
    except (OSError, PermissionError):
        return []


def looks_like_project(d: Path) -> bool:
    """C のソースとビルド方法 (build.bat など) か main.c がある。"""
    try:
        names = {p.name for p in d.iterdir()}
    except (OSError, PermissionError):
        return False
    if "project.gbproj" in names:
        return True
    has_c = any(n.endswith(".c") for n in names)
    return has_c and (any(b in names for b in BUILD_FILES) or "main.c" in names)


def resolve_project_dir(d: Path, depth: int = 2) -> Path | None:
    """d かその中 (zip 展開で二重になったフォルダなど) の本当のプロジェクトフォルダ。"""
    d = Path(d)
    if looks_like_project(d):
        return d
    if depth <= 0:
        return None
    subs = [p for p in _safe_iterdir(d) if p.is_dir()]
    same = [p for p in subs if p.name == d.name]          # HighwayStar_GBC_v2.0\HighwayStar_GBC_v2.0
    for p in same + (subs if len(subs) <= 3 else []):
        r = resolve_project_dir(p, depth - 1)
        if r:
            return r
    return None


# ---------------------------------------------------------------------------
# プロジェクトフォルダの自動検出
# ---------------------------------------------------------------------------
def candidate_roots() -> list[Path]:
    """GBDK のプロジェクトが置かれていそうな場所。"""
    cands: list[Path] = []
    for r in load_user_settings().get("project_roots", []):
        cands.append(Path(r))
    gbdk_dirs = []
    from .toolchain import find
    lcc, _ = find(None)
    if lcc:
        gbdk_dirs.append(lcc.parent.parent)
    if os.environ.get("GBDK_HOME"):
        gbdk_dirs.append(Path(os.environ["GBDK_HOME"]))
    if os.name == "nt":
        for drive in _drives():
            root = Path(drive)
            for p in _safe_iterdir(root):
                if p.is_dir() and re.match(r"gbdk", p.name, re.I):
                    gbdk_dirs += [p, p / "gbdk"]
    home = Path.home()
    gbdk_dirs += [home / "gbdk", home / "gbdk-2020", home / "Documents" / "gbdk"]
    for g in gbdk_dirs:
        for sub in ("Project", "Projects", "project", "projects", "work", "src"):
            cands.append(g / sub)
    cands += [home / "GBForge", home / "Documents" / "GBForge"]
    # 最近開いたプロジェクトの親フォルダ
    try:
        recent = json.loads((gbforge_home() / "recent.json").read_text(encoding="utf-8"))
        for r in recent:
            p = Path(r)
            cands.append(p.parent.parent if p.parent.name == p.name else p.parent)
    except (OSError, ValueError):
        pass
    out, seen = [], set()
    for c in cands:
        try:
            c = c.resolve()
        except OSError:
            continue
        if c.is_dir() and str(c).lower() not in seen:
            seen.add(str(c).lower())
            out.append(c)
    return out


def _version_key(name: str):
    base, ver, suffix = parse(name)
    return (ver or (0,), suffix)


def scan_projects(roots: list[Path] | None = None, limit: int = 400) -> list[dict]:
    """プロジェクトを名前ごとにまとめる: [{root, groups: [{name, versions: [...]}]}]"""
    result = []
    total = 0
    for root in roots or candidate_roots():
        groups: dict[str, list[dict]] = {}
        for d in sorted(_safe_iterdir(root), key=lambda p: p.name.lower()):
            if not d.is_dir() or total >= limit:
                continue
            proj = resolve_project_dir(d)
            if not proj:
                continue
            total += 1
            base, ver, suffix = parse(d.name)
            try:
                mtime = max(proj.stat().st_mtime, d.stat().st_mtime)
            except OSError:
                mtime = 0
            groups.setdefault(base, []).append({
                "folder": d.name, "path": str(proj), "version": ".".join(map(str, ver)) if ver else "",
                "suffix": suffix.strip("_"), "nested": proj != d, "mtime": mtime,
                "is_project": (proj / "project.gbproj").exists(),
            })
        if not groups:
            continue
        gl = []
        for name, items in groups.items():
            items.sort(key=lambda it: it["mtime"])
            gl.append({"name": name, "versions": items, "latest": max(items, key=lambda it: it["mtime"]),
                       "mtime": max(it["mtime"] for it in items)})
        gl.sort(key=lambda g: -g["mtime"])
        result.append({"root": str(root), "groups": gl})
    return result


# ---------------------------------------------------------------------------
# フォルダブラウザ
# ---------------------------------------------------------------------------
def _drives() -> list[str]:
    if os.name != "nt":
        return ["/"]
    try:
        return list(os.listdrives())          # Python 3.12+
    except AttributeError:
        return [f"{c}:\\" for c in string.ascii_uppercase if os.path.exists(f"{c}:\\")]


def roots() -> dict:
    home = Path.home()
    places = [{"name": "ホーム", "path": str(home)}]
    for n in ("Desktop", "Documents", "Downloads"):
        if (home / n).is_dir():
            places.append({"name": {"Desktop": "デスクトップ", "Documents": "ドキュメント", "Downloads": "ダウンロード"}[n],
                           "path": str(home / n)})
    for r in candidate_roots()[:6]:
        places.append({"name": r.name if r.name else str(r), "path": str(r)})
    return {"drives": _drives(), "places": places}


def list_dir(path: str) -> dict:
    p = Path(path).expanduser()
    if not p.is_dir():
        raise FileNotFoundError(f"フォルダがありません: {p}")
    entries = []
    for d in sorted(_safe_iterdir(p), key=lambda x: x.name.lower()):
        if not d.is_dir():
            continue
        proj = None
        try:
            proj = resolve_project_dir(d, depth=1)
        except OSError:
            pass
        entries.append({"name": d.name, "path": str(d), "project": str(proj) if proj else None,
                        "nested": bool(proj) and proj != d})
    parent = str(p.parent) if p.parent != p else None
    here = resolve_project_dir(p, depth=0)
    return {"path": str(p), "parent": parent, "entries": entries[:1000], "is_project": bool(here)}
