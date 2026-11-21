"""プロジェクト設定 (ゲーム・ビルド・ロムライタ)。"""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QPlainTextEdit, QScrollArea, QToolBar, QVBoxLayout, QWidget

from ..core import button, check, combo, form, group, hint, label, line, run_async
from ..local import ApiFail
from ..view import View, register

FONT_LABEL = {"misaki_gothic": "美咲ゴシック", "misaki_mincho": "美咲明朝"}


@register("settings")
class SettingsView(View):
    icon = "⚙"

    def __init__(self, win, name):
        super().__init__(win, name)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        tb = QToolBar()
        tb.addWidget(label("プロジェクト設定  ", "title", wrap=False))
        tb.addWidget(button("保存", lambda: self.save(False), "play"))
        lay.addWidget(tb)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        lay.addWidget(self.area, 1)
        self.f: dict = {}

    def title(self):
        return "設定"

    def load(self):
        p = self.win.proj
        s = p["settings"]
        c = p["type"] == "c"
        b, fl, jp, ide = s.get("build", {}), s.get("flasher", {}), s.get("jpfont", {}), s.get("ide", {})
        f = self.f
        f["name"] = line(s.get("name", ""))
        f["rom_title"] = line(s.get("rom_title", ""), mono=True)
        f["rom_title"].setMaxLength(11)
        f["target"] = combo([("dmg", "初代ゲームボーイ"), ("both", "初代 + カラー対応"), ("cgb", "ゲームボーイカラー専用")], s.get("target", "dmg"))
        f["start_scene"] = combo(p["scenes"], s.get("start_scene"))
        f["font"] = combo([(n, FONT_LABEL.get(n, n)) for n in p["fonts"]], s.get("font"))
        f["jpfont.mode"] = combo([("scan", "ソースで使っている文字だけ (小さい)"), ("full", "全文字 約7000字 (約70KB)")], jp.get("mode", "scan"))
        f["jpfont.extra_chars"] = line(jp.get("extra_chars", ""))
        f["variables"] = QPlainTextEdit("\n".join(s.get("variables") or []))
        f["variables"].setMaximumHeight(90)
        f["ide.editor"] = combo([("auto", "自動 (Visual Studio → VS Code)"), ("visualstudio", "Visual Studio"), ("vscode", "VS Code")], ide.get("editor", "auto"))
        f["build.mode"] = combo([("gbforge", "GBForge でコンパイル"), ("script", "今までのビルドスクリプトを実行 (build.bat など)")], b.get("mode", "gbforge"))
        f["build.script_windows"] = line(b.get("script_windows", ""), placeholder="build.bat", mono=True)
        f["build.script_unix"] = line(b.get("script_unix", ""), placeholder="build.sh / make", mono=True)
        f["build.output"] = line(b.get("output", ""), placeholder="game.gb", mono=True)
        f["stages_output"] = line(s.get("stages_output", ""), placeholder="stage_data", mono=True)
        f["build.gbdk_home"] = line(b.get("gbdk_home", ""), placeholder="C:\\gbdk  /  ~/gbdk", mono=True)
        f["build.emulator"] = line(b.get("emulator", ""), placeholder="例: C:\\bgb\\bgb.exe  /  sameboy {rom}", mono=True)
        f["flasher.port"] = QComboBox()
        f["flasher.baud"] = combo([125000, 187500, 375000], int(fl.get("baud", 187500)))
        f["flasher.mbc"] = combo(["MBC5", "MBC1", "MBC3", "AUTO"], fl.get("mbc", "MBC5"))
        f["flasher.algorithm"] = combo(["ALG16", "ALG12", "ALG16WR", "ALG12_M29F"], fl.get("algorithm", "ALG16"))
        f["flasher.wait_mode"] = combo(["TOGGLE", "DATA_POLL", "DEFAULT", "LONGER"], fl.get("wait_mode", "TOGGLE"))
        f["flasher.erase_before_write"] = check(fl.get("erase_before_write"), text="書き込み前に全消去")
        f["flasher.verify"] = check(fl.get("verify"), text="書き込み後にベリファイ")
        self.cart = label("", "muted")
        game = group("ゲーム",
                     form(("名前", f["name"]), ("ROM タイトル (英数 11 文字)", f["rom_title"]),
                          ("対象ハード", f["target"]) if c else None,
                          ("開始シーン", f["start_scene"]) if not c else None,
                          ("日本語フォント", f["font"]),
                          ("フォントに入れる文字", f["jpfont.mode"]) if c else None,
                          ("追加で入れる文字", f["jpfont.extra_chars"], "実行時に組み立てる文字列用") if c else None,
                          ("グローバル変数 (1行1つ)", f["variables"], "スクリプトで vars[VAR_名前]") if not c else None,
                          ("コードエディタ", f["ide.editor"])))
        build = group("ビルド",
                      form(("ビルド方式", f["build.mode"]) if c else None,
                           ("Windows のビルドスクリプト", f["build.script_windows"]) if c else None,
                           ("Mac / Linux のビルドスクリプト", f["build.script_unix"]) if c else None,
                           ("できる ROM (スクリプト方式)", f["build.output"]) if c else None,
                           ("ステージの出力ファイル名", f["stages_output"]) if c else None,
                           ("GBDK-2020 のフォルダ", f["build.gbdk_home"], "空なら GBDK_HOME / PATH / 自動セットアップしたもの"),
                           ("エミュレータ", f["build.emulator"], "空なら PyBoy。SameBoy / BGB / mGBA などのパス。{rom} で ROM の位置を指定できます")))
        flash = group("ロムライタ (CUBIC STYLE / GB Cart Flasher)",
                      form(("ポート", _row(f["flasher.port"], button("再検出", self.refresh_ports))),
                           ("ボーレート", f["flasher.baud"]), ("MBC", f["flasher.mbc"]),
                           ("フラッシュアルゴリズム", f["flasher.algorithm"]), ("完了検出", f["flasher.wait_mode"]),
                           f["flasher.erase_before_write"], f["flasher.verify"],
                           _row(button("カートリッジ情報を読む", self.cart_info), self.cart)))
        w = QWidget()
        v = QVBoxLayout(w)
        for x in (game, build, flash):
            v.addWidget(x)
        v.addStretch(1)
        self.area.setWidget(w)
        self.refresh_ports()

    def refresh_ports(self):
        cur = self.win.proj["settings"].get("flasher", {}).get("port", "")

        def done(ports):
            box = self.f["flasher.port"]
            box.clear()
            box.addItem("(自動検出)", "")
            for p in ports:
                box.addItem(("★ " if p.get("likely") else "") + p["device"] + " " + p.get("description", ""), p["device"])
            if cur and not any(p["device"] == cur for p in ports):
                box.addItem(cur + " (未接続)", cur)
            i = box.findData(cur)
            box.setCurrentIndex(max(0, i))
        run_async(self.api.get, "/api/ports", on_done=done, on_error=lambda e: done([]))

    def cart_info(self):
        self.cart.setText("読み込み中…")
        port = self.f["flasher.port"].currentData()

        def done(i):
            self.cart.setText(f"FW {i['firmware']} / {i['flash_maker']} {i['flash_device_id']} / {i.get('title') or '(空)'} {i['cart_type']}")

        def err(e):
            self.cart.setText("")
            self.log(f"エラー: {e}", "e")
        run_async(self.api.post, "/api/cart/info", {"port": port} if port else {}, on_done=done, on_error=err)

    def save(self, quiet=True):
        out = {"build": {}, "flasher": {}, "jpfont": {}, "ide": {}}
        for k, w in self.f.items():
            if not w.isVisibleTo(self) and w.parent() is None:
                continue
            if isinstance(w, QPlainTextEdit):
                v = [x for x in w.toPlainText().split() if x]
            elif isinstance(w, QComboBox):
                v = w.currentData()
            elif hasattr(w, "isChecked"):
                v = w.isChecked()
            else:
                v = w.text()
            if v is None:
                continue
            a, _, b = k.partition(".")
            if b:
                out[a][b] = v
            else:
                out[a] = v
        try:
            self.api.put("/api/project", out)
        except ApiFail as e:
            self.log(f"エラー: {e}", "e")
            return
        self.win.extra.pop("glyphs", None)
        self.log("設定を保存しました", "o")
        self.win.load_project()

    def inspector(self):
        return hint("保存すると project.gbproj に書き込みます。ビルドスクリプト方式では、今までの build.bat をそのまま実行します。")


def _row(*ws):
    from PySide6.QtWidgets import QHBoxLayout
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    for x in ws:
        lay.addWidget(x)
    return w
