"""バージョン管理 (フォルダごとの版: _v1.9 → _v2.0)。"""
from __future__ import annotations

import time

from PySide6.QtWidgets import QHeaderView, QTableWidget, QTableWidgetItem, QToolBar, QVBoxLayout

from ..core import button, check, combo, group, hint, label, line
from ..local import ApiFail
from ..view import View, register


@register("versions")
class VersionsView(View):
    icon = "🕘"

    def __init__(self, win, name):
        super().__init__(win, name)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        tb = QToolBar()
        tb.addWidget(label("バージョン (フォルダごとの版)", "title", wrap=False))
        lay.addWidget(tb)
        self.name_in = line(mono=True)
        self.suggest = combo([], None, lambda v: v and self.name_in.setText(v))
        self.zip = check(True, text="zip も作る (今までと同じ名前の付け方: 例 HighwayStar_GBC_v2.1.zip)")
        self.open_new = check(True, text="保存後に新しいバージョンを開く")
        lay.addWidget(group("新しいバージョンとして保存",
                            hint("今のフォルダを丸ごとコピーして次の版のフォルダを作ります (今までの「_v1.9 → _v2.0」と同じ運用)。zip も一緒に作れます。"),
                            _row(self.name_in, self.suggest), self.zip, self.open_new, _row(button("保存", self.save_version, "play"), "stretch")))
        lay.addWidget(label("同じプロジェクトの版", "title"))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["名前", "版", "更新日時", ""])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        lay.addWidget(self.table, 1)

    def title(self):
        return "バージョン"

    def load(self):
        r = self.api.get("/api/versions")
        self.suggest.blockSignals(True)
        self.suggest.clear()
        for s in r["suggest"]:
            self.suggest.addItem(s, s)
        self.suggest.blockSignals(False)
        self.name_in.setText(r["suggest"][0] if r["suggest"] else "")
        vs = list(reversed(r["versions"]))
        self.table.setRowCount(len(vs))
        for i, v in enumerate(vs):
            self.table.setItem(i, 0, QTableWidgetItem(("▶ " if v.get("current") else "") + v["name"]))
            self.table.setItem(i, 1, QTableWidgetItem(("v" + v["version"]) if v.get("version") else ""))
            self.table.setItem(i, 2, QTableWidgetItem(time.strftime("%Y/%m/%d %H:%M", time.localtime(v["mtime"]))))
            if v.get("current"):
                self.table.setItem(i, 3, QTableWidgetItem("(開いています)"))
            else:
                self.table.setCellWidget(i, 3, button("開く", lambda p=v["path"]: self.open_version(p)))

    def open_version(self, path):
        self.win.save_all(True)
        self.win.home.open_path(path)

    def save_version(self):
        name = self.name_in.text().strip()
        if not name:
            return
        self.win.save_all(True)
        try:
            res = self.api.post("/api/versions", {"name": name, "zip": self.zip.isChecked(), "open": self.open_new.isChecked()})
        except ApiFail as e:
            self.log(f"エラー: {e}", "e")
            return
        self.log(f"新しいバージョンを作りました: {res['path']}" + (f" / {res['zip']}" if res.get("zip") else ""), "o")
        if self.open_new.isChecked():
            self.win.enter_project()
        else:
            self.load()

    def inspector(self):
        return hint("同じフォルダにある「名前_v番号」のフォルダを同じプロジェクトの版として一覧します。")


def _row(*ws):
    from PySide6.QtWidgets import QHBoxLayout, QWidget
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    for x in ws:
        if x == "stretch":
            lay.addStretch(1)
        else:
            lay.addWidget(x)
    return w
