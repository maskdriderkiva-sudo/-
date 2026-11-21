"""日本語テキストのエディタ (ゲームボーイのメッセージウィンドウと同じ見た目のプレビュー付き)。"""
from __future__ import annotations

import re

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QPlainTextEdit, QToolBar,
                               QVBoxLayout, QWidget)

from ..core import GB_GREEN, ask, button, check, hint, label
from ..local import ApiFail, q
from ..view import View, register

BORDER = [[0, 0, 0, 0x1F, 0x10, 0x10, 0x10, 0x10], [0, 0, 0, 0xFF, 0, 0, 0, 0], [0, 0, 0, 0xF8, 8, 8, 8, 8],
          [0x10] * 8, [8] * 8, [0x10, 0x10, 0x10, 0x10, 0x1F, 0, 0, 0], [0, 0, 0, 0, 0xFF, 0, 0, 0], [8, 8, 8, 8, 0xF8, 0, 0, 0]]


class Glyphs:
    """フォントのビットマップのキャッシュ (/api/font)"""

    def __init__(self, api):
        self.api = api
        self.font: dict[str, list[int] | None] = {}

    def ensure(self, chars):
        need = [c for c in set(chars) if c not in self.font]
        if not need:
            return
        r = self.api.get("/api/font", chars="".join(need))
        for c in need:
            self.font[c] = [int(r[c][i:i + 2], 16) for i in range(0, 16, 2)] if c in r else None


def _tile(img: QImage, ox: int, oy: int, rows, z: int, p: QPainter):
    col = QColor(*GB_GREEN[3])
    for y, b in enumerate(rows):
        for i in range(8):
            if b & (0x80 >> i):
                p.fillRect((ox + i) * z, (oy + y) * z, z, z, col)


def text_preview(api, glyphs: Glyphs, text: str, choice: bool, zoom: int = 2) -> tuple[list[QPixmap], list[str], int]:
    """GB のメッセージウィンドウと同じ見た目の画像 (ページごと)、フォントに無い文字、ページ数。"""
    r = api.post("/api/text/preview", {"text": text, "choice": choice})
    glyphs.ensure([ch for page in r["pages"] for row in page for ch in row] + ["▶"])
    out = []
    for pi, page in enumerate(r["pages"]):
        img = QImage(160 * zoom, 40 * zoom, QImage.Format_RGB32)
        img.fill(QColor(*GB_GREEN[0]))
        p = QPainter(img)
        tiles = [(0, 0, 0), (19, 0, 2), (0, 4, 5), (19, 4, 7)] + [(i, 0, 1) for i in range(1, 19)] + \
            [(i, 4, 6) for i in range(1, 19)] + [(0, j, 3) for j in range(1, 4)] + [(19, j, 4) for j in range(1, 4)]
        for tx, ty, t in tiles:
            _tile(img, tx * 8, ty * 8, BORDER[t], zoom, p)
        for ly, row in enumerate(page):
            for cx, ch in enumerate(row):
                g = glyphs.font.get(ch)
                if g:
                    _tile(img, 8 + cx * 8, 8 + ly * 8, g, zoom, p)
        if choice and pi == len(r["pages"]) - 1 and len(page) >= 2 and glyphs.font.get("▶"):
            _tile(img, 8, 8 + (len(page) - 2) * 8, glyphs.font["▶"], zoom, p)
        p.end()
        out.append(QPixmap.fromImage(img))
    return out, r["missing"], len(r["pages"])


def preview_widget(api, glyphs, text, choice, zoom=2) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    try:
        pms, missing, _ = text_preview(api, glyphs, text, choice, zoom)
    except ApiFail as e:
        lay.addWidget(label(str(e), "err"))
        return w
    for i, pm in enumerate(pms):
        lb = QLabel()
        lb.setPixmap(pm)
        lb.setToolTip(f"ページ {i + 1}")
        lay.addWidget(lb)
    if missing:
        lay.addWidget(label("フォントに無い文字: " + " ".join(missing), "warn"))
    return w


