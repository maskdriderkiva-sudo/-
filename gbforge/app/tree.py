"""左の「プロジェクト」ツリー (ブラウザ版と同じ項目)。"""
from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont, QIcon
from PySide6.QtWidgets import QFileDialog, QInputDialog, QTreeWidgetItem

from .core import COLORS, run_async
from .local import ApiFail, q

KIND_ICON = {"tiles": "▦", "palette": "🎨", "map": "🗺", "data": "#"}
TARGET = {"dmg": "初代", "both": "初代+カラー", "cgb": "カラー専用"}


def _section(tree, title: str, add=None, add_label: str = "＋ 追加…"):
    it = QTreeWidgetItem([title])
    f = it.font(0)
    f.setBold(True)
    it.setFont(0, f)
    it.setForeground(0, QBrush(QColor(COLORS["muted"])))
    it.setFlags(it.flags() & ~Qt.ItemIsSelectable)
    tree.addTopLevelItem(it)
    if add:
        it.setData(0, Qt.UserRole + 1, [(add_label, add)])
    return it


def _item(parent, text: str, data, tip: str = "", badge: str = "", icon: str | None = None, muted: bool = False):
    it = QTreeWidgetItem([text + (f"   {badge}" if badge else "")])
    it.setData(0, Qt.UserRole, data)
    if tip:
        it.setToolTip(0, tip)
    if icon and Path(icon).exists():
        it.setIcon(0, QIcon(icon))
    if muted:
        it.setForeground(0, QBrush(QColor(COLORS["muted"])))
    parent.addChild(it)
    return it


def _adder(parent, text: str, fn):
    it = _item(parent, text, fn, muted=True)
    f = it.font(0)
    f.setItalic(True)
    it.setFont(0, f)
    return it


def build_tree(win, tree):
    p = win.proj
    root = Path(p["root"])
    if p["type"] == "c":
        s = _section(tree, f"📄 ソース (C) — {TARGET.get(p['target'], p['target'])}", lambda: new_source(win))
        for f in p["sources"]:
            _item(s, f, ("file", f), "ダブルクリックで開く", muted=f.startswith("lib/"))
        _adder(s, "＋ 新しいファイル…", lambda: new_source(win))
        s = _section(tree, "🗺 ステージ (シーン)", lambda: new_stage(win))
        for n in p.get("stages", []):
            _item(s, n, ("stage", n))
        _adder(s, "＋ 新しいステージ…", lambda: new_stage(win))
        s = _section(tree, "🎨 アセット (画像)")
        for f in p.get("images", [])[:300]:
            _item(s, f, ("image", f), f, icon=str(root / f))
        if not p.get("images"):
            _item(s, "(PNG なし)", None, muted=True)
        s = _section(tree, "🧩 C データ (配列)")
        arrs = win.extra.get("arrays")
        if arrs is None:
            _item(s, "読み込み中…", None, muted=True)
            run_async(win.api.get, "/api/carrays", on_done=lambda r: (win.extra.__setitem__("arrays", r), win.render_tree()),
                      on_error=lambda e: win.extra.__setitem__("arrays", []))
        else:
            shown = [a for a in arrs if (a["kind"] != "data" or a["count"] >= 8) and not re.search(r"(^|/)gbmedia[^/]*\.c$", a["file"])]
            for a in shown:
                cnt = f"{a['count'] // 16}枚" if a["kind"] == "tiles" else str(a["count"])
                _item(s, f"{KIND_ICON.get(a['kind'], '#')} {a['name']}", ("carray", f"{a['file']}#{a['name']}"),
                      f"{a['file']}:{a.get('line', '')}", f"{a['file']} · {cnt}")
            if not shown:
                _item(s, "(配列なし)", None, muted=True)
        s = _section(tree, "🧰 ツール")
        _item(s, "画像 → スプライト / タイル / マップ 変換", ("convert", "convert"))
        _item(s, "バージョン管理", ("versions", "versions"))
        if p.get("res") or not p.get("adopted"):
            s = _section(tree, "🖼 リソース (res)", lambda: upload(win, None))
            for f in p.get("res", []):
                _item(s, f.replace("res/", "", 1), ("res", f), f, icon=str(root / f))
            _adder(s, "⤒ PNG を追加…", lambda: upload(win, None))
    else:
        s = _section(tree, "🎬 シーン", lambda: new_scene(win))
        start = p["settings"].get("start_scene") or (p["scenes"][0] if p["scenes"] else None)
        for n in p["scenes"]:
            _item(s, n, ("scene", n), badge="開始" if n == start else "")
        _adder(s, "＋ 新しいシーン…", lambda: new_scene(win))
        s = _section(tree, "🧍 スプライト", lambda: upload(win, "sprite"))
        for sp in p["sprites"]:
            it = _item(s, sp["name"], ("sprite", sp["name"]), sp.get("error") or "", f"{sp['frames']}f",
                       icon=str(root / "assets" / "sprites" / f"{sp['name']}.png"))
            if sp.get("error"):
                it.setForeground(0, QBrush(QColor(COLORS["err"])))
        _adder(s, "⤒ PNG を追加…", lambda: upload(win, "sprite"))
        s = _section(tree, "🖼 背景", lambda: upload(win, "background"))
        for b in p["backgrounds"]:
            _item(s, b["file"], ("background", b["file"]), badge=f"{b['width']}×{b['height']}")
        _adder(s, "⤒ PNG を追加…", lambda: upload(win, "background"))
        s = _section(tree, "📜 スクリプト (C)", lambda: new_script(win))
        for n in p["scripts"]:
            _item(s, n + ".c", ("script", n))
        _adder(s, "＋ 新しいスクリプト…", lambda: new_script(win))
        s = _section(tree, "💬 テキスト")
        _item(s, f"テキスト一覧 ({len(p['texts'])})", ("texts", "texts"))
    media(win, tree)
    s = _section(tree, "⚙ 設定")
    _item(s, "プロジェクト設定", ("settings", "settings"))


