"""バージョン管理: 「HighwayStar_GBC_v1.9_HW」のようにフォルダごとに版を分ける運用を自動化する。

- 兄弟フォルダから同じプロジェクトの版を一覧
- 「新しいバージョンとして保存」: フォルダを丸ごとコピーして次の版のフォルダを作り、<名前>_source.zip も作る
"""
from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path

VER_RE = re.compile(r"^(?P<base>.*?)_v(?P<ver>\d+(?:\.\d+)*)(?P<suffix>.*)$", re.I)
COPY_IGNORE = shutil.ignore_patterns("build", ".vs", "__pycache__", "*.vcxproj.user")


def parse(name: str) -> tuple[str, tuple[int, ...] | None, str]:
    m = VER_RE.match(name)
    if not m:
        return name, None, ""
    return m.group("base"), tuple(int(x) for x in m.group("ver").split(".")), m.group("suffix")


def fmt(ver: tuple[int, ...]) -> str:
    return ".".join(str(v) for v in ver)


def outer_folder(folder: Path) -> Path:
    """X\\X (zip を展開して二重になったフォルダ) なら外側の X を返す。"""
    folder = Path(folder).resolve()
    return folder.parent if folder.parent.name == folder.name else folder


def siblings(folder: Path) -> list[dict]:
    """同じ名前 (_v 番号違い) のフォルダを古い順に。path は中の本当のプロジェクトフォルダ。"""
    from .browse import resolve_project_dir
    cur = Path(folder).resolve()
    folder = outer_folder(cur)
    base, _, _ = parse(folder.name)
    out = []
    for d in folder.parent.iterdir():
        if not d.is_dir():
            continue
        b, ver, suffix = parse(d.name)
        if b != base and d.name != base:
            continue
        proj = resolve_project_dir(d) or d
        out.append({"name": d.name, "path": str(proj), "version": fmt(ver) if ver else "", "suffix": suffix,
                    "current": d == folder, "mtime": d.stat().st_mtime, "nested": proj != d,
                    "is_project": (proj / "project.gbproj").exists()})
    out.sort(key=lambda x: x["mtime"])
    return out


def suggest_next(folder: Path) -> list[str]:
    """次のバージョン名の候補 (小さい更新 / 大きい更新)。"""
    folder = outer_folder(folder)
    base, ver, suffix = parse(folder.name)
    existing = {s["name"] for s in siblings(folder)}
    allv = [parse(s["name"])[1] for s in siblings(folder) if parse(s["name"])[1]]
    top = max(allv) if allv else (0, 0)
    cands = []
    if ver is None:
        cands = [f"{folder.name}_v0.2", f"{folder.name}_v1.0"]
    else:
        minor = list(top) + ([0] if len(top) == 1 else [])
        minor[-1] += 1
        major = [top[0] + 1] + [0] * (len(minor) - 1)
        cands = [f"{base}_v{fmt(tuple(minor))}", f"{base}_v{fmt(tuple(major))}"]
        if len(top) >= 2 and top[-1] == 9:      # v1.9 の次は v2.0 を先に
            cands.reverse()
        if suffix:
            cands.insert(1, f"{base}_v{fmt(tuple(minor))}{suffix}")
    return [c for c in cands if c not in existing]


def save_new_version(folder: Path, new_name: str, make_zip: bool = True) -> dict:
    folder = Path(folder).resolve()           # 中の本当のプロジェクトフォルダ (これをコピー)
    outer = outer_folder(folder)               # 兄弟の版が並んでいる場所はこの親
    if not re.fullmatch(r"[\w.\-]+", new_name):
        raise ValueError("フォルダ名に使えない文字があります")
    dest = outer.parent / new_name
    if dest.exists():
        raise FileExistsError(f"{dest} は既にあります")
    shutil.copytree(folder, dest, ignore=COPY_IGNORE)
    result = {"path": str(dest), "zip": None}
    if make_zip:
        # 今までの zip の付け方に合わせる (X.zip か X_source.zip)
        style_plain = (outer.parent / f"{outer.name}.zip").exists()
        z = outer.parent / (f"{new_name}.zip" if style_plain else f"{new_name}_source.zip")
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(dest.rglob("*")):
                rel = f.relative_to(dest.parent)
                if f.is_file() and not f.suffix.lower() in (".sav",):
                    zf.write(f, rel.as_posix())
        result["zip"] = str(z)
    # 新しいフォルダの project.gbproj の名前を更新
    pj = dest / "project.gbproj"
    if pj.exists():
        import json
        s = json.loads(pj.read_text(encoding="utf-8"))
        if s.get("adopted"):
            s["name"] = new_name
        pj.write_text(json.dumps(s, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
