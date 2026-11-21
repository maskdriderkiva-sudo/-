"""GB Media Studio (ネイティブ版): 写真・動画を GB / GBC 用に変換し、タイムラインで演出を付ける。

プレビューの画面は必ず「書き出すデータ」を再生ライブラリと同じ規則で再生したもの (/api/media/frames)。
"""
from __future__ import annotations

import base64
import copy
import json
import random
import string
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QMimeData, QPoint, QPointF, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QDrag, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QInputDialog, QLabel, QListWidget, QListWidgetItem,
                               QScrollArea, QSizePolicy, QSplitter, QToolBar, QVBoxLayout, QWidget)

from .. import core
from ..core import COLORS, Slider, ask, button, check, combo, dspin, form, group, hint, label, line, run_async, spin
from ..local import ApiFail, q
from ..view import View, register
from .convert import script_add_box

GBFPS = 59.7275
DMG_SHADES = {"dmg_green": [(224, 248, 208), (136, 192, 112), (52, 104, 86), (8, 24, 32)],
              "dmg_gray": [(255, 255, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)]}
FX_COLOR = {"fade_in_black": "#3b5b8c", "fade_out_black": "#2c3e66", "fade_in_white": "#8c8c5b", "fade_out_white": "#6e6e45",
            "flash_white": "#b0a040", "flash_black": "#555555", "flicker": "#8c4b3b", "film": "#7a5a3a", "random": "#5a3a7a",
            "horror": "#8c2b2b"}
RANDOM_FX = ("flicker", "film", "random", "horror")
MIME = "application/x-gbm-asset"
TIP = {
    "preset": "写真らしさを残すための設定のまとめ。選ぶと下の値がその設定になります (個別に変えた値はそのプリセットの上書きになります)",
    "fit": "fill = 画面いっぱいにしてはみ出しを切る / fit = 全体を入れて余白を足す / stretch = 縦横比を無視して引き伸ばす",
    "zoom": "拡大して一部を切り出す (1 = そのまま)", "crop_x": "切り出す位置 (横)。0 = 左端、1 = 右端", "crop_y": "切り出す位置 (縦)。0 = 上、1 = 下",
    "pad": "fit のときの余白の色", "brightness": "明るさ (-100〜100)", "contrast": "コントラスト (-100〜100)。上げると白黒がはっきりする",
    "gamma": "ガンマ。1 より大きいと中間の明るさが明るくなる (暗い写真に)", "saturation": "彩度 (カラーのみ)",
    "black_point": "これより暗い所を黒にする (レベル補正)", "white_point": "これより明るい所を白にする (レベル補正)",
    "sharpen": "輪郭の強調。小さな画面では少し強めると見やすい", "denoise": "ノイズ除去。ざらつきを減らすとタイル数も減る",
    "grayscale": "カラーでも白黒にする",
    "dither": "ディザリング (少ない色で中間の明るさを点の混ぜ方で表す)。none はベタ塗り。Bayer は規則的な模様で動画のちらつきが少ない",
    "dither_strength": "ディザの強さ。弱くすると模様が減り、タイル数と動画の転送量も減る",
    "palettes": "カラー: 使う BG パレットの数 (1〜8)。8x8 ドットごとに 1 本のパレット (4 色) を使う",
    "tile_limit": "固有タイル数の上限 (0 = 自動: 初代 256 / カラー 512)。超えると似たタイルを統合する",
    "fps": "目標 FPS。ゲームボーイの画面は約 59.73 回/秒なので、1 枚を何フレーム表示するかに丸めます (例: 15 → 4 フレームずつ = 14.93 FPS)",
    "start": "動画のどこから使うか (秒)", "end": "どこまで使うか (秒、0 = 最後まで)", "speed": "再生速度 (2 = 倍速)",
    "threshold": "差分しきい値。前のフレームとの違いがこれ以下のタイルは送らない (上げると転送量・ROM が減るが、細かな動きが消える)",
    "frame_tile_limit": "動画 1 フレームで新しく送るタイルの上限 (0 = 自動)", "loop": "動画を最後まで再生したら最初に戻る",
    "strict_fps": "オン: 目標 FPS を必ず守る (間に合わない時は画質を大きく落とす)。オフ: 画質を落としすぎる時は表示時間を延ばす (FPS が下がる)",
}


def f2s(f) -> float:
    return f / GBFPS


def s2f(s) -> int:
    return int(round(s * GBFPS))


def uid(p: str) -> str:
    return p + "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(6))


# ---------------------------------------------------------------------------
# 素材の一覧 (タイムラインへドラッグできる)
# ---------------------------------------------------------------------------
class AssetList(QListWidget):
    def __init__(self, view):
        super().__init__()
        self.v = view
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setIconSize(QSize(44, 40))
        self.itemClicked.connect(lambda it: self.v.select(("asset", it.data(Qt.UserRole))))

    def startDrag(self, actions):
        it = self.currentItem()
        if not it:
            return
        md = QMimeData()
        md.setData(MIME, it.data(Qt.UserRole).encode())
        d = QDrag(self)
        d.setMimeData(md)
        if not it.icon().isNull():
            d.setPixmap(it.icon().pixmap(44, 40))
        d.exec(Qt.CopyAction)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    dragMoveEvent = dragEnterEvent

    def dropEvent(self, e):
        if e.mimeData().hasUrls():
            self.v.import_files([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])
            e.acceptProposedAction()


