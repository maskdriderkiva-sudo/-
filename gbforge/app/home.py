"""スタート画面: 自分の GBDK プロジェクトの一覧 (自動で探す)、最近開いたもの、新しいプロジェクト、既存フォルダの取り込み。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QScrollArea, QVBoxLayout, QWidget)

from . import core
from .core import button, check, combo, error, group, hint, label, line, run_async
from .local import ApiFail


class AdoptDialog(QDialog):
    """project.gbproj の無い GBDK フォルダを開く時の確認"""

    def __init__(self, parent, info: dict):
        super().__init__(parent)
        self.setWindowTitle("既存の GBDK フォルダを開く")
        self.setMinimumWidth(620)
        lay = QVBoxLayout(self)
        lay.addWidget(label("既存の GBDK フォルダを開く", "title"))
        lay.addWidget(hint("このフォルダに project.gbproj を 1 つ追加するだけで、ソースや build.bat はそのまま使います。"))
        lay.addWidget(label(f"<b>{info['name']}</b><br><span style='color:#8b8e96'>{info['path']}</span>"))
        lay.addWidget(label(f"C ファイル {info['c_files']} / ヘッダー {info['h_files']} / PNG {info['png_files']} / "
                            f"ROM: {', '.join(info['roms']) or 'なし'}"))
        lay.addWidget(label(f"GBDK を発見: {info['gbdk']}" if info.get("gbdk") else
                            "GBDK が見つかりません (同梱/自動セットアップを使います)", None if info.get("gbdk") else "warn"))
        self.win = line(info.get("build_script_windows") or "", mono=True)
        self.unix = line(info.get("build_script_unix") or "", mono=True)
        self.out = line(info.get("output") or "", mono=True)
        self.gbdk = line(info.get("gbdk") or "", mono=True)
        self.target = combo([("dmg", "初代"), ("both", "初代 + カラー"), ("cgb", "カラー専用")], info.get("target") or "dmg")
        lay.addWidget(core.form(("Windows のビルドスクリプト", self.win, "今まで使っていた build.bat など"),
                                ("Mac / Linux のビルドスクリプト", self.unix),
                                ("できる ROM", self.out, "ビルドスクリプトが作る ROM のファイル名"),
                                ("GBDK のフォルダ", self.gbdk),
                                ("対象", self.target)))
        lay.addWidget(core.hbox("stretch", button("キャンセル", self.reject), button("このフォルダを開く", self.accept, "play")))

    def values(self) -> dict:
        return {"build_script_windows": self.win.text(), "build_script_unix": self.unix.text(), "output": self.out.text(),
                "gbdk": self.gbdk.text(), "target": self.target.currentData()}


class HomePage(QScrollArea):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.api = win.api
        self.setWidgetResizable(True)
        box = QWidget()
        self.setWidget(box)
        outer = QHBoxLayout(box)
        outer.addStretch(1)
        col = QWidget()
        col.setMaximumWidth(1000)
        col.setMinimumWidth(640)
        outer.addWidget(col, 4)
        outer.addStretch(1)
        lay = QVBoxLayout(col)
        lay.setSpacing(12)
        t = QLabel("▣ GBForge")
        t.setStyleSheet("font-size: 26pt; font-weight: bold; color: #88c070;")
        lay.addWidget(t)
        lay.addWidget(label("ゲームボーイ用ゲームエンジン — GBDK-2020 / 日本語フォント / ロムライタ / GB Media Studio 統合環境", "muted"))
        self.status_row = QHBoxLayout()
        sw = QWidget()
        sw.setLayout(self.status_row)
        lay.addWidget(sw)

        # ---- 自分のプロジェクト ----
        self.groups = QVBoxLayout()
        gw = QWidget()
        gw.setLayout(self.groups)
        lay.addWidget(group("あなたの GBDK プロジェクト",
                            hint("GBDK の Project フォルダ (例: C:\\gbdk-win64\\gbdk\\Project) を自動で探して、同じゲームの版 "
                                 "(v0.3 … v2.0) をまとめて表示します。「開く」を押すだけでリンクできます。"),
                            core.hbox(button("📂 フォルダを選んで開く…", self.browse, "play"), button("再検索", self.scan), "stretch"),
                            gw))

        # ---- 最近 / 新規 ----
        cols = QHBoxLayout()
        self.recent = QListWidget()
        self.recent.setMinimumHeight(180)
        self.recent.itemActivated.connect(lambda it: self.open_path(it.data(Qt.UserRole)))
        self.recent.itemClicked.connect(lambda it: it.data(Qt.UserRole) and self.open_path(it.data(Qt.UserRole)))
        self.path_in = line(placeholder="例: C:\\gbdk-win64\\gbdk\\Project\\HighwayStar_GBC_v2.0", mono=True)
        self.path_in.returnPressed.connect(lambda: self.open_path(self.path_in.text().strip()))
        left = group("最近開いたもの", self.recent,
                     hint("パスを直接入力して開く"), core.hbox(self.path_in, button("開く", lambda: self.open_path(self.path_in.text().strip()))))
        self.new_type = combo([("engine", "ゲームエンジン (シーン・アクター・会話をエディタで作る)"),
                               ("c", "C プログラム (GBDK + 日本語フォント gbjp で自由に書く)")], "engine", lambda v: self._type_changed())
        self.new_target = combo([("both", "初代 + カラー対応"), ("dmg", "初代ゲームボーイ"), ("cgb", "ゲームボーイカラー専用")], "both")
        self.new_name = line("My Game")
        self.new_path = line(mono=True)
        self.new_sample = check(True, text="サンプル (町・家・会話・2択・セーブ) を入れる")
        self.f_new = core.form(("種類", self.new_type), ("対象ハード", self.new_target), ("名前", self.new_name),
                               ("保存先フォルダ", core.hbox(self.new_path, button("…", self._pick_new_dir))), self.new_sample)
        right = group("新しいプロジェクト", self.f_new, button("作成して開く", self.create, "play"))
        cols.addWidget(left, 1)
        cols.addWidget(right, 1)
        cw = QWidget()
        cw.setLayout(cols)
        lay.addWidget(cw)
        lay.addStretch(1)
        self._type_changed()

    # ------------------------------------------------------------------
    def _type_changed(self):
        c = self.new_type.currentData() == "c"
        self.new_target.setEnabled(c)
        self.new_sample.setVisible(not c)

    def _pick_new_dir(self):
        d = QFileDialog.getExistingDirectory(self, "保存先の親フォルダを選ぶ", str(Path(self.new_path.text()).parent))
        if d:
            self.new_path.setText(str(Path(d) / (self.new_name.text().strip() or "MyGame")))

    def set_status(self, st: dict):
        while self.status_row.count():
            w = self.status_row.takeAt(0).widget()
            if w:
                w.deleteLater()
        chips = [("chip-ok" if st["gbdk"]["installed"] else "chip-ng", "✔ GBDK" if st["gbdk"]["installed"] else "✘ GBDK 未セットアップ (クリックでセットアップ)"),
                 ("chip-ok" if st["writer"] else "chip-idle", ("✔ ロムライタ " + st["writer"][0]["device"]) if st["writer"] else "○ ロムライタ未接続"),
                 ("chip-ok" if st["emulator"] else "chip-idle", ("✔ エミュレータ " + st["emulator"]) if st["emulator"] else "○ エミュレータなし")]
        for i, (role, text) in enumerate(chips):
            lb = QLabel(text)
            lb.setProperty("role", role)
            if i == 0 and not st["gbdk"]["installed"]:
                lb.setCursor(Qt.PointingHandCursor)
                lb.mousePressEvent = lambda e: self.win.install_gbdk()
            self.status_row.addWidget(lb)
        self.status_row.addStretch(1)

    def refresh(self):
        self.scan()
        try:
            r = self.api.get("/api/home")
        except ApiFail as e:
            self.win.log(str(e), "e")
            return
        self.recent.clear()
        if not r["recent"]:
            it = QListWidgetItem("まだありません")
            it.setFlags(Qt.NoItemFlags)
            self.recent.addItem(it)
        for x in r["recent"]:
            it = QListWidgetItem(f"{x.get('name') or x['path']}\n{x['path']}")
            it.setData(Qt.UserRole, x["path"])
            it.setToolTip(x["path"])
            self.recent.addItem(it)
        if not self.new_path.text():
            self.new_path.setText(str(Path(r["default_dir"]) / "MyGame"))

    def scan(self):
        self._clear_groups()
        self.groups.addWidget(label("探しています…", "muted"))
        run_async(self.api.get, "/api/projects/scan", on_done=self._scanned,
                  on_error=lambda e: (self._clear_groups(), self.groups.addWidget(label(str(e), "err"))))

    def _clear_groups(self):
        while self.groups.count():
            w = self.groups.takeAt(0).widget()
            if w:
                w.deleteLater()

    def _scanned(self, r):
        self._clear_groups()
        if not r["roots"]:
            self.groups.addWidget(label("GBDK のプロジェクトフォルダが見つかりませんでした。「📂 フォルダを選んで開く…」から選んでください。", "muted"))
            return
        for root in r["roots"]:
            self.groups.addWidget(label("📁 " + root["root"], "muted"))
            for g in root["groups"]:
                row = QFrame()
                row.setStyleSheet("QFrame { background: #232428; border-radius: 6px; }")
                hl = QHBoxLayout(row)
                nm = QLabel(g["name"])
                nm.setStyleSheet("font-weight: bold;")
                latest = g["latest"]
                lt = QLabel("最新: " + latest["folder"])
                lt.setToolTip(latest["path"])
                hl.addWidget(nm, 1)
                hl.addWidget(lt)
                hl.addWidget(button("開く", lambda p=latest["path"]: self.open_path(p), "play"))
                if len(g["versions"]) > 1:
                    sel = QComboBox()
                    sel.addItem(f"全 {len(g['versions'])} 版から選ぶ…", None)
                    for v in reversed(g["versions"]):
                        sel.addItem(f"{v['folder']}{' (中のフォルダ)' if v.get('nested') else ''}{' ★' if v.get('is_project') else ''}", v["path"])
                    sel.activated.connect(lambda i, s=sel: s.currentData() and self.open_path(s.currentData()))
                    hl.addWidget(sel)
                self.groups.addWidget(row)

    # ------------------------------------------------------------------
    def browse(self):
        d = QFileDialog.getExistingDirectory(self.win, "GBDK のプロジェクトフォルダを選ぶ", str(Path.home()))
        if not d:
            return
        # 親フォルダを「プロジェクトを探す場所」として覚える (X\X は X の親)
        parent = Path(d).parent
        if parent.name == Path(d).name:
            parent = parent.parent
        try:
            self.api.post("/api/project_roots", {"path": str(parent)})
        except ApiFail:
            pass
        self.open_path(d)

    def open_path(self, path: str):
        if not path:
            return
        try:
            r = self.api.post("/api/open", {"path": path})
        except ApiFail as e:
            error(self.win, str(e))
            return
        if r.get("needs_adopt"):
            dlg = AdoptDialog(self.win, r["info"])
            if dlg.exec() != QDialog.Accepted:
                return
            try:
                self.api.post("/api/adopt", dict(dlg.values(), path=r["info"]["path"]))
            except ApiFail as e:
                error(self.win, str(e))
                return
        self.win.enter_project()

    def create(self):
        try:
            self.api.post("/api/create", {"path": self.new_path.text().strip(), "name": self.new_name.text().strip(),
                                          "sample": self.new_sample.isChecked(), "type": self.new_type.currentData(),
                                          "target": self.new_target.currentData()})
        except ApiFail as e:
            error(self.win, str(e))
            return
        self.win.enter_project()
