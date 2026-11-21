"""ステージエディタ (C プロジェクト): タイルマップを塗り、オブジェクトを置く。保存すると C ソースに書き出す。"""
from __future__ import annotations

import copy
import json
import re

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QColorDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QScrollArea, QSplitter, QToolBar, QVBoxLayout, QWidget)

from .. import core
from ..core import ask, button, check, combo, form, group, hint, label, png_to_pixmap, spin
from ..local import ApiFail, q
from ..view import View, register
from .carray import arrays, key_of
from .convert import script_add_box

TOOLS = (("paint", "✏ タイル", "タイルを塗る (B)"), ("rect", "▭ 矩形", "矩形で塗る (R)"), ("pick", "💧 スポイト", "スポイト (I) / Alt+クリック / 右クリック"),
         ("attr", "🎨 パレット", "CGB パレットを塗る (P)"), ("object", "◆ オブジェクト", "オブジェクトを置く・動かす (O)"))


def new_stage_dialog(win):
    p = win.proj
    name, ok = QInputDialog.getText(win, "新しいステージ", "ステージ (シーン) の名前 (半角英数字)", text=f"stage{len(p.get('stages', [])) + 1}")
    name = name.strip()
    if not ok or not name:
        return
    arrs = arrays(win, True)
    maps = [a for a in arrs if a["kind"] == "map" and a["parsed"]]
    tiles = next((a for a in arrs if a["kind"] == "tiles" and a["parsed"]), None)
    pals = next((a for a in arrs if a["kind"] == "palette" and a["parsed"]), None)
    tileset = ({"kind": "carray", "file": tiles["file"], "symbol": tiles["name"], "palette_file": pals["file"] if pals else "",
                "palette_symbol": pals["name"] if pals else ""} if tiles else
               ({"kind": "png", "path": p["images"][0]} if p.get("images") else {}))
    body = None
    if maps:
        items = ["新しく作る"] + [f"{a['name']} ({a['file']}, {a['count']})" for a in maps]
        choice, ok = QInputDialog.getItem(win, "新しいステージ", "既存のマップ配列を編集しますか？", items, 0, False)
        if not ok:
            return
        if choice != items[0]:
            a = maps[items.index(choice) - 1]
            w, ok = QInputDialog.getText(win, "新しいステージ", "マップの幅 (タイル数) 空欄 = 自動", text="")
            body = {"from": "array", "file": a["file"], "symbol": a["name"], "width": int(w) if w.strip().isdigit() else None, "tileset": tileset}
    if body is None:
        sz, ok = QInputDialog.getText(win, "新しいステージ", "幅,高さ (タイル数)  例: 20,18 / 縦スクロール 20,128", text="20,32")
        if not ok:
            return
        nums = [int(x) for x in re.split(r"[,x×\s]+", sz.strip()) if x.isdigit()] + [20, 18]
        body = {"from": "new", "width": nums[0] or 20, "height": nums[1] or 18, "tileset": tileset}
    try:
        win.api.post("/api/stage/" + q(name), body)
    except ApiFail as e:
        win.log(f"エラー: {e}", "e")
        return
    win.load_project()
    win.open_tab("stage", name)