def media(win, tree):
    s = _section(tree, "🎞 メディア (写真・動画)", lambda: new_media(win))
    lst = win.extra.get("media")
    if lst is None:
        try:
            lst = win.api.get("/api/media/list")
        except (ApiFail, ImportError):
            lst = []
        win.extra["media"] = lst
    for m in lst:
        _item(s, f"🎞 {m['name']}", ("media", m["path"]), m.get("error") or m["path"],
              f"{'カラー' if m.get('target') == 'cgb' else '初代'} · {m.get('scenes', 0)}シーン")
    _adder(s, "＋ 新しいメディアプロジェクト…", lambda: new_media(win))


# ---------------------------------------------------------------------------
# 追加
# ---------------------------------------------------------------------------
def _ask(win, title: str, label: str, default: str = "") -> str | None:
    t, ok = QInputDialog.getText(win, title, label, text=default)
    return t.strip() if ok and t.strip() else None


def _try(win, fn):
    try:
        return fn()
    except ApiFail as e:
        win.log(f"エラー: {e}", "e")
        return None


def new_scene(win):
    name = _ask(win, "新しいシーン", "シーンの名前 (半角英数字)", f"scene{len(win.proj['scenes']) + 1}")
    if name and _try(win, lambda: win.api.post("/api/scene/" + q(name), {})) is not None:
        win.load_project()
        win.open_tab("scene", name)


def new_source(win):
    name = _ask(win, "新しいファイル", "src/ からの相対パス (.c か .h)", "src/game.c")
    if name and _try(win, lambda: win.api.call("POST", "/api/file?path=" + q(name))) is not None:
        win.load_project()
        win.open_tab("file", name)


def new_script(win):
    name = _ask(win, "新しいスクリプト", "スクリプトの名前 (例: Coin)", "NewScript")
    if name and _try(win, lambda: win.api.post("/api/script/" + q(name))) is not None:
        win.load_project()
        win.open_tab("script", name)


def new_media(win):
    name = _ask(win, "新しいメディアプロジェクト", "名前 (例: opening)", "opening")
    if not name:
        return
    r = _try(win, lambda: win.api.post("/api/media/new", {"name": name}))
    if r:
        win.extra.pop("media", None)
        win.render_tree()
        win.open_tab("media", r["path"])


def new_stage(win):
    from .views.stage import new_stage_dialog
    new_stage_dialog(win)


def upload(win, kind: str | None):
    f, _ = QFileDialog.getOpenFileName(win, "PNG 画像を選ぶ", "", "PNG 画像 (*.png)")
    if not f:
        return
    if kind is None:
        from PySide6.QtWidgets import QMessageBox
        r = QMessageBox.question(win, "リソースの種類", "スプライトとして追加しますか？\n(いいえ = 背景マップ)")
        kind = "res_sprite" if r == QMessageBox.Yes else "res_background"
    name = re.sub(r"[^A-Za-z0-9_]", "_", Path(f).stem)
    if not re.match(r"[A-Za-z_]", name):
        name = "img_" + name
    name = _ask(win, "アセット名", "アセット名 (半角英数字)", name)
    if not name:
        return
    r = _try(win, lambda: win.api.call("POST", f"/api/upload?kind={kind}&name={q(name)}.png", raw=Path(f).read_bytes()))
    if r is None:
        return
    win.log(f"{name}.png を追加しました", "o")
    if r.get("warning"):
        win.log("警告: " + r["warning"], "w")
    win.load_project()
    for v in win.views():
        if v.kind == "scene":
            v.load()
