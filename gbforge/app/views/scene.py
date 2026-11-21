"""シーンエディタ (ゲームエンジン型): 背景・当たり判定・アクターを配置する。"""
from __future__ import annotations

import copy
import re

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QButtonGroup, QLabel, QPushButton, QScrollArea, QToolBar, QVBoxLayout, QWidget

from .. import core
from ..core import ask, button, check, combo, form, gb_image, group, hint, label, line, spin
from ..local import ApiFail, q
from ..view import View, register
from .texts import Glyphs, preview_widget

BHV_LABEL = {"none": "なし (スクリプトのみ)", "player": "プレイヤー操作", "talk": "話しかける (テキスト)",
             "warp": "ワープ (シーン移動)", "wander": "うろうろ歩く"}
BHV_ICON = {"player": "🕹", "talk": "💬", "warp": "⇥", "wander": "🐾", "none": "◻"}
DIR_LABEL = {"down": "下", "up": "上", "left": "左", "right": "右"}


class SceneCanvas(QWidget):
    def __init__(self, view):
        super().__init__()
        self.v = view
        self.setMouseTracking(True)
        self.drag = None

    def paintEvent(self, e):
        v = self.v
        sc = v.scene
        if not sc:
            return
        z = v.zoom
        w, hh = v.size_tiles()
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0))
        bg = v.bg_image()
        if bg is not None:
            p.drawImage(QRect(0, 0, w * 8 * z, hh * 8 * z), bg)
        if v.opt["coll"].isChecked():
            col = QColor(248, 81, 73, 97)
            for ty, row in enumerate(sc["collision"]):
                for tx, c in enumerate(row):
                    if c == "1":
                        p.fillRect(tx * 8 * z, ty * 8 * z, 8 * z, 8 * z, col)
        if v.opt["grid"].isChecked():
            for gx in range(w + 1):
                p.setPen(QColor(0, 0, 0, 31 if gx % 2 else 71))
                p.drawLine(gx * 8 * z, 0, gx * 8 * z, hh * 8 * z)
            for gy in range(hh + 1):
                p.setPen(QColor(0, 0, 0, 31 if gy % 2 else 71))
                p.drawLine(0, gy * 8 * z, w * 8 * z, gy * 8 * z)
        for i, a in enumerate(sc["actors"]):
            px, py = a["x"] * z, a["y"] * z
            spr = v.sprite_image(a.get("sprite"))
            if spr is not None:
                p.setOpacity(0.4 if a.get("hidden") else 1.0)
                f = v.frame_for(a)
                p.drawImage(QRect(px, py, 16 * z, 16 * z), spr, QRect(f * 16, 0, 16, 16))
                p.setOpacity(1.0)
            else:
                warp = a.get("behaviour") == "warp"
                p.fillRect(px, py, 16 * z, 16 * z, QColor(76, 139, 245, 90) if warp else QColor(255, 255, 255, 51))
                pen = QPen(QColor("#4c8bf5" if warp else "#ffffff"))
                pen.setStyle(Qt.DashLine)
                p.setPen(pen)
                p.drawRect(px, py, 16 * z - 1, 16 * z - 1)
                p.setPen(QColor("#ffffff"))
                f = QFont()
                f.setPixelSize(max(8, 4 * z))
                p.setFont(f)
                p.drawText(px + 2, py + 6 * z, ("⇥" + (a.get("warp") or {}).get("scene", "")) if warp else a["name"])
            if i == v.sel:
                p.setPen(QPen(QColor("#ffd34d"), 2))
                p.drawRect(px - 1, py - 1, 16 * z + 2, 16 * z + 2)
        player = next((a for a in sc["actors"] if a.get("behaviour") == "player"), None)
        if v.opt["cam"].isChecked() and player:
            cx = min(max(player["x"] + 8 - 80, 0), w * 8 - 160)
            cy = min(max(player["y"] + 8 - 72, 0), hh * 8 - 144)
            pen = QPen(QColor("#3fb950"), 2)
            pen.setStyle(Qt.DashLine)
            p.setPen(pen)
            p.drawRect(cx * z, cy * z, 160 * z, 144 * z)
            p.drawText(cx * z + 4, cy * z + 14, "カメラ (開始時)")
        p.end()

    def pos(self, e):
        z = self.v.zoom
        return e.position().x() / z, e.position().y() / z

    def mousePressEvent(self, e):
        v = self.v
        if not v.scene:
            return
        x, y = self.pos(e)
        if v.tool == "select":
            i = v.actor_at(x, y)
            v.sel = i
            if i >= 0:
                a = v.scene["actors"][i]
                self.drag = {"i": i, "dx": x - a["x"], "dy": y - a["y"]}
            v.selection_changed()
        elif v.tool == "collision":
            val = "0" if (e.button() == Qt.RightButton or e.modifiers() & Qt.ShiftModifier) else "1"
            self.drag = {"paint": val}
            if v.set_collision(int(x // 8), int(y // 8), val):
                v.mark_dirty()
        elif v.tool == "actor":
            v.add_actor(x, y)

    def mouseMoveEvent(self, e):
        v = self.v
        if not v.scene:
            return
        x, y = self.pos(e)
        w, hh = v.size_tiles()
        if 0 <= x < w * 8 and 0 <= y < hh * 8:
            v.cursor.setText(f"x {int(x)}, y {int(y)}  (タイル {int(x // 8)}, {int(y // 8)})")
        d = self.drag
        if not d:
            return
        if "paint" in d:
            if v.set_collision(int(x // 8), int(y // 8), d["paint"]):
                v.mark_dirty()
        else:
            a = v.scene["actors"][d["i"]]
            nx, ny = x - d["dx"], y - d["dy"]
            if v.opt["snap"].isChecked():
                nx, ny = round(nx / 8) * 8, round(ny / 8) * 8
            nx = max(0, min(w * 8 - 16, round(nx)))
            ny = max(0, min(hh * 8 - 16, round(ny)))
            if (nx, ny) != (a["x"], a["y"]):
                a["x"], a["y"] = nx, ny
                v.mark_dirty(inspector=True)

    def mouseReleaseEvent(self, e):
        self.drag = None

    def contextMenuEvent(self, e):
        pass


@register("scene")
class SceneView(View):
    icon = "🎬"

    def __init__(self, win, name):
        super().__init__(win, name)
        self.scene = None
        self.sel = -1
        self.dirty = False
        self.tool = "select"
        self.cache: dict = {}
        self.glyphs = win.extra.setdefault("glyphs", Glyphs(self.api))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        tb = QToolBar()
        self.tools = QButtonGroup(self)
        for t, text, tip in (("select", "↖ 選択", "選択・移動 (V)"), ("collision", "▦ 当たり判定", "当たり判定を塗る (C) 右クリック/Shift で消す"),
                             ("actor", "＋ アクター", "クリックでアクター追加 (A)")):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setToolTip(tip)
            b.setChecked(t == "select")
            b.clicked.connect(lambda *_, t=t: self.set_tool(t))
            self.tools.addButton(b)
            tb.addWidget(b)
        tb.addSeparator()
        self.opt = {k: check(True, lambda *_: self.canvas.update(), text) for k, text in
                    (("grid", "グリッド"), ("coll", "当たり判定表示"), ("snap", "8px スナップ"), ("cam", "カメラ枠"))}
        for w in self.opt.values():
            tb.addWidget(w)
        tb.addWidget(QLabel("  ズーム "))
        self.zoom_box = combo([2, 3, 4], 3, lambda *_: self.resize_canvas())
        tb.addWidget(self.zoom_box)
        self.cursor = label("", "muted", wrap=False)
        tb.addWidget(self.cursor)
        lay.addWidget(tb)
        self.canvas = SceneCanvas(self)
        self.canvas.setFocusPolicy(Qt.ClickFocus)
        self.area = QScrollArea()
        self.area.setWidget(self.canvas)
        self.area.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.area, 1)
        self.setFocusPolicy(Qt.StrongFocus)

    @property
    def zoom(self) -> int:
        return int(self.zoom_box.currentData() or 3)

    # ------------------------------------------------------------------
    def load(self):
        self.scene = self.api.get("/api/scene/" + q(self.name))
        self.sel = -1
        self.dirty = False
        self.cache = {}
        self.normalize()
        self.resize_canvas()

    def size_tiles(self):
        bs = (self.scene or {}).get("_bg_size")
        return (bs["width"] >> 3, bs["height"] >> 3) if bs else (20, 18)

    def normalize(self):
        w, hh = self.size_tiles()
        c = self.scene.get("collision") or []
        rows = []
        for y in range(hh):
            r = ((c[y] if y < len(c) else "") + "0" * w)[:w]
            rows.append(re.sub(r"[^01]", "0", r))
        self.scene["collision"] = rows

    def resize_canvas(self):
        w, hh = self.size_tiles()
        self.canvas.setFixedSize(w * 8 * self.zoom, hh * 8 * self.zoom)
        self.canvas.update()

    def bg_image(self):
        bg = self.scene.get("background")
        if not bg:
            return None
        bgp = int(self.scene.get("palette") or "E4", 16) if re.fullmatch(r"[0-9A-Fa-f]{1,2}", self.scene.get("palette") or "E4") else 0xE4
        k = ("bg", bg, bgp)
        if k not in self.cache:
            try:
                self.cache[k] = gb_image(self.api.binary("GET", f"/asset/backgrounds/{q(bg)}").body, "bg", bgp)
            except ApiFail:
                self.cache[k] = None
        return self.cache[k]

    def sprite_info(self, name):
        return next((s for s in self.win.proj["sprites"] if s["name"] == name), None)

    def sprite_image(self, name):
        if not name or not self.sprite_info(name):
            return None
        k = ("spr", name)
        if k not in self.cache:
            try:
                self.cache[k] = gb_image(self.api.binary("GET", f"/asset/sprites/{q(name)}.png").body, "sprite")
            except ApiFail:
                self.cache[k] = None
        return self.cache[k]

    def frame_for(self, a) -> int:
        si = self.sprite_info(a.get("sprite"))
        if not si:
            return 0
        dirs = self.win.proj["directions"]
        d = dirs.index(a.get("dir")) if a.get("dir") in dirs else 0
        return d if si["frames"] == 4 else d * 2 if si["frames"] == 8 else 0

    def actor_at(self, x, y) -> int:
        for i in range(len(self.scene["actors"]) - 1, -1, -1):
            a = self.scene["actors"][i]
            if a["x"] <= x < a["x"] + 16 and a["y"] <= y < a["y"] + 16:
                return i
        return -1

    def set_collision(self, tx, ty, val) -> bool:
        rows = self.scene["collision"]
        if ty < 0 or ty >= len(rows) or tx < 0 or tx >= len(rows[0]):
            return False
        r = rows[ty]
        if r[tx] == val:
            return False
        rows[ty] = r[:tx] + val + r[tx + 1:]
        return True

    def add_actor(self, x, y):
        sc = self.scene
        if len(sc["actors"]) >= 10:
            self.log("アクターは 1 シーン 10 体までです", "w")
            return
        snap = self.opt["snap"].isChecked()
        ax = int((x - 8) // 8) * 8 if snap else round(x - 8)
        ay = int((y - 8) // 8) * 8 if snap else round(y - 8)
        sprites = self.win.proj["sprites"]
        sc["actors"].append({"name": f"Actor{len(sc['actors']) + 1}", "sprite": sprites[0]["name"] if sprites else None,
                             "x": max(0, ax), "y": max(0, ay), "dir": "down", "behaviour": "talk", "script": None, "text": None,
                             "speed": 1, "solid": True, "hidden": False, "warp": {"scene": "", "x": 0, "y": 0}})
        self.sel = len(sc["actors"]) - 1
        self.set_tool("select")
        self.mark_dirty()
        self.selection_changed()

    def set_tool(self, t):
        self.tool = t
        for b, name in zip(self.tools.buttons(), ("select", "collision", "actor")):
            b.setChecked(name == t)

    def mark_dirty(self, inspector: bool = False):
        if not self.dirty:
            self.dirty = True
            self.changed()
        self.canvas.update()
        if inspector:
            self.refresh_inspector()

    def selection_changed(self):
        self.canvas.update()
        self.refresh_hierarchy()
        self.refresh_inspector()

    def is_dirty(self):
        return self.dirty

    def save(self, quiet=True):
        data = {k: v for k, v in self.scene.items() if k != "_bg_size"}
        self.api.put("/api/scene/" + q(self.name), data)
        self.dirty = False
        if not quiet:
            self.log(f"シーン {self.name} を保存しました", "o")
        self.changed()

    def keyPressEvent(self, e):
        k = e.text().lower()
        if k == "v":
            self.set_tool("select")
        elif k == "c":
            self.set_tool("collision")
        elif k == "a":
            self.set_tool("actor")
        elif e.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.sel >= 0:
            self.scene["actors"].pop(self.sel)
            self.sel = -1
            self.mark_dirty()
            self.selection_changed()
        else:
            super().keyPressEvent(e)

    # ------------------------------------------------------------------
    def hierarchy_title(self):
        return self.name

    def hierarchy(self):
        if not self.scene:
            return []

        def pick(i):
            return lambda: (setattr(self, "sel", i), self.selection_changed())
        out = [(f"🎬 {self.name} (シーン)", self.sel == -1, pick(-1))]
        for i, a in enumerate(self.scene["actors"]):
            out.append((f"{BHV_ICON.get(a.get('behaviour'), '◻')} {a['name']}" + (f"   {a['script']}.c" if a.get("script") else ""),
                        self.sel == i, pick(i)))
        return out

    def inspector(self):
        if not self.scene:
            return None
        sc, p = self.scene, self.win.proj

        def upd(fn, insp=False):
            def f(v):
                fn(v)
                self.mark_dirty(inspector=insp)
                self.refresh_hierarchy()
            return f
        if self.sel < 0:
            w, hh = self.size_tiles()

            def set_bg(v):
                sc["background"] = v
                self.mark_dirty()
                self.save(True)
                self.load()
                self.refresh_inspector()

            def set_pal(v):
                sc["palette"] = v.upper()
                self.cache = {}
                self.mark_dirty()

            def make_start():
                self.api.put("/api/project", {"start_scene": self.name})
                self.win.load_project()

            return group(f"🎬 シーン: {self.name}", form(
                ("背景", combo([b["file"] for b in p["backgrounds"]], sc.get("background"), set_bg, "(未設定)"),
                 "160×144〜256×256px の PNG。ユニークな 8×8 タイルは 128 個まで"),
                ("スクリプト", combo(p["scripts"], sc.get("script"), upd(lambda v: sc.__setitem__("script", v)), "(なし)"),
                 "シーン用スクリプトでは self は NULL"),
                ("BG パレット", line(sc.get("palette") or "E4", set_pal, mono=True), "BGP レジスタ値 (16進)。E4 が標準")),
                hint(f"サイズ {w}×{hh} タイル / アクター {len(sc['actors'])}/10"),
                core.hbox(button("開始シーンにする", make_start), button("シーンを削除", self.delete_scene, "danger"), "stretch"))
        a = sc["actors"][self.sel]
        pv = QLabel()
        spr = self.sprite_image(a.get("sprite"))
        if spr is not None:
            f = self.frame_for(a)
            pv.setPixmap(QPixmap.fromImage(spr.copy(f * 16, 0, 16, 16)).scaled(48, 48))

        def rename(t):
            a["name"] = re.sub(r"[^A-Za-z0-9_]", "_", t) or "Actor"
            self.mark_dirty()
            self.refresh_hierarchy()
        boxes = [core.hbox(pv, line(a["name"], rename))]
        boxes.append(group("Transform", form(
            ("X (px)", spin(a["x"], 0, 240, upd(lambda v: a.__setitem__("x", v)))),
            ("Y (px)", spin(a["y"], 0, 240, upd(lambda v: a.__setitem__("y", v)))),
            ("向き", combo([(d, DIR_LABEL[d]) for d in p["directions"]], a.get("dir"), upd(lambda v: a.__setitem__("dir", v)))))))
        boxes.append(group("Sprite Renderer", form(
            ("スプライト", combo([s["name"] for s in p["sprites"]], a.get("sprite"), upd(lambda v: a.__setitem__("sprite", v), True), "(非表示)"),
             "16×16。4フレーム=下上左右、8フレーム=各向き2コマ歩行"),
            ("非表示", check(a.get("hidden"), upd(lambda v: a.__setitem__("hidden", v)))),
            ("当たり判定", check(a.get("solid"), upd(lambda v: a.__setitem__("solid", v))), "ON だとプレイヤーが通り抜けられない"))))
        bh = [("種類", combo([(b, BHV_LABEL[b]) for b in p["behaviours"]], a.get("behaviour"), upd(lambda v: a.__setitem__("behaviour", v), True)))]
        b = a.get("behaviour")
        if b in ("player", "wander"):
            bh.append(("速度", spin(a.get("speed", 1), 1, 4, upd(lambda v: a.__setitem__("speed", max(1, min(4, v))))), "px / フレーム"))
        extra = []
        if b in ("talk", "wander", "none"):
            bh.append(("テキスト", combo(p["texts"], a.get("text"), upd(lambda v: a.__setitem__("text", v), True), "(なし)"), "A ボタンで話しかけると表示"))
            if a.get("text"):
                texts = self.win.extra.get("texts_cache") or self.api.get("/api/texts")
                self.win.extra["texts_cache"] = texts
                t = texts.get(a["text"])
                if t:
                    extra.append(preview_widget(self.api, self.glyphs, t["text"], t.get("choice", False), 1))
        if b == "warp":
            a.setdefault("warp", {"scene": "", "x": 0, "y": 0})
            wp = a["warp"]
            bh += [("行き先シーン", combo(p["scenes"], wp.get("scene") or None, upd(lambda v: wp.__setitem__("scene", v or "")), "(選択)")),
                   ("出現 X (タイル)", spin(wp.get("x", 0), 0, 31, upd(lambda v: wp.__setitem__("x", v)))),
                   ("出現 Y (タイル)", spin(wp.get("y", 0), 0, 31, upd(lambda v: wp.__setitem__("y", v))))]
            extra.append(hint("プレイヤーが触れると行き先シーンのタイル座標 (X, Y) に移動します"))
        boxes.append(group("ビヘイビア", form(*bh), *extra))
        boxes.append(group("スクリプト", form(("C スクリプト", combo(p["scripts"], a.get("script"), upd(lambda v: a.__setitem__("script", v), True), "(なし)"))),
                           button(f"{a['script']}.c を開く", lambda: self.win.open_tab("script", a["script"])) if a.get("script") else None,
                           hint(f"スクリプトからは ACTOR_{self.name.upper()}_{a['name'].upper()} で参照できます")))

        def dup():
            c = copy.deepcopy(a)
            c["name"] += "_copy"
            c["x"] = min(c["x"] + 16, 240)
            sc["actors"].append(c)
            self.sel = len(sc["actors"]) - 1
            self.mark_dirty()
            self.selection_changed()

        def rm():
            sc["actors"].pop(self.sel)
            self.sel = -1
            self.mark_dirty()
            self.selection_changed()
        boxes.append(core.hbox(button("複製", dup), button("削除", rm, "danger"), "stretch"))
        return core.vbox(*boxes)

    def delete_scene(self):
        if not ask(self, "削除", f"シーン {self.name} を削除しますか？"):
            return
        self.api.delete("/api/scene/" + q(self.name))
        self.dirty = False
        self.win.load_project()
        self.win.close_view(self)