# ---------------------------------------------------------------------------
# タイムライン
# ---------------------------------------------------------------------------
class Timeline(QWidget):
    HEAD, RULER, MEDIA, LANE, PAL = 74, 22, 40, 20, 56

    def __init__(self, view):
        super().__init__()
        self.v = view
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.drag = None
        self.hover_edge = False

    # ---- 配置 ----
    def lanes(self):
        lanes, out = [], {}
        for f in sorted(self.v.sc()["fx"], key=lambda x: x["start"]):
            i = next((k for k, end in enumerate(lanes) if end <= f["start"]), -1)
            if i < 0:
                i = len(lanes)
                lanes.append(0)
            lanes[i] = f["start"] + f["duration"]
            out[f["id"]] = i
        return out, max(1, len(lanes))

    def rows(self):
        _, n = self.lanes()
        fx_h = n * self.LANE + 4
        y_media = self.RULER
        y_fx = y_media + self.MEDIA
        y_pal = y_fx + fx_h
        return y_media, y_fx, fx_h, y_pal, y_pal + self.PAL

    def relayout(self):
        s = self.v.sc()
        z = self.v.zoom
        *_, total = self.rows()
        self.setMinimumSize(self.HEAD + int(s["length"] * z) + 60, total)
        self.update()

    def x_of(self, f) -> float:
        return self.HEAD + f * self.v.zoom

    def f_of(self, x) -> int:
        return max(0, int(round((x - self.HEAD) / self.v.zoom)))

    def level_y(self, lv) -> float:
        _, _, _, y_pal, _ = self.rows()
        return y_pal + (31 - lv) / 62 * (self.PAL - 4) + 2

    # ---- 描画 ----
    def paintEvent(self, e):
        v = self.v
        s = v.sc()
        z = v.zoom
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(COLORS["panel2"]))
        y_media, y_fx, fx_h, y_pal, total = self.rows()
        W = self.width()
        # 目盛り
        p.fillRect(0, 0, W, self.RULER, QColor("#1b1c1f"))
        step = 0.25 if z >= 6 else 0.5 if z >= 2 else 1 if z >= 1 else 2
        t = 0.0
        f = QFont()
        f.setPixelSize(10)
        p.setFont(f)
        while s2f(t) <= s["length"] + 1:
            x = self.x_of(s2f(t))
            major = abs(t - round(t)) < 1e-6
            p.setPen(QColor("#888" if major else "#555"))
            p.drawLine(int(x), 2 if major else 12, int(x), self.RULER)
            if major:
                p.setPen(QColor(COLORS["muted"]))
                p.drawText(int(x) + 3, 12, f"{int(t)}s")
            t += step
        # 行の線と名前
        for y, h_, name in ((y_media, self.MEDIA, "映像"), (y_fx, fx_h, "演出"), (y_pal, self.PAL, "パレット")):
            p.setPen(QColor(COLORS["border"]))
            p.drawLine(0, y + h_, W, y + h_)
        # 終わりの線
        p.setPen(QPen(QColor("#666"), 1, Qt.DashLine))
        p.drawLine(int(self.x_of(s["length"])), self.RULER, int(self.x_of(s["length"])), total)
        # クリップ
        sel = v.sel
        for c in s["media"]:
            a = v.asset(c["asset"])
            r = QRect(int(self.x_of(c["start"])), y_media + 3, max(4, int(c["duration"] * z)), self.MEDIA - 6)
            p.setPen(QPen(QColor("#ffffff") if sel == ("clip", c["id"]) else QColor("#4c8bf5"), 2 if sel == ("clip", c["id"]) else 1))
            p.setBrush(QColor("#2f5d8a"))
            p.drawRoundedRect(r, 4, 4)
            icon = v.thumb(a)
            tx = r.left() + 4
            if icon is not None:
                p.drawPixmap(QRect(tx, r.top() + 3, 40, r.height() - 6), icon)
                tx += 44
            p.setPen(QColor("#ffffff"))
            p.drawText(QRect(tx, r.top(), max(0, r.right() - tx - 8), r.height()), Qt.AlignVCenter, (a or {}).get("name") or "?")
            p.fillRect(r.right() - 6, r.top(), 6, r.height(), QColor(255, 255, 255, 64))
        # 演出
        lanes, _ = self.lanes()
        for fx in s["fx"]:
            r = QRect(int(self.x_of(fx["start"])), y_fx + 2 + lanes.get(fx["id"], 0) * self.LANE, max(4, int(fx["duration"] * z)), self.LANE - 3)
            p.setPen(QPen(QColor("#ffffff") if sel == ("fx", fx["id"]) else QColor(255, 255, 255, 90), 2 if sel == ("fx", fx["id"]) else 1))
            p.setBrush(QColor(FX_COLOR.get(fx["type"], "#555555")))
            p.drawRoundedRect(r, 3, 3)
            p.setPen(QColor("#ffffff"))
            p.drawText(r.adjusted(4, 0, -8, 0), Qt.AlignVCenter, v.info["fx_types"].get(fx["type"], fx["type"]))
            p.fillRect(r.right() - 6, r.top(), 6, r.height(), QColor(255, 255, 255, 64))
        # パレット (明るさ)
        p.setPen(QColor("#444"))
        p.drawLine(self.HEAD, int(self.level_y(0)), W, int(self.level_y(0)))
        lv = v.frames and v.frames.get("levels")
        if lv:
            path = QPainterPath()
            for i, val in enumerate(lv):
                pt = QPointF(self.x_of(i), self.level_y(val))
                if i == 0:
                    path.moveTo(pt)
                else:
                    path.lineTo(pt)
            p.setPen(QPen(QColor(COLORS["muted"] if v.stale else COLORS["ok"]), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
        for k in s["palette"]:
            cx, cy = self.x_of(k["frame"]), self.level_y(k["level"])
            pts = [QPointF(cx, cy - 7), QPointF(cx + 7, cy), QPointF(cx, cy + 7), QPointF(cx - 7, cy)]
            p.setPen(QPen(QColor("#ffffff"), 1))
            p.setBrush(QColor("#ffffff") if sel == ("key", k["id"]) else QColor("#d29922"))
            p.drawPolygon(pts)
        # 見出し (左に固定)
        sx = self.v.tl_area.horizontalScrollBar().value()
        p.fillRect(sx, 0, self.HEAD, total, QColor(COLORS["panel"]))
        p.setPen(QColor(COLORS["muted"]))
        for y, h_, name in ((y_media, self.MEDIA, "映像"), (y_fx, fx_h, "演出"), (y_pal, self.PAL, "パレット")):
            p.drawText(QRect(sx + 6, y, self.HEAD - 8, h_), Qt.AlignVCenter, name)
        p.setPen(QColor(COLORS["border"]))
        p.drawLine(sx + self.HEAD, 0, sx + self.HEAD, total)
        # 再生位置
        x = int(self.x_of(v.frame))
        p.setPen(QPen(QColor("#f85149"), 1))
        p.drawLine(x, 0, x, total)
        p.end()

    # ---- 当たり判定 ----
    def hit(self, pos: QPointF):
        s = self.v.sc()
        z = self.v.zoom
        x, y = pos.x(), pos.y()
        y_media, y_fx, fx_h, y_pal, total = self.rows()
        if y < self.RULER:
            return ("ruler", None, False)
        if y_media <= y < y_media + self.MEDIA:
            for c in reversed(s["media"]):
                x0, x1 = self.x_of(c["start"]), self.x_of(c["start"]) + max(4, c["duration"] * z)
                if x0 <= x <= x1:
                    return ("clip", c, x >= x1 - 7)
            return ("media_row", None, False)
        if y_fx <= y < y_fx + fx_h:
            lanes, _ = self.lanes()
            for f in reversed(s["fx"]):
                ly = y_fx + 2 + lanes.get(f["id"], 0) * self.LANE
                x0, x1 = self.x_of(f["start"]), self.x_of(f["start"]) + max(4, f["duration"] * z)
                if x0 <= x <= x1 and ly <= y < ly + self.LANE:
                    return ("fx", f, x >= x1 - 7)
            return ("fx_row", None, False)
        if y_pal <= y < total:
            for k in reversed(s["palette"]):
                if abs(self.x_of(k["frame"]) - x) <= 7 and abs(self.level_y(k["level"]) - y) <= 7:
                    return ("key", k, False)
            return ("pal_row", None, False)
        return (None, None, False)

    def mousePressEvent(self, e):
        v = self.v
        kind, item, edge = self.hit(e.position())
        if kind == "ruler":
            self.drag = {"scrub": True}
            v.seek(self.f_of(e.position().x()))
            return
        if kind in ("clip", "fx", "key"):
            v.sel = (kind, item["id"])
            orig = {"frame": item["frame"]} if kind == "key" else {"start": item["start"], "duration": item["duration"]}
            s = v.sc()
            edges = [0, v.frame, s["length"]] + [g for c in s["media"] + s["fx"] if c is not item for g in (c["start"], c["start"] + c["duration"])]
            self.drag = {"kind": kind, "item": item, "x0": e.position().x(), "orig": orig, "resize": edge and kind != "key",
                         "moved": False, "edges": edges}
            self.update()
            v.refresh_inspector()
            return
        v.sel = None
        self.update()
        v.refresh_inspector()

    def _snap(self, f, shift: bool):
        if shift:
            return f
        for g in self.drag["edges"]:
            if abs(g - f) * self.v.zoom <= 6:
                return g
        return f

    def mouseMoveEvent(self, e):
        d = self.drag
        if not d:
            kind, item, edge = self.hit(e.position())
            self.setCursor(Qt.SizeHorCursor if edge else Qt.OpenHandCursor if kind in ("clip", "fx", "key") else Qt.ArrowCursor)
            return
        v = self.v
        if d.get("scrub"):
            v.seek(self.f_of(e.position().x()))
            return
        dx = e.position().x() - d["x0"]
        if not d["moved"] and abs(dx) < 3:
            return
        if not d["moved"]:
            v.snapshot()
            d["moved"] = True
        df = int(round(dx / v.zoom))
        it, o = d["item"], d["orig"]
        shift = bool(e.modifiers() & Qt.ShiftModifier)
        if d["kind"] == "key":
            it["frame"] = max(0, self._snap(o["frame"] + df, shift))
        elif d["resize"]:
            it["duration"] = max(1, self._snap(o["start"] + o["duration"] + df, shift) - it["start"])
        else:
            st = max(0, self._snap(o["start"] + df, shift))
            end_snap = self._snap(st + it["duration"], shift) - it["duration"]
            if not shift and end_snap != st and abs(end_snap - st) * v.zoom <= 6:
                st = max(0, end_snap)
            it["start"] = st
        self.relayout()

    def mouseReleaseEvent(self, e):
        d = self.drag
        self.drag = None
        if d and d.get("moved"):
            self.v.changed_media()

    def mouseDoubleClickEvent(self, e):
        kind, item, _ = self.hit(e.position())
        if kind == "pal_row":
            y_media, y_fx, fx_h, y_pal, total = self.rows()
            lv = max(-31, min(31, int(round(31 - (e.position().y() - y_pal - 2) / (self.PAL - 4) * 62))))
            v = self.v

            def add():
                k = {"id": uid("k"), "frame": self.f_of(e.position().x()), "level": lv, "interp": "linear"}
                v.sc()["palette"].append(k)
                v.sel = ("key", k["id"])
            v.change(add)

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        y_media = self.RULER
        if e.mimeData().hasFormat(MIME) and y_media <= e.position().y() < y_media + self.MEDIA:
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        aid = bytes(e.mimeData().data(MIME)).decode()
        self.v.add_clip(aid, self.f_of(e.position().x()))
        e.acceptProposedAction()


# ---------------------------------------------------------------------------
# プレビュー画面
# ---------------------------------------------------------------------------
class Screen(QLabel):
    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background: #000; border: 1px solid #3a3c42;")
        self.img: QImage | None = None
        self.scale = 2.0

    def set_image(self, img: QImage | None, scale: float):
        self.img = img
        self.scale = scale
        w, h = int(160 * scale), int(144 * scale)
        self.setFixedSize(w + 2, h + 2)
        if img is None:
            pm = QPixmap(w, h)
            pm.fill(QColor(0, 0, 0))
        else:
            pm = QPixmap.fromImage(img).scaled(w, h, Qt.IgnoreAspectRatio, Qt.FastTransformation)
        self.setPixmap(pm)


@register("media")
class MediaView(View):
    icon = "🎞"

    def __init__(self, win, name):
        super().__init__(win, name)
        self.proj: dict = {}
        self.info: dict = {}
        self.undo_stack: list[str] = []
        self.redo_stack: list[str] = []
        self.scene = 0
        self.sel = None
        self.frame = 0
        self.playing = False
        self.frames: dict | None = None
        self.screens: dict[int, np.ndarray] = {}
        self.stale = True
        self.zoom = 3.0
        self.dirty = False
        self.building = False
        self.rebuild = False
        self.report = None
        self.diags: list = []
        self.probe: dict = {}
        self.thumbs: dict = {}
        self.orig_key = None
        self.verify_result = None
        self.export_files: list[str] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        # ---- ツールバー (2 段) ----
        tb1 = QToolBar()
        self.name_lbl = label("", "title", wrap=False)
        tb1.addWidget(self.name_lbl)
        self.target = combo([("dmg", "初代 (4 階調)"), ("cgb", "カラー")], None, lambda v: self.change(lambda: self.proj.__setitem__("target", v)))
        self.target.setToolTip("出力するハード。初代は 4 階調 (BGP)、カラーは BG パレット 8 本 x 4 色 (RGB555)")
        tb1.addWidget(QLabel(" 対象 "))
        tb1.addWidget(self.target)
        self.fast = check(True, lambda v: self.change(lambda: self.proj.__setitem__("cgb_fast", v)), "倍速")
        self.fast.setToolTip("カラーの CPU 倍速モード (cpu_fast)。VRAM に送れる量が増え、動画の FPS や画質を上げられます。ゲーム側も倍速で動くことに注意")
        self.fast_act = tb1.addWidget(self.fast)
        tb1.addSeparator()
        tb1.addWidget(QLabel("シーン "))
        self.scene_box = combo([], None)
        self.scene_box.setToolTip("編集するシーン (書き出すと GBM_SCENE_名前 で呼び出せます)")
        self.scene_box.activated.connect(lambda i: self.set_scene(i))
        tb1.addWidget(self.scene_box)
        tb1.addWidget(button("＋", self.add_scene, tip="シーンを追加"))
        tb1.addWidget(button("名前", self.rename_scene, tip="シーンの名前を変える"))
        tb1.addWidget(button("削除", self.delete_scene, "danger", "シーンを削除"))
        tb1.addWidget(QLabel(" 長さ "))
        self.len_in = dspin(1, 0.1, 3600, 0.1, lambda v: self.change(lambda: self.sc().__setitem__("length", max(1, s2f(v)))), 2)
        self.len_in.setSuffix(" 秒")
        self.len_in.setToolTip("シーンの長さ (秒)。ゲームボーイの 1 秒 = 約 59.73 フレーム")
        tb1.addWidget(self.len_in)
        self.loop = check(False, lambda v: self.change(lambda: self.sc().__setitem__("loop", v)), "ループ")
        self.loop.setToolTip("最後まで行ったら最初から繰り返す")
        tb1.addWidget(self.loop)
        self.fade_obj = check(False, lambda v: self.change(lambda: self.sc().__setitem__("fade_obj", v)), "スプライトもフェード")
        self.fade_obj.setToolTip("スプライト (OBJ) のパレットも一緒にフェードさせる。ゲームが gbmedia_set_obj_palettes() で渡したパレットに効きます")
        tb1.addWidget(self.fade_obj)
        lay.addWidget(tb1)
        tb2 = QToolBar()
        tb2.addWidget(button("↶", lambda: self.do_undo(False), tip="元に戻す (Ctrl+Z)"))
        tb2.addWidget(button("↷", lambda: self.do_undo(True), tip="やり直す (Ctrl+Y)"))
        tb2.addWidget(button("保存", lambda: self.save(False), tip="保存 (Ctrl+S)。保存のたびに 1 つ前の内容を .gbmedia.bak に残します"))
        tb2.addWidget(button("⟳ 変換", lambda: self.build(True), tip="変換・エンコードして、書き出すデータからプレビューを作り直す (ハードの制約の診断も更新)"))
        tb2.addWidget(button("▶ ROM プレビュー", lambda: self.demo(False), "play", "このシーンだけを再生するデモ ROM を GBDK でコンパイルし、エミュレーターで起動"))
        tb2.addWidget(button("✔ ROM 検証", lambda: self.demo(True), None, "デモ ROM を PyBoy で動かし、プレビューと 1 フレームずつ比べる (実際の FPS・フレーム落ちを測る)"))
        tb2.addWidget(button("⤓ 書き出し", self.export, None, "GBDK 用の C ソース (gbmedia/) をプロジェクトに書き出す"))
        self.stale_lbl = label("", "warn", wrap=False)
        tb2.addWidget(self.stale_lbl)
        lay.addWidget(tb2)

        # ---- 上: 素材 | プレビュー ----
        vsplit = QSplitter(Qt.Vertical)
        top = QSplitter()
        aw = QWidget()
        al = QVBoxLayout(aw)
        al.setContentsMargins(6, 6, 6, 6)
        al.addWidget(core.hbox(label("素材 (ドラッグでタイムラインへ)", "hint", wrap=False), "stretch",
                               button("＋ 取り込み", self.pick_files, tip="写真・画像 (PNG/JPEG/BMP/WebP/GIF) や動画 (MP4/WebM/MOV) を取り込む。"
                                      "元のファイルはコピーして使い、書き換えません")))
        self.assets = AssetList(self)
        al.addWidget(self.assets, 1)
        al.addWidget(hint("ここにファイルをドロップしても取り込めます"))
        aw.setMinimumWidth(170)
        aw.setMaximumWidth(320)
        top.addWidget(aw)
        pw = QWidget()
        pl = QVBoxLayout(pw)
        pl.setContentsMargins(0, 0, 0, 0)
        scr = QWidget()
        scr.setStyleSheet("background: #141518;")
        sl = core.hbox()
        sl.layout().addStretch(1)
        self.orig_lbl = label("元の素材", "muted", wrap=False)
        self.conv_lbl = label("ゲームボーイの画面 (書き出すデータから再現)", "muted", wrap=False)
        self.orig = Screen()
        self.screen = Screen()
        self.orig_box = core.vbox(self.orig_lbl, self.orig)
        sl.layout().addWidget(self.orig_box)
        sl.layout().addWidget(core.vbox(self.conv_lbl, self.screen))
        sl.layout().addStretch(1)
        sv = QVBoxLayout(scr)
        sv.addStretch(1)
        sv.addWidget(sl)
        sv.addStretch(1)
        self.screen_area = scr
        pl.addWidget(scr, 1)
        self.time_lbl = label("", None, wrap=False)
        self.time_lbl.setFont(core.mono_font())
        self.play_btn = button("▶", self.play, tip="再生 / 停止 (Space)")
        self.loop_play = check(True, None, "ループ再生")
        self.scale_box = combo([("1", "1x"), ("2", "2x"), ("4", "4x"), ("8", "8x"), ("fit", "合わせる")], "fit", lambda *_: self.draw())
        self.mode_box = combo([("auto", "実際の色"), ("dmg_green", "DMG 緑"), ("dmg_gray", "DMG グレー")], "auto", lambda *_: self.draw())
        self.compare = check(True, lambda *_: (setattr(self, "orig_key", None), self.draw()), "元と比較")
        self.scale_box.setToolTip("表示倍率")
        self.mode_box.setToolTip("表示の色。初代は DMG の緑かグレー、カラーは実際の色")
        ctb = QToolBar()      # 狭い画面では » に畳まれる
        for w in (button("⏮", lambda: self.seek(0), tip="先頭へ (Home)"), button("◀|", lambda: self.step(-1), tip="1 フレーム戻る (←)"),
                  self.play_btn, button("|▶", lambda: self.step(1), tip="1 フレーム進む (→)"),
                  button("⏭", lambda: self.seek(self.sc()["length"]), tip="最後へ (End)"), self.loop_play, self.time_lbl,
                  QLabel(" 倍率 "), self.scale_box, QLabel(" 表示 "), self.mode_box, self.compare):
            ctb.addWidget(w)
        pl.addWidget(ctb)
        self.report_lbl = QLabel()
        self.report_lbl.setWordWrap(True)
        self.report_lbl.setTextFormat(Qt.RichText)
        self.report_lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.chart = QLabel()
        rep = core.vbox(self.report_lbl, self.chart, margins=6)
        self.report_area = QScrollArea()
        self.report_area.setWidgetResizable(True)
        self.report_area.setWidget(rep)
        self.report_area.setMaximumHeight(150)
        pl.addWidget(self.report_area)
        top.addWidget(pw)
        top.setStretchFactor(1, 1)
        vsplit.addWidget(top)
        # ---- 下: タイムライン ----
        tw = QWidget()
        tl = QVBoxLayout(tw)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(0)
        self.fx_type = combo([], None)
        self.fx_type.setToolTip("演出を再生位置に追加")
        self.zoom_sl = Slider(3, 0.5, 12, 0.5, lambda v, live: self.set_zoom(v), lambda v: f"{v:g}")
        self.zoom_sl.setFixedWidth(160)
        ttb = QToolBar()
        for w in (QLabel(" 演出 "), self.fx_type, button("＋ 追加", self.add_fx), button("◆ キーフレーム", self.add_key, tip="パレット (明るさ) のキーフレームを再生位置に追加"),
                  button("削除", self.delete_sel, "danger", "選んでいる物を削除 (Delete)"), QLabel(" ズーム "), self.zoom_sl,
                  label("  ドラッグで移動・右端で長さ・Shift で吸着なし・パレットの行をダブルクリックでキーフレーム", "hint", wrap=False)):
            ttb.addWidget(w)
        tl.addWidget(ttb)
        self.timeline = Timeline(self)
        self.tl_area = QScrollArea()
        self.tl_area.setWidget(self.timeline)
        self.tl_area.setWidgetResizable(False)
        self.tl_area.horizontalScrollBar().valueChanged.connect(lambda *_: self.timeline.update())
        tl.addWidget(self.tl_area, 1)
        vsplit.addWidget(tw)
        vsplit.setStretchFactor(0, 3)
        vsplit.setStretchFactor(1, 1)
        vsplit.setSizes([560, 230])
        lay.addWidget(vsplit, 1)

        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(lambda: self.save(True))
        self.build_timer = QTimer(self)
        self.build_timer.setSingleShot(True)
        self.build_timer.timeout.connect(lambda: self.build(False))
        self.prev_timer = QTimer(self)
        self.prev_timer.setSingleShot(True)
        self.prev_timer.timeout.connect(self._quick_preview)
        self.play_timer = QTimer(self)
        self.play_timer.setInterval(15)
        self.play_timer.timeout.connect(self._tick)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)

    # ------------------------------------------------------------------
    def title(self):
        return Path(self.name).stem

    def sc(self) -> dict:
        return self.proj["scenes"][self.scene]

    def asset(self, aid):
        return next((a for a in self.proj.get("assets", []) if a["id"] == aid), None)

    def url(self, sub: str) -> str:
        return f"/api/media/{sub}?path={q(self.name)}"

    def load(self):
        self.proj = self.api.get("/api/media/project", path=self.name)
        self.info = self.win.extra.get("media_info") or self.api.get("/api/media/info")
        self.win.extra["media_info"] = self.info
        self.fx_type.clear()
        for k, v in self.info["fx_types"].items():
            self.fx_type.addItem(v, k)
        self.undo_stack, self.redo_stack = [], []
        self.scene = 0
        self.sel = None
        self.frame = 0
        self.frames = None
        self.dirty = False
        self.render_all()
        self.schedule_build(10)

    # ------------------------------------------------------------------
    # 変更・元に戻す・保存
    # ------------------------------------------------------------------
    def slider_snapshot(self, key, live: bool):
        """スライダーを動かし始めた時に 1 回だけ記録 (離した時の値ではなく、動かす前の状態に戻せるように)"""
        if getattr(self, "_drag_key", None) != key:
            self.snapshot()
        self._drag_key = key if live else None

    def snapshot(self):
        self.undo_stack.append(json.dumps(self.proj, ensure_ascii=False))
        if len(self.undo_stack) > 200:
            self.undo_stack.pop(0)
        self.redo_stack = []

    def change(self, fn, render: bool = True, build: bool = True):
        self.snapshot()
        fn()
        self.changed_media(render, build)

    def changed_media(self, render: bool = True, build: bool = True):
        if not self.dirty:
            self.dirty = True
            self.changed()
        self.stale = True
        self.stale_lbl.setText("  変更あり — 変換中…")
        if render:
            self.render_all()
        self.save_timer.start(800)
        if build:
            self.schedule_build(1200)

    def do_undo(self, redo: bool):
        src, dst = (self.redo_stack, self.undo_stack) if redo else (self.undo_stack, self.redo_stack)
        if not src:
            return
        dst.append(json.dumps(self.proj, ensure_ascii=False))
        self.proj = json.loads(src.pop())
        if self.scene >= len(self.proj["scenes"]):
            self.scene = 0
        self.sel = None
        self.changed_media()

    def is_dirty(self):
        return self.dirty

    def save(self, quiet=True):
        if not self.dirty and quiet:
            return
        self.api.put(self.url("project"), self.proj)
        self.dirty = False
        if not quiet:
            self.log("保存しました: " + self.name, "o")
        self.changed()

    def closing(self):
        self.play_timer.stop()
        if self.dirty:
            self.save(True)

    # ------------------------------------------------------------------
    # 変換 (ジョブ) → プレビュー用のフレーム
    # ------------------------------------------------------------------
    def schedule_build(self, ms: int):
        self.build_timer.start(ms)

    def build(self, verbose: bool):
        if self.building:
            self.rebuild = True
            return
        self.building = True
        self.rebuild = False
        self.stale_lbl.setText("  変換中…")
        if self.dirty:
            self.save(True)
        try:
            job = self.api.post(self.url("build"), {})
        except ApiFail as e:
            self.log(f"エラー: {e}", "e")
            self.building = False
            return
        scene = self.scene
        st = {"since": 0}

        def poll():
            try:
                j = self.api.get(f"/api/job/{job['id']}", since=st["since"])
            except ApiFail:
                t.stop()
                self.building = False
                return
            if verbose:
                for ln in j["log"]:
                    self.log(ln)
            st["since"] = j["log_len"]
            if not j["done"]:
                return
            t.stop()
            if not j["ok"]:
                for ln in j["log"]:
                    if "エラー" in ln or "✖" in ln:
                        self.log(ln, "e")
            r = j.get("result") or {}
            if r.get("report"):
                self.report = r["report"]
                self.diags = r.get("diagnostics") or []
                run_async(self.api.get, "/api/media/frames", path=self.name, scene=scene,
                          on_done=lambda fr: self._frames_done(fr, scene), on_error=lambda e: self._build_end(str(e)))
            else:
                self._build_end(None)
        t = QTimer(self)
        t.timeout.connect(poll)
        t.start(150)

    def _frames_done(self, fr, scene):
        if scene == self.scene:
            self.frames = fr
            self.screens = {}
        self._build_end(None)

    def _build_end(self, err):
        if err:
            self.log(f"エラー: {err}", "e")
        self.building = False
        self.stale = self.rebuild
        self.stale_lbl.setText("  変更あり — 変換中…" if self.stale else "")
        self.render_report()
        self.draw()
        self.timeline.update()
        if self.rebuild:
            self.schedule_build(50)

    # ------------------------------------------------------------------
    # 描画
    # ------------------------------------------------------------------
    def render_all(self):
        if not self.proj:
            return
        p, s = self.proj, self.sc()
        self.name_lbl.setText(p.get("name", "") + "  ")
        for w, v in ((self.target, p.get("target")),):
            w.blockSignals(True)
            w.setCurrentIndex(max(0, w.findData(v)))
            w.blockSignals(False)
        self.fast.blockSignals(True)
        self.fast.setChecked(p.get("cgb_fast", True) is not False)
        self.fast.blockSignals(False)
        self.fast_act.setVisible(p.get("target") == "cgb")
        self.scene_box.clear()
        for x in p["scenes"]:
            self.scene_box.addItem(x["name"])
        self.scene_box.setCurrentIndex(self.scene)
        for w, v in ((self.len_in, round(f2s(s["length"]), 2)),):
            w.blockSignals(True)
            w.setValue(v)
            w.blockSignals(False)
        for w, v in ((self.loop, s.get("loop")), (self.fade_obj, s.get("fade_obj"))):
            w.blockSignals(True)
            w.setChecked(bool(v))
            w.blockSignals(False)
        self.render_assets()
        self.timeline.relayout()
        self.draw()
        self.render_report()
        self.refresh_inspector()

    def thumb(self, a, w: int = 64):
        if not a:
            return None
        k = (a["path"], w)
        if k not in self.thumbs:
            try:
                r = self.api.binary("GET", self.url("thumb") + f"&asset_path={q(a['path'])}&w={w}")
                self.thumbs[k] = core.png_to_pixmap(r.body)
            except ApiFail:
                self.thumbs[k] = None
        return self.thumbs[k]

    def render_assets(self):
        self.assets.clear()
        for a in self.proj.get("assets", []):
            used = any(c["asset"] == a["id"] for x in self.proj["scenes"] for c in x["media"])
            it = QListWidgetItem(("🖼 " if a.get("kind") == "image" else "🎞 ") + (a.get("name") or a["path"]) + "\n" +
                                 ("画像" if a.get("kind") == "image" else f"動画 {(a.get('video') or {}).get('fps', 10)}fps") + ("" if used else " · 未使用"))
            it.setData(Qt.UserRole, a["id"])
            it.setToolTip(a["path"])
            pm = self.thumb(a)
            if pm is not None:
                it.setIcon(pm)
            self.assets.addItem(it)
            if self.sel == ("asset", a["id"]):
                it.setSelected(True)
        if not self.proj.get("assets"):
            it = QListWidgetItem("「＋ 取り込み」で写真や動画を追加")
            it.setFlags(Qt.NoItemFlags)
            self.assets.addItem(it)

    def screen_image(self) -> QImage | None:
        F = self.frames
        if not F:
            return None
        fi = min(self.frame, len(F["frames"]) - 1)
        sid, pid = F["frames"][fi][0], F["frames"][fi][1]
        if sid not in self.screens:
            self.screens[sid] = np.frombuffer(base64.b64decode(F["screens"][sid]), np.uint8).reshape(144, 160)
        scr = self.screens[sid]
        pal = F["palettes"][pid]
        mode = self.mode_box.currentData()
        if F["target"] == "dmg":
            shades = DMG_SHADES["dmg_gray" if mode == "dmg_gray" else "dmg_green"]
            lut = np.array([shades[(pal[0] >> (2 * c)) & 3] for c in range(4)], np.uint8)
            rgb = lut[scr & 3]
        else:
            w = np.array(pal, np.int32)
            cols = np.stack([(w & 31), (w >> 5) & 31, (w >> 10) & 31], -1)
            lut = ((cols << 3) | (cols >> 2)).astype(np.uint8)
            if mode != "auto":
                yv = (lut[:, 0].astype(int) * 299 + lut[:, 1].astype(int) * 587 + lut[:, 2].astype(int) * 114) // 1000
                shades = np.array(DMG_SHADES[mode], np.uint8)
                lut = shades[np.where(yv > 212, 0, np.where(yv > 127, 1, np.where(yv > 42, 2, 3)))]
            rgb = lut[scr]
        return core.array_to_qimage(rgb)

    def scale(self) -> float:
        v = self.scale_box.currentData()
        if v != "fit":
            return float(v)
        n = 2 if self.compare.isChecked() else 1
        aw, ah = self.screen_area.width() - 40 * n, self.screen_area.height() - 50
        z = max(1.0, min(aw / (160 * n), ah / 144))
        return float(int(z)) if z >= 2 else max(1.0, int(z * 2) / 2)

    def draw(self):
        if not self.proj:
            return
        z = self.scale()
        self.screen.set_image(self.screen_image(), z)
        L = self.sc()["length"]
        self.time_lbl.setText(f" {f2s(self.frame):.2f} 秒 / {self.frame} f  (全 {f2s(L):.2f} 秒) ")
        self.orig_box.setVisible(self.compare.isChecked())
        if self.compare.isChecked():
            self.draw_orig(z)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, self.draw)

    def clip_at(self, f):
        cs = [c for c in sorted(self.sc()["media"], key=lambda c: c["start"]) if c["start"] <= f < c["start"] + c["duration"]]
        return cs[-1] if cs else None

    def draw_orig(self, z):
        clip = self.clip_at(self.frame)
        if not clip or not self.asset(clip["asset"]):
            self.orig_lbl.setText("元の素材 (この位置には素材がありません)")
            self.orig.set_image(None, z)
            self.orig_key = None
            return
        a = self.asset(clip["asset"])
        t = 0.0
        if a.get("kind") != "image":
            vp = a.get("video") or {}
            hold = max(1, round(GBFPS / (vp.get("fps") or 10)))
            fr = self.frames["frames"][min(self.frame, len(self.frames["frames"]) - 1)] if self.frames else None
            vf = fr[2] if fr and fr[2] >= 0 else (self.frame - clip["start"]) // hold
            t = max(0, vf) * hold / GBFPS * (vp.get("speed") or 1)
            if self.playing:
                return
        key = f"{a['id']}|{t:.3f}|{json.dumps(a.get('params'))}|{json.dumps(a.get('video'))}|{z}"
        if key == self.orig_key:
            return
        self.orig_key = key
        self.orig_lbl.setText(f"元の素材: {a.get('name') or a['path']}" + ("" if a.get("kind") == "image" else f" ({t:.2f} 秒)"))
        url = self.url("thumb") + f"&asset={q(a['id'])}&asset_path={q(a['path'])}&t={t}&adjusted=1"

        def done(r):
            if self.orig_key == key:
                pm = core.png_to_pixmap(r.body)
                self.orig.set_image(pm.toImage(), z)
        run_async(self.api.binary, "GET", url, on_done=done, on_error=lambda e: None)

    # ------------------------------------------------------------------
    # 再生
    # ------------------------------------------------------------------
    def play(self):
        if self.playing:
            return self.stop()
        if not self.frames:
            self.log("プレビューを準備中です (変換が終わると再生できます)", "w")
            return
        self.playing = True
        self.play_btn.setText("⏸")
        self.t0 = time.perf_counter()
        self.acc = 0.0
        self.play_timer.start()

    def stop(self):
        self.playing = False
        self.play_btn.setText("▶")
        self.play_timer.stop()
        self.orig_key = None
        self.draw()

    def _tick(self):
        now = time.perf_counter()
        self.acc += (now - self.t0) * GBFPS
        self.t0 = now
        n = int(self.acc)
        if n <= 0:
            return
        self.acc -= n
        self.frame += n
        L = self.sc()["length"]
        if self.frame >= L:
            if self.loop_play.isChecked():
                self.frame = (1 + (self.frame - L) % max(1, L)) if self.sc().get("loop") else 0
            else:
                self.frame = L
                self.stop()
        self.draw()
        self.timeline.update()

    def seek(self, f):
        self.frame = max(0, min(self.sc()["length"], int(round(f))))
        self.draw()
        self.timeline.update()

    def step(self, d):
        self.stop()
        self.seek(self.frame + d)

    def set_zoom(self, z):
        self.zoom = float(z)
        self.timeline.relayout()

    # ------------------------------------------------------------------
    # ハードの制約・診断
    # ------------------------------------------------------------------
    def render_report(self):
        r = self.report
        if not r:
            self.report_lbl.setText(f"<span style='color:{COLORS['muted']}'>{'変換中…' if self.building else 'まだ変換していません'}</span>")
            self.chart.clear()
            return
        mode = {"dmg": "初代", "cgb": "カラー (等速)", "cgb_fast": "カラー (倍速)"}[r["mode"]]
        lines = [f"<b>ROM 約 {-(-r['rom_bytes'] // 1024)} KB ({r['rom_banks']} バンク) &nbsp; {mode}: 転送 {r['bytes_per_update']} バイト/フレーム &nbsp; タイル枠 {r['vram_slots']}</b>"]
        video = None
        for a in r["assets"]:
            if a["kind"] == "image":
                merged = f" <span style='color:{COLORS['warn']}'>(似たタイル {a['merged_tiles']} 枚を統合)</span>" if a["merged_tiles"] else ""
                lines.append(f"🖼 {a['name']}: タイル {a['tiles']}/{a['tile_limit']}{merged}" +
                             (f" · パレット {a['palettes']}/8" if r["target"] == "cgb" else "") +
                             f" · VRAM {a['vram_bytes']} B · 読み込み {a['load_frames']} フレーム ({f2s(a['load_frames']):.2f} 秒) · ROM {a['rom_bytes'] / 1024:.1f} KB")
            else:
                video = video or a
                col = COLORS["ok"] if a["feasible"] else COLORS["warn"]
                raised = (f" · <span style='color:{COLORS['warn']}'>画質を下げた枚数 {a['raised_frames']} (しきい値 {a['threshold']:g}→{a['max_used_threshold']})</span>"
                          if a.get("raised_frames") else "")
                lines.append(f"🎞 {a['name']}: {a['frames']} 枚 · <b style='color:{col}'>目標 {a['target_fps']} FPS → 実際 {a['achieved_fps']} FPS</b> · "
                             f"1 枚平均 {a['avg_frame_bytes']} B (最大 {a['max_frame_bytes']} B) · CPU 最大 {a['max_cpu_percent']}% / 平均 {a['avg_cpu_percent']}% · "
                             f"ROM {a['rom_bytes'] / 1024:.1f} KB{raised}")
        if self.frames:
            lines.append(f"プレビューでの遅れた動画フレーム: {self.frames['late']} (最大 {self.frames['max_lag']} フレーム)"
                         f" <span style='color:{COLORS['muted']}'>— 実機での実測は「✔ ROM 検証」</span>")
        if self.verify_result:
            lines.append(self.verify_result)
        for d in self.diags:
            col = {"error": COLORS["err"], "warn": COLORS["warn"]}.get(d["level"], "#9cc3ff")
            ic = {"error": "✖", "warn": "⚠"}.get(d["level"], "ℹ")
            lines.append(f"<span style='color:{col}'>{ic} {d['text']}</span>" +
                         (f"<br><span style='color:{COLORS['muted']}'>→ {d['fix']}</span>" if d.get("fix") else ""))
        self.report_lbl.setText("<br>".join(lines))
        self.chart.setPixmap(self.bytes_chart(video, r) if video else QPixmap())
        if video:
            self.chart.setToolTip("動画 1 枚ごとの転送量 (棒) と、表示時間内に送れる量 (線)。赤 = 間に合わない枚、灰色 = 先頭 (再生前に読み込む)")

    def bytes_chart(self, a, r) -> QPixmap:
        n = len(a["per_frame_bytes"])
        W, H = min(600, max(120, n * 4)), 40
        pm = QPixmap(W, H)
        pm.fill(QColor("#141518"))
        p = QPainter(pm)
        lims = [r["bytes_per_update"] * h for h in a["per_frame_hold"]]
        cap = max([max(b, l) for i, (b, l) in enumerate(zip(a["per_frame_bytes"], lims)) if i > 0] or [1])
        bw = W / n
        for i, (b, lim) in enumerate(zip(a["per_frame_bytes"], lims)):
            col = "#8b8e96" if i == 0 else "#f85149" if b > lim else "#4c8bf5"
            hh = max(1, min(H - 2, b / cap * (H - 2)))
            p.fillRect(QRect(int(i * bw), int(H - hh), max(1, int(bw) - 1), int(hh)), QColor(col))
            p.fillRect(QRect(int(i * bw), int(H - lim / cap * (H - 2) - 1), max(1, int(bw)), 1), QColor("#d29922"))
        p.end()
        return pm

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------
    def select(self, sel):
        self.sel = sel
        self.timeline.update()
        self.refresh_inspector()

    def set_scene(self, i):
        self.stop()
        self.scene = i
        self.frame = 0
        self.frames = None
        self.sel = None
        self.render_all()
        self.schedule_build(10)

    def add_scene(self):
        name, ok = QInputDialog.getText(self, "シーンを追加", "シーンの名前 (英数字。C から GBM_SCENE_名前 で呼びます)", text=f"scene{len(self.proj['scenes']) + 1}")
        if not ok or not name.strip():
            return

        def add():
            self.proj["scenes"].append({"name": name.strip(), "length": s2f(5), "loop": False, "fade_obj": False, "media": [], "fx": [], "palette": []})
            self.scene = len(self.proj["scenes"]) - 1
            self.frame = 0
            self.frames = None
        self.change(add)

    def rename_scene(self):
        n, ok = QInputDialog.getText(self, "シーンの名前", "シーンの名前", text=self.sc()["name"])
        if ok and n.strip():
            self.change(lambda: self.sc().__setitem__("name", n.strip()))

    def delete_scene(self):
        if len(self.proj["scenes"]) < 2:
            self.log("シーンは 1 つ以上必要です", "w")
            return
        if not ask(self, "削除", f"シーン「{self.sc()['name']}」を削除しますか？ (元に戻すで戻せます)"):
            return

        def rm():
            self.proj["scenes"].pop(self.scene)
            self.scene = 0
            self.frames = None
        self.change(rm)

    def add_clip(self, aid, start):
        a = self.asset(aid)
        if not a:
            return
        dur = s2f(3)
        if a.get("kind") != "image":
            pr = self.probe_asset(a)
            if pr and pr.get("duration"):
                vp = a.get("video") or {}
                ln = ((vp.get("end") or pr["duration"]) - (vp.get("start") or 0)) / (vp.get("speed") or 1)
                if ln > 0:
                    dur = max(1, s2f(ln))

        def add():
            c = {"id": uid("c"), "asset": aid, "start": start, "duration": dur}
            self.sc()["media"].append(c)
            if start + dur > self.sc()["length"]:
                self.sc()["length"] = start + dur
            self.sel = ("clip", c["id"])
        self.change(add)

    def add_fx(self):
        t = self.fx_type.currentData()

        def add():
            f = {"id": uid("f"), "type": t, "start": self.frame,
                 "duration": 12 if t.startswith("flash") else 60 if t in RANDOM_FX else 30}
            if t in RANDOM_FX:
                f.update(intensity=16, period=4 if t == "flicker" else 3, seed=1)
            self.sc()["fx"].append(f)
            self.sel = ("fx", f["id"])
            if f["start"] + f["duration"] > self.sc()["length"]:
                self.sc()["length"] = f["start"] + f["duration"]
        self.change(add)

    def add_key(self):
        lv = self.frames["levels"][min(self.frame, len(self.frames["levels"]) - 1)] if self.frames else 0

        def add():
            k = {"id": uid("k"), "frame": self.frame, "level": lv, "interp": "linear"}
            self.sc()["palette"].append(k)
            self.sel = ("key", k["id"])
        self.change(add)

    def delete_sel(self):
        t = self.sel
        if not t:
            return
        kind, ident = t
        if kind == "asset":
            a = self.asset(ident)
            n = sum(1 for x in self.proj["scenes"] for c in x["media"] if c["asset"] == ident)
            if not ask(self, "素材を外す", f"素材「{a.get('name') or a['path']}」をこのメディアプロジェクトから外しますか？" +
                       (f"\n(タイムライン上のクリップ {n} 個も消えます)" if n else "") + "\n元のファイルは消しません"):
                return

            def rm():
                self.proj["assets"] = [x for x in self.proj["assets"] if x["id"] != ident]
                for x in self.proj["scenes"]:
                    x["media"] = [c for c in x["media"] if c["asset"] != ident]
                self.sel = None
            self.change(rm)
            return
        key = {"clip": "media", "fx": "fx", "key": "palette"}[kind]

        def rm2():
            self.sc()[key] = [x for x in self.sc()[key] if x["id"] != ident]
            self.sel = None
        self.change(rm2)

    def probe_asset(self, a):
        if a["id"] not in self.probe:
            try:
                self.probe[a["id"]] = self.api.get("/api/media/probe", path=self.name, asset_path=a["path"])
            except ApiFail:
                return None
        return self.probe[a["id"]]

    # ---- 取り込み ----
    def pick_files(self):
        fs, _ = QFileDialog.getOpenFileNames(self, "写真・動画を取り込む", "",
                                             "画像・動画 (*.png *.jpg *.jpeg *.bmp *.webp *.gif *.mp4 *.webm *.mov *.mkv *.avi *.m4v);;すべて (*)")
        if fs:
            self.import_files(fs)

    def import_files(self, files):
        for f in files:
            p = Path(f)
            if not p.is_file():
                continue
            try:
                self.log(f"取り込み中: {p.name} ({p.stat().st_size // 1024} KB)")
                r = self.api.call("POST", self.url("import") + f"&name={q(p.name)}", raw=p.read_bytes())
            except ApiFail as e:
                self.log(f"エラー: {e}", "e")
                continue
            a = {"id": uid("a"), "name": p.stem, "path": r["path"], "kind": "image" if r["kind"] == "image" else "video",
                 "params": {"preset": "standard"}}
            if a["kind"] == "video":
                a["video"] = {"fps": 10, "start": 0, "end": 0, "speed": 1, "threshold": 8, "tile_limit": 0, "loop": False, "strict_fps": False}
                a["params"]["dither"] = "bayer4"

            def add(a=a):
                self.proj.setdefault("assets", []).append(a)
                self.sel = ("asset", a["id"])
            self.change(add)
            if a["kind"] == "video":
                pr = self.probe_asset(a)
                if pr and pr.get("error"):
                    self.log(pr["error"], "w")
                elif pr:
                    self.log(f"動画: {pr['width']}x{pr['height']} {pr['fps']:.2f}fps {pr['duration']:.2f} 秒 ({pr['backend']})")

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.import_files([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    # ---- キー ----
    def keyPressEvent(self, e):
        mod = e.modifiers() & Qt.ControlModifier
        if mod and e.key() == Qt.Key_Z:
            self.do_undo(bool(e.modifiers() & Qt.ShiftModifier))
        elif mod and e.key() == Qt.Key_Y:
            self.do_undo(True)
        elif e.key() == Qt.Key_Space:
            self.play()
        elif e.key() == Qt.Key_Left:
            self.step(-10 if e.modifiers() & Qt.ShiftModifier else -1)
        elif e.key() == Qt.Key_Right:
            self.step(10 if e.modifiers() & Qt.ShiftModifier else 1)
        elif e.key() == Qt.Key_Home:
            self.seek(0)
        elif e.key() == Qt.Key_End:
            self.seek(self.sc()["length"])
        elif e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_sel()
        else:
            super().keyPressEvent(e)

    # ------------------------------------------------------------------
    # 書き出し・ROM プレビュー・検証
    # ------------------------------------------------------------------
    def export(self):
        self.save(True)

        def fin(j):
            if j["ok"]:
                self.log("書き出しました。プロジェクトをビルドすると一緒にコンパイルされます", "o")
                self.win.extra.pop("arrays", None)
                self.win.extra.pop("media", None)
                self.win.render_tree()
                self.export_files = (j.get("result") or {}).get("c_files") or []
                self.sel = None
                self.refresh_inspector()
        self.win.run_job(self.url("export"), {}, None, need_gbdk=False, on_finish=fin)

    def demo(self, verify: bool):
        self.save(True)

        def fin(j):
            r = (j.get("result") or {}).get("verify")
            if r:
                ok = r["matched"] == r["compared"] and r["cut"] == 0 and r["overruns"] == 0 and r["late"] == r["sim_late"]
                col = COLORS["ok"] if ok else COLORS["warn"]
                self.verify_result = (f"<span style='color:{col}'>{'✔' if ok else '⚠'} ROM 検証 (シーン「{self.sc()['name']}」): "
                                      f"{r['matched']}/{r['compared']} フレームがプレビューと一致 · 遅れた動画フレーム {r['late']} (最大 {r['max_lag']}) · "
                                      f"転送の打ち切り {r['cut']} · フレーム落ち {r['overruns']}</span>")
                self.render_report()
        self.win.run_job(self.url("demo"), {"scene": self.scene, "run": not verify, "verify": verify},
                         "ROM 検証 (PyBoy)" if verify else "ROM プレビュー", need_gbdk=True, on_finish=fin)

    # ------------------------------------------------------------------
    # インスペクター
    # ------------------------------------------------------------------
    def inspector(self):
        if not self.proj:
            return None
        t = self.sel
        if t:
            kind, ident = t
            if kind == "asset" and self.asset(ident):
                return self.inspect_asset(self.asset(ident))
            s = self.sc()
            for k, key, fn in (("clip", "media", self.inspect_clip), ("fx", "fx", self.inspect_fx), ("key", "palette", self.inspect_key)):
                if kind == k:
                    it = next((x for x in s[key] if x["id"] == ident), None)
                    if it:
                        return fn(it)
        return self.inspect_project()

    def inspect_asset(self, a):
        cgb = self.proj.get("target") == "cgb"
        P, D = self.info["preset_params"], self.info["defaults"]
        p = a.setdefault("params", {})
        cur = dict(D, **P.get(p.get("preset"), {}), **p)

        def setp(k):
            def f(v, live=False):
                self.slider_snapshot(("asset", a["id"], k), live)
                if k == "preset":
                    a["params"] = {"preset": v}
                    if a.get("kind") != "image":
                        a["params"]["dither"] = P.get(v, {}).get("dither", "bayer4")
                else:
                    a["params"][k] = v
                self.changed_media(render=False, build=not live)
                if k == "preset":
                    self.refresh_inspector()
                else:
                    self.prev_timer.start(350)
            return f

        def sld(k, lo, hi, step, fmt=None):
            return Slider(cur[k], lo, hi, step, setp(k), fmt)
        self.quick_box = QVBoxLayout()
        qb = QWidget()
        qb.setLayout(self.quick_box)
        self.quick_box.addWidget(hint("変換プレビューを作っています…"))
        self.quick_asset = a["id"]

        def rename(v):
            self.change(lambda: a.__setitem__("name", v), render=True, build=False)
        rows = [label(("🖼 " if a.get("kind") == "image" else "🎞 ") + (a.get("name") or a["path"]), "title"),
                form(("名前", line(a.get("name") or "", rename), "一覧とタイムラインに出る名前")), hint(a["path"]), qb,
                form(("プリセット", combo(list(self.info["presets"].items()), p.get("preset") or "standard", setp("preset")), TIP["preset"])),
                group("切り抜き (160×144)", form(
                    ("合わせ方", combo([("fill", "いっぱい (はみ出しを切る)"), ("fit", "全体 (余白を足す)"), ("stretch", "引き伸ばす")], cur["fit"], setp("fit")), TIP["fit"]),
                    ("拡大", sld("zoom", 1, 4, 0.05, lambda v: f"{v:.2f}x"), TIP["zoom"]),
                    ("位置 (横)", sld("crop_x", 0, 1, 0.01, lambda v: f"{v:.2f}"), TIP["crop_x"]),
                    ("位置 (縦)", sld("crop_y", 0, 1, 0.01, lambda v: f"{v:.2f}"), TIP["crop_y"]),
                    ("余白", combo([("black", "黒"), ("white", "白")], cur["pad"], setp("pad")), TIP["pad"]) if cur["fit"] == "fit" else None)),
                group("調整", form(
                    ("明るさ", sld("brightness", -100, 100, 1), TIP["brightness"]),
                    ("コントラスト", sld("contrast", -100, 100, 1), TIP["contrast"]),
                    ("ガンマ", sld("gamma", 0.3, 3, 0.05, lambda v: f"{v:.2f}"), TIP["gamma"]),
                    ("黒レベル", sld("black_point", 0, 254, 1), TIP["black_point"]),
                    ("白レベル", sld("white_point", 1, 255, 1), TIP["white_point"]),
                    ("シャープ", sld("sharpen", 0, 3, 0.1, lambda v: f"{v:.1f}"), TIP["sharpen"]),
                    ("ノイズ除去", sld("denoise", 0, 3, 1), TIP["denoise"]),
                    ("彩度", sld("saturation", -100, 100, 1), TIP["saturation"]) if cgb else None,
                    ("白黒にする", check(cur["grayscale"], setp("grayscale")), TIP["grayscale"]) if cgb else None)),
                group("減色", form(
                    ("ディザ", combo([("none", "なし"), ("fs", "Floyd–Steinberg"), ("atkinson", "Atkinson"), ("bayer2", "Bayer 2x2"),
                                     ("bayer4", "Bayer 4x4"), ("bayer8", "Bayer 8x8")], cur["dither"], setp("dither")), TIP["dither"]),
                    ("ディザの強さ", sld("dither_strength", 0, 1, 0.05, lambda v: f"{v:.2f}"), TIP["dither_strength"]),
                    ("パレット数", sld("palettes", 1, 8, 1), TIP["palettes"]) if cgb else None,
                    ("タイル上限", spin(cur["tile_limit"], 0, 512 if cgb else 256, setp("tile_limit")), TIP["tile_limit"])))]
        if a.get("kind") != "image":
            vp = a.setdefault("video", {})

            def setv(k, conv=lambda x: x):
                return lambda v, live=False: (not live) and self.change(lambda: vp.__setitem__(k, conv(v)), render=False)
            pr = self.probe_asset(a)
            hold = max(1, round(GBFPS / (vp.get("fps") or 10)))
            rows.append(group("動画",
                              hint(pr.get("error") if pr and pr.get("error") else
                                   (f"{pr['width']}x{pr['height']} · {pr['fps']:.2f}fps · {pr['duration']:.2f} 秒" if pr else "")),
                              form(("目標 FPS", core.hbox(dspin(vp.get("fps") or 10, 1, 30, 1, setv("fps"), 1),
                                                          label(f"→ 1 枚 {hold} フレーム = {GBFPS / hold:.2f} FPS", "hint", wrap=False)), TIP["fps"]),
                                   ("開始 (秒)", dspin(vp.get("start") or 0, 0, 36000, 0.1, setv("start")), TIP["start"]),
                                   ("終了 (秒)", dspin(vp.get("end") or 0, 0, 36000, 0.1, setv("end")), TIP["end"]),
                                   ("速度", dspin(vp.get("speed") or 1, 0.1, 8, 0.25, setv("speed")), TIP["speed"]),
                                   ("差分しきい値", Slider(vp.get("threshold", 8), 0, 60, 1, setv("threshold")), TIP["threshold"]),
                                   ("1 枚の新タイル上限", spin(vp.get("tile_limit") or 0, 0, 512, setv("tile_limit")), TIP["frame_tile_limit"]),
                                   ("ループ", check(vp.get("loop"), setv("loop")), TIP["loop"]),
                                   ("FPS を必ず守る", check(vp.get("strict_fps"), setv("strict_fps")), TIP["strict_fps"]))))
        rows.append(core.hbox(button("＋ 再生位置に置く", lambda: self.add_clip(a["id"], self.frame), tip="再生位置にこの素材のクリップを置く"),
                              button("素材を外す", self.delete_sel, "danger"), "stretch"))
        self.prev_timer.start(10)
        return core.vbox(*rows)

    def _quick_preview(self):
        aid = getattr(self, "quick_asset", None)
        if not aid or not self.sel or self.sel != ("asset", aid):
            return
        proj = copy.deepcopy(self.proj)

        def done(r):
            if self.sel != ("asset", aid):
                return
            try:
                box = self.quick_box
                while box.count():
                    w = box.takeAt(0).widget()
                    if w:
                        w.deleteLater()
            except RuntimeError:
                return
            imgs = []
            for key, title in (("source", "調整後"), ("converted", "変換結果")):
                pm = QPixmap()
                pm.loadFromData(base64.b64decode(r[key]))
                lb = QLabel()
                lb.setPixmap(pm.scaled(112, 101, Qt.KeepAspectRatio, Qt.FastTransformation))
                imgs.append(core.vbox(label(title, "hint"), lb))
            box.addWidget(core.hbox(*imgs, "stretch"))
            box.addWidget(label(f"タイル {r['tiles']}" + (f" (統合 {r['merged']})" if r["merged"] else "") +
                                f" · VRAM {r['vram_bytes']} B · 読み込み {r['load_frames']} フレーム", "warn" if r["merged"] else "muted"))
            if r.get("palettes"):
                from .convert import swatches
                box.addWidget(swatches([[[c * 8 for c in col] for col in pal] for pal in r["palettes"]], per_row=4))
            for w in r.get("warnings") or []:
                box.addWidget(label(w, "warn"))

        def err(e):
            try:
                self.quick_box.addWidget(label(str(e), "warn"))
            except RuntimeError:
                pass
        run_async(self.api.post, self.url("preview"), {"project": proj, "asset": aid, "t": 0}, on_done=done, on_error=err)

    def _time_fields(self, item, keys):
        s = self.sc()
        rows = []
        for k, title, tip in keys:
            def setv(v, k=k):
                self.change(lambda: item.__setitem__(k, max(1 if k == "duration" else 0, s2f(v))))

            def nudge(d, k=k):
                def f():
                    item[k] = max(1 if k == "duration" else 0, item[k] + d)
                    if item.get("start", 0) + item.get("duration", 0) > s["length"]:
                        s["length"] = item["start"] + item["duration"]
                self.change(f)
            rows.append((title, core.hbox(dspin(round(f2s(item[k]), 2), 0, 3600, 0.05, setv), label(f"秒 = {item[k]} フレーム", "hint", wrap=False),
                                          button("−", lambda k=k: nudge(-1)(), tip="1 フレーム減らす"),
                                          button("＋", lambda k=k: nudge(1)(), tip="1 フレーム増やす")), tip))
        return form(*rows)

    def inspect_clip(self, c):
        a = self.asset(c["asset"])
        rows = [label("映像クリップ", "title"),
                form(("素材", combo([(x["id"], x.get("name") or x["path"]) for x in self.proj["assets"]], c["asset"],
                                   lambda v: self.change(lambda: c.__setitem__("asset", v))), "表示する写真・動画")),
                self._time_fields(c, [("start", "開始", "この時刻に画面が切り替わる (読み込みはその前から始める)"), ("duration", "長さ", "表示する時間")])]
        if a and a.get("kind") != "image":
            def fit():
                pr = self.probe_asset(a)
                if pr and pr.get("duration"):
                    vp = a.get("video") or {}
                    self.change(lambda: c.__setitem__("duration", max(1, s2f(((vp.get("end") or pr["duration"]) - (vp.get("start") or 0)) / (vp.get("speed") or 1)))))
            rows.append(button("動画の長さに合わせる", fit))
        rows.append(hint("前の絵が見えているうちに切り替えると、読み込み中にタイルが重なって乱れることがあります。暗転中に切り替えると確実です。"))
        rows.append(core.hbox(button("素材の変換設定", lambda: self.select(("asset", c["asset"]))), button("削除", self.delete_sel, "danger"), "stretch"))
        return core.vbox(*rows)

    def inspect_fx(self, f):
        t = f["type"]
        rows = [label("演出", "title"),
                form(("種類", combo(list(self.info["fx_types"].items()), t, lambda v: self.change(lambda: f.__setitem__("type", v))),
                      "パレットだけで行う演出 (LCD は止めない)。初代は BGP の 4 階調、カラーは 31 段階")),
                self._time_fields(f, [("start", "開始", ""), ("duration", "長さ", "フェードにかけるフレーム数")])]
        if t in ("flash_white", "flash_black"):
            rows.append(form(("戻りのフレーム数", spin(f.get("decay", 0), 0, 600, lambda v: self.change(lambda: f.__setitem__("decay", max(0, v)))),
                              "最後の何フレームで元の明るさに戻すか (0 = すぐ戻る)")))
        if t in RANDOM_FX:
            rows.append(form(
                ("強さ", Slider(f.get("intensity", 16), 1, 31, 1, lambda v, live: self._fx_intensity(f, v, live)),
                 "明るさの変化の大きさ (1〜31)"),
                ("間隔 (フレーム)", spin(f.get("period", 4), 1, 120, lambda v: self.change(lambda: f.__setitem__("period", max(1, v)))), "何フレームごとに明るさを変えるか"),
                ("乱数の種", spin(f.get("seed", 1), 0, 99999, lambda v: self.change(lambda: f.__setitem__("seed", v))),
                 "変えると違う揺らぎ方になる (プレビューと ROM は同じ)") if t != "flicker" else None))
        if self.proj.get("target") == "dmg":
            rows.append(hint("初代は 4 階調なので、明るさは 31/3 ごとの段階で変わります (パレット段階変化)。"))
        rows.append(button("削除", self.delete_sel, "danger"))
        return core.vbox(*rows)

    def inspect_key(self, k):
        return core.vbox(
            label("パレットのキーフレーム", "title"),
            form(("フレーム", spin(k["frame"], 0, 216000, lambda v: self.change(lambda: k.__setitem__("frame", max(0, v)))), "このフレームでこの明るさになる"),
                 ("明るさ", Slider(k["level"], -31, 31, 1, lambda v, live: self._key_level(k, v, live)),
                  "-31 = 真っ黒、0 = 元の色、+31 = 真っ白"),
                 ("次までの変化", combo([("linear", "少しずつ (linear)"), ("step", "そのまま (step)")], k.get("interp") or "linear",
                                       lambda v: self.change(lambda: k.__setitem__("interp", v))),
                  "linear = 次のキーフレームまで少しずつ変える / step = 次まで同じ")),
            button("削除", self.delete_sel, "danger"))

    def _key_level(self, k, v, live):
        self.slider_snapshot(("key", k["id"]), live)
        k["level"] = v
        if live:
            self.timeline.update()
        else:
            self.changed_media(render=False)

    def _fx_intensity(self, f, v, live):
        self.slider_snapshot(("fx", f["id"]), live)
        f["intensity"] = v
        if not live:
            self.changed_media(render=False)

    def inspect_project(self):
        p = self.proj
        out = p.setdefault("output", {})
        st = p.setdefault("settings", {})
        dir_ = out.get("dir") or "gbmedia"
        scn = "".join(ch if ch.isalnum() else "_" for ch in self.sc()["name"]).upper()
        rows = [label("🎞 " + p.get("name", ""), "title"),
                hint("クリップ・演出・キーフレームを選ぶとここで編集できます。素材は左の一覧から選びます。"),
                group("書き出し", form(
                    ("出力先", line(dir_, lambda v: self.change(lambda: out.__setitem__("dir", v.strip() or "gbmedia"), build=False), mono=True),
                     "C ソースを書き出すフォルダ (ソースの場所からの相対パス)。GBForge のビルドでは自動的にコンパイルされます"),
                    ("バンク配置", combo([("auto", "自動 (-autobank)"), ("fixed", "固定バンク")], out.get("bank_mode") or "auto",
                                        lambda v: self.change(lambda: out.__setitem__("bank_mode", v), build=False)),
                     "auto = #pragma bank 255 (lcc -autobank でリンク。GBForge のビルドはこれ) / fixed = 決まったバンク番号 (-autobank を使わない build.bat 用)"),
                    ("最初のバンク", spin(out.get("first_bank") or 2, 1, 511, lambda v: self.change(lambda: out.__setitem__("first_bank", v), build=False)),
                     "データを置き始めるバンク番号 (ゲームの他のデータと重ならない番号に)") if out.get("bank_mode") == "fixed" else None),
                      script_add_box(self.win, self.export_files)),
                group("ツール", form(
                    ("ffmpeg", line(st.get("ffmpeg") or "", lambda v: self.change(lambda: st.__setitem__("ffmpeg", v.strip()), render=False),
                                    placeholder=self.info.get("ffmpeg") or "C:\\ffmpeg\\bin\\ffmpeg.exe", mono=True),
                     f"動画の読み込みに使う ffmpeg.exe の場所 (空 = 自動で探す)。現在: {self.info.get('ffmpeg') or '見つかりません — winget install Gyan.FFmpeg'}"),
                    ("エミュレーター", line(st.get("emulator") or "", lambda v: self.change(lambda: st.__setitem__("emulator", v.strip()), render=False, build=False),
                                         placeholder="sameboy.exe / bgb.exe / mGBA.exe {rom}", mono=True),
                     "ROM プレビューで起動するエミュレーター (空 = プロジェクト設定のエミュレーター)。例: C:\\SameBoy\\sameboy.exe / C:\\bgb\\bgb.exe / \"C:\\mGBA\\mGBA.exe\" {rom}"))),
                group("使い方 (C)", label(f'#include "{dir_}/gbmedia.h"\n#include "{dir_}/gbmedia_data.h"\n\ngbmedia_init();\n'
                                         f"gbmedia_load_scene(GBM_SCENE_{scn});\ngbmedia_play();   // 最後まで再生", "hint"))]
        return core.vbox(*rows)