class TilesetCanvas(QWidget):
    Z = 2

    def __init__(self, view):
        super().__init__()
        self.v = view

    def paintEvent(self, e):
        v = self.v
        pm = v.tileset(v.pal)
        if pm is None:
            return
        z = self.Z
        p = QPainter(self)
        p.drawPixmap(QRect(0, 0, pm.width() * z, pm.height() * z), pm)
        p.setPen(QColor(255, 255, 255, 31))
        for gx in range(17):
            p.drawLine(gx * 8 * z, 0, gx * 8 * z, pm.height() * z)
        for gy in range(pm.height() // 8 + 1):
            p.drawLine(0, gy * 8 * z, pm.width() * z, gy * 8 * z)
        p.setPen(QPen(QColor("#ffd34d"), 2))
        p.drawRect((v.sel % 16) * 8 * z, (v.sel // 16) * 8 * z, 8 * z, 8 * z)
        p.end()

    def mousePressEvent(self, e):
        z = self.Z
        t = int(e.position().y() // (8 * z)) * 16 + int(e.position().x() // (8 * z))
        v = self.v
        if t < v.tile_count:
            v.sel = t
            if v.tool in ("pick", "object", "attr"):
                v.set_tool("paint")
            v.update_tileset()


class StageCanvas(QWidget):
    def __init__(self, view):
        super().__init__()
        self.v = view
        self.setMouseTracking(True)

    def paintEvent(self, e):
        v = self.v
        d = v.data
        if not d:
            return
        z = v.zoom
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0))
        clip = e.rect()
        x0, y0 = max(0, clip.left() // (8 * z)), max(0, clip.top() // (8 * z))
        x1, y1 = min(d["width"], clip.right() // (8 * z) + 1), min(d["height"], clip.bottom() // (8 * z) + 1)
        attrs = d.get("attrs")
        for ty in range(y0, y1):
            for tx in range(x0, x1):
                i = ty * d["width"] + tx
                pal = (attrs[i] & 7) if attrs else 0
                pm = v.tileset(pal)
                t = d["tiles"][i]
                r = QRect(tx * 8 * z, ty * 8 * z, 8 * z, 8 * z)
                if pm is not None and t < v.tile_count:
                    p.drawPixmap(r, pm, QRect((t % 16) * 8, (t // 16) * 8, 8, 8))
                else:
                    p.fillRect(r, QColor("#802050"))
        if v.opt["attr"].isChecked() and attrs:
            f = QFont("monospace")
            f.setPixelSize(max(8, 4 * z))
            p.setFont(f)
            p.setPen(QColor(255, 255, 0, 230))
            for ty in range(y0, y1):
                for tx in range(x0, x1):
                    p.drawText(tx * 8 * z + 1, ty * 8 * z + 4 * z + 2, str(attrs[ty * d["width"] + tx] & 7))
        if v.opt["grid"].isChecked():
            p.setPen(QColor(255, 255, 255, 26))
            for gx in range(d["width"] + 1):
                p.drawLine(gx * 8 * z, 0, gx * 8 * z, d["height"] * 8 * z)
            for gy in range(d["height"] + 1):
                p.drawLine(0, gy * 8 * z, d["width"] * 8 * z, gy * 8 * z)
        if v.opt["screen"].isChecked():
            pen = QPen(QColor("#3fb950"), 2)
            pen.setStyle(Qt.DashLine)
            p.setPen(pen)
            for sy in range(0, d["height"] * 8, 144):
                p.drawRect(1, sy * z + 1, min(160, d["width"] * 8) * z - 2, min(144, d["height"] * 8 - sy) * z - 2)
        types = v.win.proj.get("object_types") or []
        for i, o in enumerate(d.get("objects") or []):
            t = next((tt for tt in types if tt["name"] == o["type"]), {"color": "#cccccc"})
            col = QColor(t.get("color") or "#cccccc")
            ox, oy = o["x"] * z, o["y"] * z
            fill = QColor(col)
            fill.setAlpha(153)
            p.fillRect(ox, oy, 16 * z, 16 * z, fill)
            p.setPen(QPen(QColor("#ffd34d") if i == v.sel_obj else col, 3 if i == v.sel_obj else 2))
            p.drawRect(ox + 1, oy + 1, 16 * z - 2, 16 * z - 2)
            p.setPen(QColor("#ffffff"))
            f = QFont()
            f.setBold(True)
            f.setPixelSize(max(9, 4 * z))
            p.setFont(f)
            p.drawText(ox + 2, oy + 5 * z, o["type"])
        if v.drag and "rect" in v.drag:
            r = v.drag["rect"]
            p.setPen(QPen(QColor("#ffd34d"), 2))
            p.drawRect(min(r[0], r[2]) * 8 * z, min(r[1], r[3]) * 8 * z, (abs(r[2] - r[0]) + 1) * 8 * z, (abs(r[3] - r[1]) + 1) * 8 * z)
        p.end()

    def pos(self, e):
        z = self.v.zoom
        px, py = e.position().x() / z, e.position().y() / z
        return px, py, int(px // 8), int(py // 8)

    def mousePressEvent(self, e):
        v = self.v
        d = v.data
        if not d:
            return
        px, py, tx, ty = self.pos(e)
        if e.modifiers() & Qt.AltModifier or v.tool == "pick" or e.button() == Qt.RightButton:
            i = ty * d["width"] + tx
            if 0 <= tx < d["width"] and 0 <= ty < d["height"]:
                v.sel = d["tiles"][i]
                if d.get("attrs"):
                    v.pal = d["attrs"][i] & 7
                    v.pal_box.setCurrentIndex(v.pal)
                v.update_tileset()
            return
        if v.tool == "object":
            i = v.obj_at(px, py)
            if i >= 0:
                v.sel_obj = i
                v.push_undo()
                v.drag = {"obj": i, "dx": px - d["objects"][i]["x"], "dy": py - d["objects"][i]["y"]}
            else:
                types = v.win.proj.get("object_types") or []
                if not types:
                    v.log("先にインスペクターで「オブジェクトの種類」を追加してください", "w")
                    return
                v.push_undo()
                d.setdefault("objects", []).append({"type": v.last_type or types[0]["name"], "x": max(0, int((px - 8) // 8) * 8),
                                                    "y": max(0, int((py - 8) // 8) * 8), "props": {}})
                v.sel_obj = len(d["objects"]) - 1
                v.mark_dirty()
            self.update()
            v.refresh_inspector()
            return
        v.push_undo()
        if v.tool == "rect":
            v.drag = {"rect": [tx, ty, tx, ty]}
            self.update()
            return
        v.drag = {"paint": True}
        if v.set_cell(tx, ty):
            v.mark_dirty()

    def mouseMoveEvent(self, e):
        v = self.v
        if not v.data:
            return
        px, py, tx, ty = self.pos(e)
        v.cursor.setText(f"タイル ({tx}, {ty})")
        d = v.drag
        if not d:
            return
        if "paint" in d:
            if v.set_cell(tx, ty):
                v.mark_dirty()
        elif "rect" in d:
            d["rect"][2], d["rect"][3] = tx, ty
            self.update()
        elif "obj" in d:
            o = v.data["objects"][d["obj"]]
            st = 1 if e.modifiers() & Qt.ShiftModifier else 8
            nx = max(0, round((px - d["dx"]) / st) * st)
            ny = max(0, round((py - d["dy"]) / st) * st)
            if (nx, ny) != (o["x"], o["y"]):
                o["x"], o["y"] = nx, ny
                v.mark_dirty()

    def mouseReleaseEvent(self, e):
        v = self.v
        d = v.drag
        if d and "rect" in d:
            r = d["rect"]
            for y in range(min(r[1], r[3]), max(r[1], r[3]) + 1):
                for x in range(min(r[0], r[2]), max(r[0], r[2]) + 1):
                    v.set_cell(x, y)
            v.mark_dirty()
        elif d and "obj" in d:
            v.refresh_inspector()
        v.drag = None
        self.update()


@register("stage")
class StageView(View):
    icon = "🗺"

    def __init__(self, win, name):
        super().__init__(win, name)
        self.data = None
        self.dirty = False
        self.tool = "paint"
        self.sel = 0
        self.pal = 0
        self.sel_obj = -1
        self.drag = None
        self.undo: list[str] = []
        self.last_type = None
        self.tile_count = 0
        self.pms: dict[int, QPixmap | None] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        tb = QToolBar()
        self.title_lbl = label("", "title", wrap=False)
        tb.addWidget(self.title_lbl)
        self.tools = QButtonGroup(self)
        self.tool_btns = {}
        for t, text, tip in TOOLS:
            b = QPushButton(text)
            b.setCheckable(True)
            b.setToolTip(tip)
            b.clicked.connect(lambda *_, t=t: self.set_tool(t))
            self.tools.addButton(b)
            self.tool_btns[t] = b
            tb.addWidget(b)
        tb.addSeparator()
        self.opt = {"grid": check(True, lambda *_: self.canvas.update(), "グリッド"),
                    "attr": check(False, lambda *_: self.canvas.update(), "パレット番号表示"),
                    "screen": check(True, lambda *_: self.canvas.update(), "画面枠")}
        for w in self.opt.values():
            tb.addWidget(w)
        tb.addWidget(QLabel("  ズーム "))
        self.zoom_box = combo([1, 2, 3, 4], 2, lambda *_: self.resize_canvas())
        tb.addWidget(self.zoom_box)
        tb.addWidget(button("保存 (Ctrl+S)", lambda: self.save(False)))
        tb.addWidget(button("↶", self.do_undo, tip="元に戻す (Ctrl+Z)"))
        self.cursor = label("", "muted", wrap=False)
        tb.addWidget(self.cursor)
        lay.addWidget(tb)
        split = QSplitter()
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.addWidget(label("タイル (クリックで選択)", "hint"))
        self.ts_canvas = TilesetCanvas(self)
        ts_area = QScrollArea()
        ts_area.setWidget(self.ts_canvas)
        ts_area.setMinimumWidth(16 * 8 * TilesetCanvas.Z + 24)
        ll.addWidget(ts_area, 1)
        self.pal_box = combo([(i, str(i)) for i in range(8)], 0, self._pal_changed)
        ll.addWidget(core.hbox(QLabel("パレット"), self.pal_box, "stretch"))
        self.sel_info = label("", "hint")
        ll.addWidget(self.sel_info)
        split.addWidget(left)
        self.canvas = StageCanvas(self)
        self.area = QScrollArea()
        self.area.setWidget(self.canvas)
        split.addWidget(self.area)
        split.setStretchFactor(1, 1)
        lay.addWidget(split, 1)
        self.set_tool("paint")
        self.setFocusPolicy(Qt.StrongFocus)

    @property
    def zoom(self):
        return int(self.zoom_box.currentData() or 2)

    def load(self):
        self.data = self.api.get("/api/stage/" + q(self.name))
        self.undo = []
        self.sel_obj = -1
        self.dirty = False
        ex = self.data.get("export") or {}
        self.title_lbl.setText(f"🗺 {self.name}" + (f"  (→ {ex.get('file')} の {ex.get('map_symbol')})  " if ex.get("mode") == "array" else "  (→ stage_data.c)  "))
        self.load_tileset()
        self.resize_canvas()

    def load_tileset(self):
        self.pms = {}
        self.tileset(0)
        self.update_tileset()

    def tileset(self, pal: int):
        ts = self.data.get("tileset") or {}
        if pal and not (ts.get("kind") == "carray" and ts.get("palette_symbol")):
            pal = 0
        if pal not in self.pms:
            try:
                r = self.api.binary("GET", f"/api/stage/{q(self.name)}/tileset.png?pal={pal}")
                self.pms[pal] = png_to_pixmap(r.body)
                if pal == 0:
                    self.tile_count = int(r.headers.get("X-Tile-Count", "0"))
            except ApiFail:
                self.pms[pal] = None
        return self.pms[pal]

    def update_tileset(self):
        pm = self.tileset(self.pal)
        if pm is not None:
            self.ts_canvas.setFixedSize(pm.width() * TilesetCanvas.Z, pm.height() * TilesetCanvas.Z)
        self.ts_canvas.update()
        self.sel_info.setText(f"タイル {self.sel} (マップの値 {self.sel + (self.data.get('tile_origin') or 0)}) / 全 {self.tile_count} 枚 / パレット {self.pal}")

    def _pal_changed(self, v):
        self.pal = int(v or 0)
        self.update_tileset()

    def resize_canvas(self):
        if self.data:
            self.canvas.setFixedSize(self.data["width"] * 8 * self.zoom, self.data["height"] * 8 * self.zoom)
        self.canvas.update()

    def set_tool(self, t):
        self.tool = t
        for k, b in self.tool_btns.items():
            b.setChecked(k == t)

    def push_undo(self):
        d = self.data
        self.undo.append(json.dumps({"tiles": d["tiles"], "attrs": d.get("attrs"), "objects": d.get("objects")}))
        if len(self.undo) > 60:
            self.undo.pop(0)

    def do_undo(self):
        if not self.undo:
            return
        self.data.update(json.loads(self.undo.pop()))
        self.sel_obj = -1
        self.mark_dirty()
        self.refresh_inspector()

    def set_cell(self, tx, ty) -> bool:
        d = self.data
        if tx < 0 or ty < 0 or tx >= d["width"] or ty >= d["height"]:
            return False
        i = ty * d["width"] + tx
        if self.tool == "attr":
            if not d.get("attrs"):
                d["attrs"] = [0] * len(d["tiles"])
            if (d["attrs"][i] & 7) == self.pal:
                return False
            d["attrs"][i] = (d["attrs"][i] & ~7) | self.pal
            return True
        if d["tiles"][i] == self.sel:
            return False
        d["tiles"][i] = self.sel
        if d.get("attrs") and (d.get("tileset") or {}).get("palette_symbol"):
            d["attrs"][i] = (d["attrs"][i] & ~7) | self.pal
        return True

    def obj_at(self, px, py) -> int:
        os_ = self.data.get("objects") or []
        for i in range(len(os_) - 1, -1, -1):
            if os_[i]["x"] <= px < os_[i]["x"] + 16 and os_[i]["y"] <= py < os_[i]["y"] + 16:
                return i
        return -1

    def mark_dirty(self):
        if not self.dirty:
            self.dirty = True
            self.changed()
        self.canvas.update()

    def is_dirty(self):
        return self.dirty

    def save(self, quiet=True):
        r = self.api.put("/api/stage/" + q(self.name), self.data)
        self.dirty = False
        if not quiet:
            self.log(f"ステージ {self.name} を保存しました", "o")
        for ln in r.get("log") or []:
            self.log(ln, "o" if not quiet else "c")
        self.win.extra.pop("arrays", None)
        self.changed()

    def keyPressEvent(self, e):
        if e.modifiers() & Qt.ControlModifier and e.key() == Qt.Key_Z:
            self.do_undo()
            return
        m = {"b": "paint", "r": "rect", "i": "pick", "p": "attr", "o": "object"}
        k = e.text().lower()
        if k in m:
            self.set_tool(m[k])
        elif e.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.sel_obj >= 0:
            self.push_undo()
            self.data["objects"].pop(self.sel_obj)
            self.sel_obj = -1
            self.mark_dirty()
            self.refresh_inspector()
        else:
            super().keyPressEvent(e)

    def resize_stage(self, w, hh):
        d = self.data
        self.push_undo()
        nt = [0] * (w * hh)
        na = [0] * (w * hh) if d.get("attrs") else None
        for y in range(min(hh, d["height"])):
            for x in range(min(w, d["width"])):
                nt[y * w + x] = d["tiles"][y * d["width"] + x]
                if na is not None:
                    na[y * w + x] = d["attrs"][y * d["width"] + x]
        d.update(width=w, height=hh, tiles=nt, attrs=na)
        self.mark_dirty()
        self.resize_canvas()
        self.refresh_inspector()

    # ------------------------------------------------------------------
    def inspector(self):
        d = self.data
        if not d:
            return None
        p = self.win.proj
        arrs = arrays(self.win)
        tiles = [a for a in arrs if a["kind"] == "tiles"]
        pals = [a for a in arrs if a["kind"] == "palette"]
        ts = d.setdefault("tileset", {})

        def reload_ts():
            self.mark_dirty()
            self.save(True)
            self.load_tileset()
            self.canvas.update()

        def set_kind(v):
            d["tileset"] = {"kind": v}
            self.mark_dirty()
            self.refresh_inspector()

        def set_tiles(v):
            a = next((x for x in arrs if key_of(x) == v), None)
            if a:
                ts.update(kind="carray", file=a["file"], symbol=a["name"])
                reload_ts()

        def set_pal(v):
            a = next((x for x in arrs if key_of(x) == v), None) if v else None
            ts.update(palette_file=a["file"] if a else "", palette_symbol=a["name"] if a else "")
            reload_ts()

        def set_png(v):
            ts["path"] = v
            reload_ts()

        def set_attrs(on):
            d["attrs"] = [0] * len(d["tiles"]) if on else None
            self.mark_dirty()

        def set_origin(v):
            d["tile_origin"] = v
            self.mark_dirty()
            self.update_tileset()
        win_ = spin(d["width"], 1, 1024)
        hin = spin(d["height"], 1, 1024)
        src = (form(("画像", combo(p.get("images", []), ts.get("path"), set_png, "(選択)"))) if ts.get("kind") == "png" else
               form(("タイル配列", combo([(key_of(a), f"{a['name']} ({a['file']}, {a['count'] // 16}枚)") for a in tiles],
                                       f"{ts.get('file')}#{ts.get('symbol')}" if ts.get("file") else None, set_tiles, "(選択)")),
                    ("パレット配列", combo([(key_of(a), f"{a['name']} ({a['file']})") for a in pals],
                                        f"{ts.get('palette_file')}#{ts.get('palette_symbol')}" if ts.get("palette_symbol") else None, set_pal,
                                        "(初代の 4 階調)"))))
        out = p["settings"].get("stages_output") or "stage_data"
        ex = d.get("export") or {}
        boxes = [group(f"🗺 ステージ: {self.name}",
                       form(("大きさ", core.hbox(win_, QLabel("×"), hin, button("変更", lambda: self.resize_stage(win_.value(), hin.value()))))),
                       hint(f"{d['width'] * 8}×{d['height'] * 8} px / 画面 {-(-d['width'] // 20)}×{-(-d['height'] // 18)} 枚分"),
                       form(("タイルの元", combo([("carray", "C 配列のタイル"), ("png", "PNG 画像")], ts.get("kind") or "carray", set_kind))),
                       src,
                       form(("タイル番号のずれ", spin(d.get("tile_origin") or 0, 0, 255, set_origin), "マップに書く値 = タイル番号 + この値 (VRAM の途中に読み込む場合)"),
                            ("CGB 属性", check(bool(d.get("attrs")), set_attrs), "タイルごとのパレット番号を保存 (カラー用)")),
                       hint(f"保存すると {ex.get('file')} の配列 {ex.get('map_symbol')} を書き換えます (他の部分はそのまま)。" if ex.get("mode") == "array" else
                            f'保存すると {out}.c / .h に出力。C からは #include "{out}.h" → stage_{self.name}.map / .objects'),
                       script_add_box(self.win, [out + ".c"] if (ex.get("mode") != "array" or d.get("objects")) else []),
                       button("ステージを削除", self.delete_stage, "danger"))]
        # オブジェクトの種類 (プレハブ)
        types = p.get("object_types") or []

        def save_types():
            self.api.put("/api/object_types", types)
            self.win.proj["object_types"] = types
            self.mark_dirty()

        rows = []
        for i, t in enumerate(types):
            nm = QLineEdit(t["name"])
            nm.setFont(core.mono_font())

            def ren(nm=nm, t=t):
                old = t["name"]
                t["name"] = re.sub(r"\W", "_", nm.text()) or old
                for o in d.get("objects") or []:
                    if o["type"] == old:
                        o["type"] = t["name"]
                save_types()
            nm.editingFinished.connect(ren)
            colb = QPushButton()
            colb.setFixedWidth(34)
            colb.setStyleSheet(f"background: {t.get('color') or '#cccccc'};")

            def pickc(t=t, b=colb):
                c = QColorDialog.getColor(QColor(t.get("color") or "#cccccc"), self)
                if c.isValid():
                    t["color"] = c.name()
                    b.setStyleSheet(f"background: {t['color']};")
                    save_types()
            colb.clicked.connect(lambda *_, f=pickc: f())
            fields = QLineEdit(",".join(t.get("fields") or []))
            fields.setPlaceholderText("値の名前 (例: speed,lane)")
            fields.setToolTip("最大 4 個")

            def setf(fw=fields, t=t):
                t["fields"] = [x for x in re.split(r"[,\s]+", fw.text()) if x][:4]
                save_types()
                self.refresh_inspector()
            fields.editingFinished.connect(setf)

            def rm(i=i):
                types.pop(i)
                save_types()
                self.refresh_inspector()
            rows.append(core.hbox(nm, colb, fields, button("×", rm)))

        def add_type():
            types.append({"name": f"obj{len(types) + 1}", "color": "#4c8bf5", "fields": []})
            save_types()
            self.refresh_inspector()
        boxes.append(group("◆ オブジェクトの種類", *rows, button("＋ 種類を追加", add_type),
                           hint("C では OBJ_名前 の番号と、p[0]〜p[3] に値が入った stage_object_t の配列になります")))
        objs = d.get("objects") or []
        if 0 <= self.sel_obj < len(objs):
            o = objs[self.sel_obj]
            t = next((tt for tt in types if tt["name"] == o["type"]), None)

            def set_type(v):
                o["type"] = v
                self.last_type = v
                self.mark_dirty()
                self.refresh_inspector()

            def setxy(k):
                def f(v):
                    o[k] = v
                    self.mark_dirty()
                return f
            rowsf = [("種類", combo([tt["name"] for tt in types], o["type"], set_type)), ("X (px)", spin(o["x"], 0, 8192, setxy("x"))),
                     ("Y (px)", spin(o["y"], 0, 8192, setxy("y")))]
            for f in (t or {}).get("fields") or []:
                def setp(v, f=f):
                    o.setdefault("props", {})[f] = v
                    self.mark_dirty()
                rowsf.append((f, spin((o.get("props") or {}).get(f, 0), -32768, 65535, setp)))

            def rm_obj():
                self.push_undo()
                objs.pop(self.sel_obj)
                self.sel_obj = -1
                self.mark_dirty()
                self.refresh_inspector()
            boxes.append(group(f"選択中のオブジェクト #{self.sel_obj}", form(*rowsf), button("削除 (Delete)", rm_obj, "danger")))
        boxes.append(hint("操作: B=タイル R=矩形 I=スポイト(右クリック/Alt) P=パレット O=オブジェクト (Shift でドット単位移動) / Ctrl+Z 元に戻す"))
        return core.vbox(*boxes)

    def delete_stage(self):
        if not ask(self, "削除", f"ステージ {self.name} を削除しますか？ (C ソースは消えません)"):
            return
        self.api.delete("/api/stage/" + q(self.name))
        self.dirty = False
        self.win.load_project()
        self.win.close_view(self)
