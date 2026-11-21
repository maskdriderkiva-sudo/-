"""C ソースの配列 (タイル・パレット・マップ・データ) を表示・編集する。"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QColorDialog, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QPlainTextEdit, QPushButton,
                               QScrollArea, QToolBar, QVBoxLayout, QWidget)

from .. import core
from ..core import ask, button, combo, hint, label, png_to_pixmap, spin
from ..local import ApiFail, q
from ..view import View, register

KIND_ICON = {"tiles": "▦", "palette": "🎨", "map": "🗺", "data": "#"}


def arrays(win, force: bool = False) -> list:
    if force or win.extra.get("arrays") is None:
        win.extra["arrays"] = win.api.get("/api/carrays")
    return win.extra["arrays"]


def key_of(a) -> str:
    return f"{a['file']}#{a['name']}"


@register("carray")
class CArrayView(View):
    icon = "🧩"

    def __init__(self, win, name):
        super().__init__(win, name)
        self.file, self.sym = name.split("#", 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        tb = QToolBar()
        self.lbl = label("", "title", wrap=False)
        self.info = label("", "muted", wrap=False)
        tb.addWidget(self.lbl)
        tb.addWidget(self.info)
        lay.addWidget(tb)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        lay.addWidget(self.area, 1)
        self.tv = win.extra.setdefault("tile_view", {"pal": "", "idx": 0, "order": "tiles", "scale": 4, "cols": 16})

    def title(self):
        return self.sym

    def load(self):
        a = self.api.get("/api/carray", file=self.file, name=self.sym)
        self.a = a
        self.lbl.setText(f"{KIND_ICON.get(a['kind'], '#')} {a['name']}")
        self.info.setText(f"   {a['file']}:{a['line']} / {a['ctype']} / {a['count']} 要素" + (f" ({a['count'] // 16} タイル)" if a["kind"] == "tiles" else ""))
        body = QWidget()
        self.body = QVBoxLayout(body)
        self.body.setContentsMargins(12, 12, 12, 12)
        if not a["parsed"]:
            self.body.addWidget(label("数値以外 (マクロなど) を含むため、表示・編集できない要素があります", "warn"))
        if a["kind"] == "tiles":
            self._tiles()
        elif a["kind"] == "palette":
            self._palette()
        else:
            self._data()
        self.body.addStretch(1)
        self.area.setWidget(body)

    # ---- タイル ----
    def _tile_params(self, scale=None) -> dict:
        tv = self.tv
        p = next((x for x in arrays(self.win) if key_of(x) == tv["pal"]), None) if tv["pal"] else None
        d = {"file": self.file, "name": self.sym, "pal_index": tv["idx"], "order": tv["order"], "cols": tv["cols"],
             "scale": scale or tv["scale"]}
        if p:
            d.update(pal_file=p["file"], pal_name=p["name"])
        return d

    def _tiles(self):
        tv = self.tv
        pals = [x for x in arrays(self.win) if x["kind"] == "palette"]
        if not tv["pal"] and pals:
            tv["pal"] = key_of(pals[0])
        self.img = QLabel()
        row = core.hbox(QLabel("パレット"), combo([(key_of(x), x["name"]) for x in pals], tv["pal"] or None, lambda v: self._set("pal", v or ""), "初代 4 階調"),
                        QLabel("番号"), combo([(i, str(i)) for i in range(8)], tv["idx"], lambda v: self._set("idx", v)),
                        QLabel("並び"), combo([("tiles", "8x8 タイル順"), ("sprite8x16", "8x16 スプライト (16x16)")], tv["order"], lambda v: self._set("order", v)),
                        QLabel("列"), spin(tv["cols"], 1, 64, lambda v: self._set("cols", v)),
                        QLabel("拡大"), combo([2, 3, 4, 6, 8], tv["scale"], lambda v: self._set("scale", v)), "stretch")
        self.body.addWidget(row)
        self.body.addWidget(self.img)
        self.body.addWidget(core.hbox(button("⤓ PNG で書き出し…", self.export_png), button("⤒ PNG から読み込んで書き換え…", self.import_png), "stretch"))
        self.body.addWidget(hint("書き出した PNG をお好みのドット絵ツールで編集し、「PNG から読み込んで書き換え」で C ソースの配列に戻せます (ソースの他の部分は変更しません)。"))
        self._refresh_img()

    def _set(self, k, v):
        self.tv[k] = v
        self._refresh_img()

    def _refresh_img(self):
        try:
            r = self.api.binary("GET", "/api/carray/png?" + urlencode(self._tile_params()))
            self.img.setPixmap(png_to_pixmap(r.body))
        except ApiFail as e:
            self.img.setText(str(e))

    def export_png(self):
        f, _ = QFileDialog.getSaveFileName(self, "PNG で書き出し", self.sym + ".png", "PNG 画像 (*.png)")
        if f:
            Path(f).write_bytes(self.api.binary("GET", "/api/carray/png?" + urlencode(self._tile_params(1))).body)
            self.log(f"書き出しました: {f}", "o")

    def import_png(self):
        f, _ = QFileDialog.getOpenFileName(self, "PNG を選ぶ", "", "PNG 画像 (*.png)")
        if not f or not ask(self, "書き換え", f"{Path(f).name} の内容で {self.sym} を書き換えます。よろしいですか？ (パレットの色に一番近い色に変換)"):
            return
        p = self._tile_params()
        qd = {k: p[k] for k in ("file", "name", "pal_index") if k in p}
        if "pal_name" in p:
            qd.update(pal_file=p["pal_file"], pal_name=p["pal_name"])
        try:
            r = self.api.call("POST", "/api/carray/png?" + urlencode(qd), raw=Path(f).read_bytes())
            self.log(f"{self.sym} を PNG から更新しました ({r['tiles']} タイル)", "o")
            self.win.extra.pop("arrays", None)
            self._refresh_img()
        except ApiFail as e:
            self.log(f"エラー: {e}", "e")

    # ---- パレット ----
    def _palette(self):
        self.vals = list(self.a.get("values") or [])
        for p in range(0, len(self.vals), 4):
            row = QWidget()
            hl = QHBoxLayout(row)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.addWidget(label(f"パレット {p // 4}", "muted"))
            for i in range(p, min(p + 4, len(self.vals))):
                b = QPushButton()
                b.setFixedSize(40, 26)
                self._paint(b, i)
                b.clicked.connect(lambda *_, i=i, b=b: self._pick(i, b))
                hl.addWidget(b)
            hl.addStretch(1)
            self.body.addWidget(row)
        self.body.addWidget(button("パレットを保存 (C ソースに書き戻し)", self._save_values, "play"))
        self.body.addWidget(hint("ゲームボーイカラーの色は各 5 ビット (RGB 0〜31) です。"))

    def _rgb(self, v):
        return [((c << 3) | (c >> 2)) for c in (v & 31, (v >> 5) & 31, (v >> 10) & 31)]

    def _paint(self, b, i):
        r, g, bb = self._rgb(self.vals[i])
        b.setStyleSheet(f"background: rgb({r},{g},{bb}); border: 1px solid #555;")
        v = self.vals[i]
        b.setToolTip(f"RGB({v & 31}, {(v >> 5) & 31}, {(v >> 10) & 31})")

    def _pick(self, i, b):
        c = QColorDialog.getColor(QColor(*self._rgb(self.vals[i])), self, "色を選ぶ")
        if c.isValid():
            self.vals[i] = (c.red() >> 3) | ((c.green() >> 3) << 5) | ((c.blue() >> 3) << 10)
            self._paint(b, i)

    def _save_values(self):
        try:
            self.api.put("/api/carray", {"values": self.vals}, file=self.file, name=self.sym)
            self.log(f"{self.sym} を保存しました", "o")
            self.win.extra.pop("arrays", None)
        except ApiFail as e:
            self.log(f"エラー: {e}", "e")

    # ---- マップ・データ ----
    def _data(self):
        if self.a["kind"] == "map":
            self.body.addWidget(core.hbox(button("🗺 ステージエディタで編集", self.to_stage, "play"), "stretch"))
        self.ta = QPlainTextEdit(", ".join(str(v) for v in (self.a.get("values") or [])))
        self.ta.setFont(core.mono_font())
        self.ta.setMinimumHeight(260)
        self.body.addWidget(self.ta)
        self.body.addWidget(core.hbox(button("値を保存 (C ソースに書き戻し)", self._save_text), "stretch"))

    def _save_text(self):
        import re
        try:
            vals = [int(v, 16 if v.lower().startswith("0x") else 10) for v in re.split(r"[\s,]+", self.ta.toPlainText()) if v]
        except ValueError:
            self.log("数値以外が含まれています", "w")
            return
        self.vals = vals
        self._save_values()

    def to_stage(self):
        import re
        sname, ok = QInputDialog.getText(self, "ステージ", "ステージ名 (この配列をステージエディタで編集します)", text=re.sub(r"\W", "_", self.sym))
        if not ok or not sname.strip():
            return
        w, ok = QInputDialog.getText(self, "ステージ", "幅 (タイル数) 空欄 = 自動推定", text="")
        arrs = arrays(self.win)
        t = next((x for x in arrs if x["kind"] == "tiles"), None)
        p = next((x for x in arrs if x["kind"] == "palette"), None)
        try:
            self.api.post("/api/stage/" + q(sname.strip()), {
                "from": "array", "file": self.file, "symbol": self.sym, "width": int(w) if w.strip().isdigit() else None,
                "tileset": {"kind": "carray", "file": t["file"], "symbol": t["name"], "palette_file": p["file"] if p else "",
                            "palette_symbol": p["name"] if p else ""} if t else {}})
        except ApiFail as e:
            self.log(f"エラー: {e}", "e")
            return
        self.win.load_project()
        self.win.open_tab("stage", sname.strip())

    def inspector(self):
        return hint("C ソースの配列を直接表示・編集しています。書き戻しは初期化子 { … } の中だけで、コメントや他のコードはそのままです。")
