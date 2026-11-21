"""画像アセットの表示 (スプライト・背景・res の PNG・プロジェクト内の画像)。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QGridLayout, QLabel, QScrollArea, QToolBar, QVBoxLayout, QWidget

from ..core import ask, button, gb_image, hint, label, png_to_pixmap
from ..local import ApiFail, q
from ..view import View, register


class _ImageView(View):
    icon = "🖼"

    def __init__(self, win, name):
        super().__init__(win, name)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.tb = QToolBar()
        self.lbl = label("", "title", wrap=False)
        self.tb.addWidget(self.lbl)
        lay.addWidget(self.tb)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        lay.addWidget(self.area, 1)

    def title(self):
        return self.name.split("/")[-1]

    def set_body(self, *widgets):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(12, 12, 12, 12)
        for x in widgets:
            if x is not None:
                v.addWidget(x)
        v.addStretch(1)
        self.area.setWidget(w)

    def big(self, pm: QPixmap, z: int) -> QLabel:
        lb = QLabel()
        lb.setPixmap(pm.scaled(pm.width() * z, pm.height() * z, Qt.KeepAspectRatio, Qt.FastTransformation))
        lb.setAlignment(Qt.AlignLeft)
        return lb

    def delete_button(self, fn):
        self.tb.addWidget(button("削除", fn, "danger"))


@register("sprite")
class SpriteView(_ImageView):
    icon = "🧍"

    def load(self):
        self.lbl.setText(f"スプライト: {self.name}  ")
        self.delete_button(self.delete)
        si = next((s for s in self.win.proj["sprites"] if s["name"] == self.name), None)
        data = self.api.binary("GET", f"/asset/sprites/{q(self.name)}.png").body
        pm = QPixmap.fromImage(gb_image(data, "sprite"))
        frames = si["frames"] if si else 0
        labels = ["下", "上", "左", "右"] if frames == 4 else ["下1", "下2", "上1", "上2", "左1", "左2", "右1", "右2"] if frames == 8 else []
        grid = QWidget()
        g = QGridLayout(grid)
        for f in range(frames):
            g.addWidget(self.big(pm.copy(f * 16, 0, 16, 16), 4), 0, f)
            g.addWidget(label(f"#{f} {labels[f] if f < len(labels) else ''}", "muted"), 1, f)
        self.set_body(label(si["error"], "err") if si and si.get("error") else None,
                      hint(f"{frames} フレーム。1 フレーム 16×16、横に並べた PNG。透明部分は透過。色は 白 / 濃い灰 / 黒 の 3 色に変換されます。"),
                      grid)

    def delete(self):
        if ask(self, "削除", f"{self.name} を削除しますか？"):
            self.api.delete(f"/api/asset/sprite/{q(self.name)}")
            self.win.load_project()
            self.win.close_view(self)


@register("background")
class BackgroundView(_ImageView):
    def load(self):
        self.lbl.setText(f"背景: {self.name}  ")
        self.delete_button(self.delete)
        bg = next((b for b in self.win.proj["backgrounds"] if b["file"] == self.name), {"width": 0, "height": 0})
        data = self.api.binary("GET", f"/asset/backgrounds/{q(self.name)}").body
        self.set_body(hint(f"{bg['width']}×{bg['height']}px ({bg['width'] // 8}×{bg['height'] // 8} タイル)。4 階調に変換したプレビュー。"
                           "ユニークタイル 128 個まで。"),
                      self.big(QPixmap.fromImage(gb_image(data, "bg")), 2))

    def delete(self):
        if ask(self, "削除", f"{self.name} を削除しますか？"):
            self.api.delete(f"/api/asset/background/{q(self.name)}")
            self.win.load_project()
            self.win.close_view(self)


@register("res")
class ResView(_ImageView):
    def load(self):
        self.lbl.setText(self.name + "  ")
        self.delete_button(self.delete)
        stem = self.name.split("/")[-1].rsplit(".", 1)[0]
        url = "/asset/" + self.name.replace("res/sprites/", "res_sprites/").replace("res/backgrounds/", "res_backgrounds/") \
            .replace("res/tiles/", "res_tiles/")
        pm = png_to_pixmap(self.api.binary("GET", url).body)
        if "/sprites/" in self.name:
            txt = (f'16×16 メタスプライト (8×16 モード)。コード: #include "{stem}.h" → set_sprite_data(0, {stem}_TILE_COUNT, {stem}_tiles); '
                   f"move_metasprite_ex({stem}_metasprites[0], 0, 0, 0, x, y);")
        else:
            txt = (f'背景マップ。コード: #include "{stem}.h" → set_bkg_data(0, {stem}_TILE_COUNT, {stem}_tiles); '
                   f"set_bkg_tiles(0, 0, {stem}_WIDTH / 8, {stem}_HEIGHT / 8, {stem}_map);")
        self.set_body(hint("ビルド時に GBDK の png2asset で自動変換されます。" + txt), self.big(pm, 3))

    def delete(self):
        if ask(self, "削除", f"{self.name} を削除しますか？"):
            self.api.call("DELETE", "/api/res?path=" + q(self.name))
            self.win.load_project()
            self.win.close_view(self)


@register("image")
class ImageView(_ImageView):
    def load(self):
        self.lbl.setText("🖼 " + self.name + "  ")
        pm = png_to_pixmap(self.api.binary("GET", "/file?path=" + q(self.name)).body)
        self.set_body(hint(f"{pm.width()}×{pm.height()}px"), self.big(pm, 4),
                      button("🧰 変換ツールで開く (スプライト / タイル / マップへ)", self.to_convert, "play"))

    def to_convert(self):
        self.win.extra["cv_preset"] = self.name
        v = self.win.open_tab("convert", "convert")
        if v:
            v.load()

    def inspector(self):
        return hint("「変換ツールで開く」で GB / GBC 用の C データ (.c / .h) に変換できます。")