@register("texts")
class TextsView(View):
    icon = "💬"

    def __init__(self, win, name):
        super().__init__(win, name)
        self.texts: dict = {}
        self.sel: str | None = None
        self.dirty = False
        self.glyphs = win.extra.setdefault("glyphs", Glyphs(self.api))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        tb = QToolBar()
        tb.addWidget(label("テキスト (日本語)  ", "title", wrap=False))
        tb.addWidget(button("＋ 追加", self.add))
        tb.addWidget(button("保存 (Ctrl+S)", lambda: self.save(False)))
        tb.addWidget(label("  改行 = 改行 / 「---」だけの行 = 改ページ / {n} = 数値 / 18文字×3行で自動折り返し", "hint", wrap=False))
        lay.addWidget(tb)
        body = QHBoxLayout()
        self.list = QListWidget()
        self.list.setMaximumWidth(220)
        self.list.currentTextChanged.connect(self._select)
        body.addWidget(self.list)
        right = QVBoxLayout()
        self.key = QLineEdit()
        self.key.editingFinished.connect(self._rename)
        self.choice = check(False, self._choice, "2択 (最後の2行が選択肢)")
        right.addWidget(_row(QLabel("ID"), self.key, self.choice, button("削除", self.delete, "danger")))
        self.body = QPlainTextEdit()
        self.body.textChanged.connect(self._edited)
        self.body.setMaximumHeight(200)
        right.addWidget(self.body)
        self.info = label("", "muted")
        right.addWidget(self.info)
        self.preview = QWidget()
        self.pv_lay = QVBoxLayout(self.preview)
        self.pv_lay.setContentsMargins(0, 0, 0, 0)
        right.addWidget(self.preview)
        right.addStretch(1)
        rw = QWidget()
        rw.setLayout(right)
        body.addWidget(rw, 1)
        bw = QWidget()
        bw.setLayout(body)
        lay.addWidget(bw, 1)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._update_preview)
        self._loading = False

    def title(self):
        return "テキスト"

    def load(self):
        self.texts = self.api.get("/api/texts")
        self.dirty = False
        if self.sel not in self.texts:
            self.sel = sorted(self.texts)[0] if self.texts else None
        self._render_list()

    def _render_list(self):
        self.list.blockSignals(True)
        self.list.clear()
        for k in sorted(self.texts):
            self.list.addItem(k)
        items = self.list.findItems(self.sel or "", Qt.MatchExactly)
        if items:
            self.list.setCurrentItem(items[0])
        self.list.blockSignals(False)
        self._show()

    def _select(self, k):
        if k:
            self.sel = k
            self._show()

    def _show(self):
        t = self.texts.get(self.sel)
        self._loading = True
        self.key.setText(self.sel or "")
        self.body.setPlainText(t["text"] if t else "")
        self.choice.setChecked(bool(t and t.get("choice")))
        self.body.setEnabled(bool(t))
        self._loading = False
        self.timer.start(50)

    def _edited(self):
        if self._loading or self.sel not in self.texts:
            return
        self.texts[self.sel]["text"] = self.body.toPlainText()
        self._dirty()

    def _choice(self, v):
        if self._loading or self.sel not in self.texts:
            return
        self.texts[self.sel]["choice"] = v
        self._dirty()

    def _dirty(self):
        self.dirty = True
        self.changed()
        self.timer.start(150)

    def _rename(self):
        nk = self.key.text().strip()
        if not self.sel or nk == self.sel:
            return
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", nk) or nk in self.texts:
            self.log("ID は半角英数字で、重複しない名前にしてください", "w")
            self.key.setText(self.sel)
            return
        self.texts[nk] = self.texts.pop(self.sel)
        self.sel = nk
        self._dirty()
        self._render_list()
        self.log("テキスト ID を変更しました。アクター・スクリプトの参照も更新してください", "w")

    def add(self):
        k, ok = QInputDialog.getText(self, "テキストを追加", "テキスト ID (半角英数字)", text=f"text{len(self.texts) + 1}")
        k = k.strip()
        if not ok or not k:
            return
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k) or k in self.texts:
            self.log("ID が不正か重複しています", "w")
            return
        self.texts[k] = {"text": "ここに ぶんしょうを かきます。", "choice": False}
        self.sel = k
        self._dirty()
        self._render_list()

    def delete(self):
        if not self.sel or not ask(self, "削除", f"テキスト {self.sel} を削除しますか？"):
            return
        del self.texts[self.sel]
        self.sel = sorted(self.texts)[0] if self.texts else None
        self._dirty()
        self._render_list()

    def _update_preview(self):
        while self.pv_lay.count():
            w = self.pv_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        t = self.texts.get(self.sel)
        if not t:
            self.info.setText("")
            return
        try:
            pms, missing, n = text_preview(self.api, self.glyphs, t["text"], t.get("choice", False), 2)
        except ApiFail as e:
            self.info.setText(str(e))
            return
        for pm in pms:
            lb = QLabel()
            lb.setPixmap(pm)
            self.pv_lay.addWidget(lb)
        if missing:
            self.pv_lay.addWidget(label("フォントに無い文字: " + " ".join(missing), "warn"))
        self.info.setText(f"{n} ページ / スクリプトでは TEXT_{self.sel.upper()}")

    def is_dirty(self):
        return self.dirty

    def save(self, quiet=True):
        self.api.put("/api/texts", self.texts)
        self.dirty = False
        if not quiet:
            self.log("テキストを保存しました", "o")
        self.changed()
        self.win.load_project()

    def inspector(self):
        return hint("テキスト ID はアクターの「話しかける」やスクリプトの TEXT_ID 定数で使います。")


def _row(*ws) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    for x in ws:
        lay.addWidget(x)
    return w
