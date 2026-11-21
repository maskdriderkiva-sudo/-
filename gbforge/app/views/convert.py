"""変換ツール: 画像 → ゲームボーイ / カラー用のタイル・スプライト・マップ (C データ)。"""
from __future__ import annotations

import base64
import re
from pathlib import Path
from urllib.parse import urlencode

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QPlainTextEdit, QScrollArea, QSplitter, QToolBar,
                               QVBoxLayout, QWidget)

from .. import core
from ..core import button, check, combo, form, hint, label, line, png_to_pixmap, spin
from ..local import ApiFail, q
from ..view import View, register


def swatches(palettes, per_row: int = 8) -> QWidget:
    from PySide6.QtWidgets import QGridLayout
    w = QWidget()
    lay = QGridLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setHorizontalSpacing(6)
    for i, p in enumerate(palettes):
        g = QWidget()
        gl = QHBoxLayout(g)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setSpacing(0)
        for c in p:
            s = QLabel()
            s.setFixedSize(14, 14)
            s.setStyleSheet(f"background: rgb({c[0]},{c[1]},{c[2]}); border: 1px solid #3a3c42;")
            gl.addWidget(s)
        g.setToolTip(f"パレット {i}")
        lay.addWidget(g, i // per_row, i % per_row)
    lay.setColumnStretch(per_row, 1)
    return w


@register("convert")
class ConvertView(View):
    icon = "🧰"

    def __init__(self, win, name):
        super().__init__(win, name)
        self.upload: bytes | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        tb = QToolBar()
        tb.addWidget(label("変換ツール: 画像 → ゲームボーイ / カラー用データ", "title", wrap=False))
        lay.addWidget(tb)
        split = QSplitter()
        self.src = combo([], None, lambda v: self._src_changed(), "(選択)")
        self.mode = combo([("map", "背景マップ (重複タイル除去 + マップ + CGB 属性)"), ("sprite8x16", "スプライト 8x16 モード"),
                           ("sprite8x8", "スプライト 8x8 モード"), ("tiles", "タイルだけ (並び順そのまま)")], "map")
        self.target = combo([("cgb", "ゲームボーイカラー (パレット自動生成)"), ("dmg", "初代 (4 階調)")], "cgb")
        self.sw, self.sh = spin(16, 8, 256, step=8), spin(16, 8, 256, step=8)
        self.origin = spin(0, 0, 255)
        self.bank = line(placeholder="空 = 固定 / 255 = 自動")
        self.dedupe = check(True, text="同じタイルをまとめる")
        self.out_name = line("converted", mono=True)
        self.stats = QVBoxLayout()
        sw = QWidget()
        sw.setLayout(self.stats)
        left = core.vbox(form(("画像", self.src), ("または PC から", button("PNG を選ぶ…", self.pick)), ("変換方法", self.mode),
                              ("対象", self.target), ("スプライトの幅 / 高さ", core.hbox(self.sw, self.sh)),
                              ("タイル番号の開始", self.origin), ("バンク", self.bank, "空 = 固定バンクなし、255 = 自動 (-autobank)"),
                              self.dedupe, ("出力名 (C の変数名)", self.out_name)),
                         core.hbox(button("変換プレビュー", lambda: self.run(False), "play"), button(".c / .h を保存", lambda: self.run(True))),
                         sw, "stretch", margins=8)
        left.setMaximumWidth(420)
        self.orig, self.prev = QLabel(), QLabel()
        self.pals = QWidget()
        self.pal_lay = QVBoxLayout(self.pals)
        self.code = QPlainTextEdit()
        self.code.setReadOnly(True)
        self.code.setFont(core.mono_font())
        right = core.vbox(core.hbox(core.vbox(label("元画像", "muted"), self.orig), core.vbox(label("変換結果 (データから描き直し)", "muted"), self.prev), "stretch"),
                          self.pals, self.code, margins=8)
        split.addWidget(left)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(right)
        split.addWidget(sc)
        split.setStretchFactor(1, 1)
        lay.addWidget(split, 1)

    def title(self):
        return "変換ツール"

    def load(self):
        self.src.blockSignals(True)
        cur = self.src.currentData()
        self.src.clear()
        self.src.addItem("(選択)", None)
        for f in self.win.proj.get("images", []):
            self.src.addItem(f, f)
        pre = self.win.extra.pop("cv_preset", None) or cur
        if pre:
            self.src.setCurrentIndex(max(0, self.src.findData(pre)))
        self.src.blockSignals(False)
        self.target.setCurrentIndex(self.target.findData("dmg" if self.win.proj["target"] == "dmg" else "cgb"))
        if pre:
            self._src_changed()

    def _src_changed(self):
        f = self.src.currentData()
        if not f:
            return
        self.upload = None
        self.out_name.setText(re.sub(r"\W", "_", Path(f).stem))
        self._show_orig(self.api.binary("GET", "/file?path=" + q(f)).body)

    def _show_orig(self, data: bytes):
        pm = png_to_pixmap(data)
        z = max(1, min(4, 320 // max(1, pm.width())))
        self.orig.setPixmap(pm.scaled(pm.width() * z, pm.height() * z, Qt.KeepAspectRatio, Qt.FastTransformation))

    def pick(self):
        f, _ = QFileDialog.getOpenFileName(self, "PNG を選ぶ", "", "PNG 画像 (*.png)")
        if not f:
            return
        self.upload = Path(f).read_bytes()
        self.src.setCurrentIndex(0)
        self.out_name.setText(re.sub(r"\W", "_", Path(f).stem))
        self._show_orig(self.upload)

    def run(self, save: bool):
        src = self.src.currentData()
        if not src and not self.upload:
            self.log("画像を選んでください", "w")
            return
        params = {"src": "" if self.upload else src, "mode": self.mode.currentData(), "target": self.target.currentData(),
                  "sw": self.sw.value(), "sh": self.sh.value(), "origin": self.origin.value(), "bank": self.bank.text().strip(),
                  "dedupe": 1 if self.dedupe.isChecked() else 0, "name": self.out_name.text().strip()}
        try:
            r = self.api.call("POST", f"/api/convert{'/save' if save else ''}?" + urlencode(params), raw=self.upload or b"")
        except ApiFail as e:
            self.log(f"エラー: {e}", "e")
            return
        pm = QPixmap()
        pm.loadFromData(base64.b64decode(r["preview"]))
        self.prev.setPixmap(pm)
        while self.stats.count():
            w = self.stats.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.stats.addWidget(label(f"タイル {r['tile_count']} 枚" + (f" / マップ {r['map_w']}×{r['map_h']}" if r.get("map_w") else "") +
                                   (f" / {r['frames']} フレーム" if r.get("frames") else "") + f" / パレット {len(r['palettes'])} 本"))
        for w in r["warnings"]:
            self.stats.addWidget(label(w, "warn"))
        while self.pal_lay.count():
            w = self.pal_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.pal_lay.addWidget(swatches(r["palettes"]))
        self.code.setPlainText(r["h"] + "\n/* ---- .c ---- */\n" + r["c"][:3000] + ("\n…" if len(r["c"]) > 3000 else ""))
        if save:
            self.log(f"保存しました: {', '.join(r['files'])}", "o")
            self.win.extra.pop("arrays", None)
            self.win.load_project()
            box = script_add_box(self.win, [f for f in r["files"] if f.endswith(".c")])
            if box:
                self.stats.addWidget(box)

    def inspector(self):
        return hint("ゲームボーイカラーでは 8×8 (スプライト 8x16 では 8×16) ごとの色から BG/OBJ パレット (最大 8 本 × 4 色) を自動で"
                    "組み立てます。スプライトは透明 + 3 色。保存すると .c / .h ができ、ビルドに自動で含まれます "
                    "(スクリプト方式の場合は build.bat に追加してください)。")


def script_add_box(win, files: list[str]) -> QWidget | None:
    """スクリプト方式 (build.bat) のとき、新しい .c をビルドスクリプトへ追加するボタン"""
    if win.proj.get("build_mode") != "script" or not files:
        return None
    try:
        r = win.api.get("/api/build_script/has", files=",".join(files))
    except ApiFail:
        return None
    missing = [f for f in files if not r["has"].get(f)]
    if not missing:
        return None
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(label(f"{', '.join(missing)} がビルドスクリプトに入っていません", "warn"))

    def add():
        res = win.api.post("/api/build_script/add", {"files": missing})
        win.log(f"ビルドスクリプトに追加しました: {', '.join(res['changed'])} (元のファイルは .bak に保存)", "o")
        box.hide()
    lay.addWidget(button("⊕ ビルドスクリプト (build.bat など) に追加", add))
    return box
