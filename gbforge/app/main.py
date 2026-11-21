"""GBForge ネイティブアプリのメインウィンドウ。

Unity のように、左にプロジェクト・ヒエラルキー、中央にタブ (シーン・コード・ステージ・メディアなど)、
右にインスペクター、下にコンソールを置く。パネルはドラッグで並べ替え・切り離しできる。
"""
from __future__ import annotations

import html
import sys
import time
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QFont, QIcon, QKeySequence
from PySide6.QtWidgets import (QApplication, QDialog, QDockWidget, QHBoxLayout, QLabel, QMainWindow, QMenu,
                               QMessageBox, QProgressBar, QPushButton, QScrollArea, QStackedWidget, QTabWidget,
                               QTextEdit, QToolBar, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import __version__
from . import core
from .core import COLORS, ask, button, run_async
from .local import ApiFail, LocalApi, q
from .view import VIEWS

LOG_COLORS = {"e": COLORS["err"], "w": COLORS["warn"], "o": COLORS["ok"], "c": COLORS["muted"]}


class JobDialog(QDialog):
    """ビルド・書き込みなどの進み具合"""

    def __init__(self, parent, title: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        self.t = QLabel(title)
        self.t.setProperty("role", "title")
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.msg = QLabel("準備中…")
        self.msg.setWordWrap(True)
        self.msg.setProperty("role", "muted")
        self.close_btn = button("閉じる", self.accept)
        self.close_btn.setEnabled(False)
        for w in (self.t, self.bar, self.msg, self.close_btn):
            lay.addWidget(w)


class MainWindow(QMainWindow):
    def __init__(self, api: LocalApi):
        super().__init__()
        self.api = api
        self.proj: dict | None = None
        self.status: dict | None = None
        self.busy = False
        self.extra: dict = {}          # ビュー同士で共有するもの (C 配列の一覧など)
        self.setWindowTitle("GBForge")
        self.resize(1500, 950)
        self.setDockNestingEnabled(True)
        icon = Path(__file__).resolve().parent.parent.parent / "packaging" / "windows" / "gbforge.ico"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))

        # ---- 中央: スタート画面 / タブ ----
        self.stack = QStackedWidget()
        from .home import HomePage
        self.home = HomePage(self)
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self.close_tab_index)
        self.tabs.currentChanged.connect(self._tab_changed)
        self._prev_view = None
        self.stack.addWidget(self.home)
        self.stack.addWidget(self.tabs)
        self.setCentralWidget(self.stack)

        # ---- パネル (ドック) ----
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.itemActivated.connect(self._tree_activate)
        self.tree.itemClicked.connect(self._tree_activate)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        self.hier = QTreeWidget()
        self.hier.setHeaderHidden(True)
        self.hier.setRootIsDecorated(False)
        self.hier.itemClicked.connect(lambda it, *_: it.data(0, Qt.UserRole) and it.data(0, Qt.UserRole)())
        self.insp_area = QScrollArea()
        self.insp_area.setWidgetResizable(True)
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setFont(core.mono_font())
        self.console.setLineWrapMode(QTextEdit.NoWrap)

        self.d_proj = self._dock("プロジェクト", self.tree, Qt.LeftDockWidgetArea)
        self.d_hier = self._dock("ヒエラルキー", self.hier, Qt.LeftDockWidgetArea)
        self.d_insp = self._dock("インスペクター", self.insp_area, Qt.RightDockWidgetArea)
        self.d_cons = self._dock("コンソール", self.console, Qt.BottomDockWidgetArea)
        self.splitDockWidget(self.d_proj, self.d_hier, Qt.Vertical)
        self.d_proj.setMinimumWidth(200)
        self.d_hier.setMinimumWidth(200)
        self.d_insp.setMinimumWidth(280)
        self.resizeDocks([self.d_proj, self.d_insp], [250, 320], Qt.Horizontal)
        self.resizeDocks([self.d_proj, self.d_hier], [600, 220], Qt.Vertical)
        self.resizeDocks([self.d_cons], [150], Qt.Vertical)

        self._build_toolbar()
        self._build_menu()
        st = QSettings("GBForge", "GBForge")
        if st.value("geometry"):
            self.restoreGeometry(st.value("geometry"))
        if st.value("state"):
            self.restoreState(st.value("state"))

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_status)
        self.timer.start(4000)
        QTimer.singleShot(0, self.start)

    # ------------------------------------------------------------------
    def _dock(self, title: str, w: QWidget, area) -> QDockWidget:
        d = QDockWidget(title, self)
        d.setObjectName("dock_" + title)
        d.setWidget(w)
        d.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable | QDockWidget.DockWidgetClosable)
        self.addDockWidget(area, d)
        return d

    def _build_toolbar(self):
        tb = QToolBar("ツールバー")
        tb.setObjectName("toolbar")
        tb.setMovable(False)
        self.addToolBar(tb)
        brand = QLabel(f"  ▣ GBForge  v{__version__}  ")
        brand.setStyleSheet("font-weight: bold; font-size: 12pt; color: #88c070;")
        self.proj_label = QLabel("")
        self.proj_label.setProperty("role", "muted")
        tb.addWidget(brand)
        tb.addWidget(self.proj_label)
        sp = QWidget()
        sp.setSizePolicy(sp.sizePolicy().horizontalPolicy().Expanding, sp.sizePolicy().verticalPolicy())
        tb.addWidget(sp)
        self.b_play = button("▶ 実行", lambda: self.build(run=True), "play", "ビルドしてエミュレーターで実行 (F5)")
        self.b_build = button("⚙ ビルド", lambda: self.build(run=False), None, "ROM をビルド (Ctrl+B)")
        self.b_flash = button("⤓ カートに書き込み", self.flash, "flash", "ロムライタ (CUBIC STYLE / GB Cart Flasher) でカートリッジに書き込み")
        self.b_ide = button("</> IDE で開く", self.open_ide, None, "Visual Studio / VS Code で開く (GBDK・日本語フォントのインクルード設定済み)")
        for b in (self.b_play, self.b_build, self.b_flash, self.b_ide):
            tb.addWidget(b)
        sp2 = QWidget()
        sp2.setSizePolicy(sp.sizePolicy())
        tb.addWidget(sp2)
        self.chip_gbdk = QLabel("GBDK …")
        self.chip_gbdk.mousePressEvent = lambda e: self.install_gbdk()
        self.chip_writer = QLabel("ロムライタ …")
        self.save_state = QLabel("")
        self.save_state.setProperty("role", "muted")
        for w in (self.chip_gbdk, self.chip_writer, self.save_state):
            tb.addWidget(w)
            tb.addSeparator()
        self.b_close = button("閉じる", self.close_project, None, "プロジェクトを閉じてスタート画面へ")
        tb.addWidget(self.b_close)

    def _build_menu(self):
        mb = self.menuBar()
        m = mb.addMenu("ファイル(&F)")
        self.act(m, "スタート画面", self.close_project)
        self.act(m, "フォルダを開く…", self.home.browse, "Ctrl+O")
        m.addSeparator()
        self.act(m, "保存", lambda: self.save_all(False), "Ctrl+S")
        m.addSeparator()
        self.act(m, "終了", self.close, "Ctrl+Q")
        m = mb.addMenu("ビルド(&B)")
        self.act(m, "ビルド", lambda: self.build(run=False), "Ctrl+B")
        self.act(m, "ビルドして実行", lambda: self.build(run=True), "F5")
        self.act(m, "カートリッジに書き込み", self.flash)
        m.addSeparator()
        self.act(m, "GBDK-2020 をセットアップ", self.install_gbdk)
        self.act(m, "Visual Studio / VS Code で開く", self.open_ide)
        m = mb.addMenu("表示(&V)")
        for d in (self.d_proj, self.d_hier, self.d_insp, self.d_cons):
            m.addAction(d.toggleViewAction())
        m.addSeparator()
        self.act(m, "パネルの配置を元に戻す", self.reset_layout)
        self.act(m, "コンソールをクリア", self.console.clear)
        m = mb.addMenu("ヘルプ(&H)")
        self.act(m, "GBForge について", lambda: QMessageBox.about(
            self, "GBForge", f"GBForge v{__version__}\nゲームボーイ用ゲームエンジン & エディタ\nGBDK-2020 / 日本語フォント gbjp / ロムライタ / GB Media Studio"))

    def act(self, menu: QMenu, text: str, fn, key: str | None = None) -> QAction:
        a = QAction(text, self)
        if key:
            a.setShortcut(QKeySequence(key))
        a.triggered.connect(lambda *_: fn())
        menu.addAction(a)
        return a

    def reset_layout(self):
        for d, area in ((self.d_proj, Qt.LeftDockWidgetArea), (self.d_hier, Qt.LeftDockWidgetArea),
                        (self.d_insp, Qt.RightDockWidgetArea), (self.d_cons, Qt.BottomDockWidgetArea)):
            d.setFloating(False)
            d.show()
            self.addDockWidget(area, d)
        self.splitDockWidget(self.d_proj, self.d_hier, Qt.Vertical)

    # ------------------------------------------------------------------
    # 起動・プロジェクト
    # ------------------------------------------------------------------
    def start(self):
        self.refresh_status()
        if self.api.root:
            self.enter_project()
        else:
            self.show_home()
            pend = getattr(self.api.state, "pending", None)
            if pend:
                self.api.state.pending = None
                self.home.open_path(str(pend))

    def show_home(self):
        for d in (self.d_proj, self.d_hier, self.d_insp, self.d_cons):
            d.hide()
        self.stack.setCurrentWidget(self.home)
        for b in (self.b_play, self.b_build, self.b_flash, self.b_ide, self.b_close):
            b.setEnabled(False)
        self.proj_label.setText("")
        self.setWindowTitle("GBForge")
        self.home.refresh()

    def enter_project(self):
        """プロジェクトを開いた後 (スタート画面 → 作業画面)"""
        while self.tabs.count():
            w = self.tabs.widget(0)
            self.tabs.removeTab(0)
            w.deleteLater()
        self.extra = {}
        self.load_project()
        self.stack.setCurrentWidget(self.tabs)
        for d in (self.d_proj, self.d_hier, self.d_insp, self.d_cons):
            d.show()
        for b in (self.b_play, self.b_build, self.b_flash, self.b_ide, self.b_close):
            b.setEnabled(True)
        p = self.proj
        self.log(f"GBForge v{p['version']} — {p['root']}", "c")
        if p["type"] == "c":
            main = next((f for f in p["sources"] if f == "main.c" or f.endswith("/main.c")), p["sources"][0] if p["sources"] else None)
            if main:
                self.open_tab("file", main)
        else:
            start = p["settings"].get("start_scene") or (p["scenes"][0] if p["scenes"] else None)
            if start:
                self.open_tab("scene", start)
        self.refresh_inspector()

    def load_project(self):
        self.proj = self.api.get("/api/project")
        s = self.proj["settings"]
        self.proj_label.setText(f"  {s.get('name')} — {self.proj['root']}")
        self.setWindowTitle(f"{s.get('name')} - GBForge")
        self.render_tree()

    def close_project(self):
        if not self.api.root:
            self.show_home()
            return
        self.save_all(True)
        while self.tabs.count():
            self.close_tab_index(0, save=False)
        self.api.post("/api/close")
        self.proj = None
        self.show_home()

    # ------------------------------------------------------------------
    # プロジェクトツリー
    # ------------------------------------------------------------------
    def render_tree(self):
        from .tree import build_tree
        self.tree.clear()
        build_tree(self, self.tree)
        self.tree.expandAll()
        self._mark_tree()

    def _mark_tree(self):
        v = self.current_view()
        it = self.tree.invisibleRootItem()
        stack = [it]
        while stack:
            x = stack.pop()
            for i in range(x.childCount()):
                c = x.child(i)
                d = c.data(0, Qt.UserRole)
                if isinstance(d, tuple) and len(d) == 2 and v is not None and d == (v.kind, v.name):
                    self.tree.blockSignals(True)
                    self.tree.setCurrentItem(c)
                    self.tree.blockSignals(False)
                stack.append(c)

    def _tree_activate(self, item: QTreeWidgetItem, *_):
        d = item.data(0, Qt.UserRole)
        if callable(d):
            d()
        elif isinstance(d, tuple) and len(d) == 2:
            self.open_tab(*d)

    def _tree_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return
        acts = item.data(0, Qt.UserRole + 1) or []
        if not acts:
            return
        m = QMenu(self)
        for text, fn in acts:
            a = m.addAction(text)
            a.triggered.connect(lambda *_, f=fn: f())
        m.exec(self.tree.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------
    # タブ
    # ------------------------------------------------------------------
    def views(self):
        return [self.tabs.widget(i) for i in range(self.tabs.count())]

    def current_view(self):
        w = self.tabs.currentWidget() if self.stack.currentWidget() is self.tabs else None
        return w

    def open_tab(self, kind: str, name: str):
        for i, v in enumerate(self.views()):
            if v.kind == kind and v.name == name:
                self.tabs.setCurrentIndex(i)
                return v
        cls = VIEWS.get(kind)
        if cls is None:
            self.log(f"この種類はまだ開けません: {kind}", "w")
            return None
        v = cls(self, name)
        try:
            v.load()
        except ApiFail as e:
            self.log(f"エラー: {e}", "e")
            v.deleteLater()
            return None
        i = self.tabs.addTab(v, f"{v.icon} {v.title()}")
        self.tabs.setTabToolTip(i, name)
        self.tabs.setCurrentIndex(i)
        return v

    def retitle(self, v):
        i = self.tabs.indexOf(v)
        if i >= 0:
            self.tabs.setTabText(i, f"{v.icon} {v.title()}" + (" ●" if v.is_dirty() else ""))

    def close_tab_index(self, i: int, save: bool = True):
        v = self.tabs.widget(i)
        if v is None:
            return
        if save and v.is_dirty():
            v.save(True)
        v.closing()
        self.tabs.removeTab(i)
        v.deleteLater()
        self.render_tree_marks()

    def close_view(self, v):
        i = self.tabs.indexOf(v)
        if i >= 0:
            self.close_tab_index(i, save=False)

    def render_tree_marks(self):
        self._mark_tree()
        self.refresh_hierarchy()
        self.refresh_inspector()

    def _tab_changed(self, *_):
        # 別のタブへ移る時に前のタブを保存 (ブラウザ版と同じ)
        if self._prev_view is not None and self._prev_view is not self.tabs.currentWidget():
            try:
                if self._prev_view.is_dirty():
                    self._prev_view.save(True)
            except RuntimeError:
                pass
        self._prev_view = self.tabs.currentWidget()
        self.render_tree_marks()
        self.update_save_state()

    # ------------------------------------------------------------------
    # インスペクター・ヒエラルキー・保存状態
    # ------------------------------------------------------------------
    def refresh_inspector(self):
        v = self.current_view()
        w = None
        if v is not None:
            try:
                w = v.inspector()
            except ApiFail as e:
                self.log(f"エラー: {e}", "e")
        if w is None:
            w = core.vbox(core.hint("項目を選択してください" if v is None else ""), "stretch", margins=8)
        else:
            w = core.vbox(w, "stretch", margins=8)
        old = self.insp_area.takeWidget()
        if old:
            old.deleteLater()
        self.insp_area.setWidget(w)

    def refresh_hierarchy(self):
        v = self.current_view()
        self.hier.clear()
        title = v.hierarchy_title() if v is not None else ""
        self.d_hier.setWindowTitle("ヒエラルキー" + (f"  {title}" if title else ""))
        if v is None:
            return
        for text, sel, fn in v.hierarchy():
            it = QTreeWidgetItem([text])
            it.setData(0, Qt.UserRole, fn)
            self.hier.addTopLevelItem(it)
            if sel:
                it.setSelected(True)

    def update_save_state(self):
        dirty = any(v.is_dirty() for v in self.views())
        self.save_state.setText("● 未保存" if dirty else "保存済み")
        for v in self.views():
            self.retitle(v)

    def save_all(self, quiet: bool = True):
        for v in self.views():
            try:
                if v.is_dirty():
                    v.save(quiet)
            except ApiFail as e:
                self.log(f"エラー: {e}", "e")
        self.update_save_state()

    # ------------------------------------------------------------------
    # コンソール
    # ------------------------------------------------------------------
    def log(self, msg: str, cls: str = ""):
        for ln in str(msg).split("\n"):
            c = cls
            if not c:
                c = "e" if any(k in ln for k in ("エラー", "error", "Error", "失敗", "✖")) else \
                    "w" if any(k in ln for k in ("警告", "warning", "⚠")) else \
                    "o" if any(k in ln for k in ("成功", "完了", "OK", "✔")) else "c" if ln.startswith("$") else ""
            col = LOG_COLORS.get(c, COLORS["text"])
            self.console.append(f'<span style="color:{col}; white-space:pre">{html.escape(ln)}</span>')
        sb = self.console.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ------------------------------------------------------------------
    # 状態 (GBDK・ロムライタ)
    # ------------------------------------------------------------------
    def refresh_status(self):
        run_async(self.api.get, "/api/status", on_done=self._status_done, on_error=lambda e: None)

    def _status_done(self, st):
        self.status = st
        g = self.chip_gbdk
        if st["gbdk"]["installed"]:
            g.setText("✔ GBDK")
            g.setProperty("role", "chip-ok")
            g.setToolTip(f"GBDK-2020: {st['gbdk'].get('home')} ({st['gbdk'].get('source')})")
        else:
            g.setText("✘ GBDK 未セットアップ")
            g.setProperty("role", "chip-ng")
            g.setToolTip("クリックで GBDK-2020 を自動セットアップ")
        w = self.chip_writer
        if st["writer"]:
            w.setText("✔ ロムライタ " + st["writer"][0]["device"])
            w.setProperty("role", "chip-ok")
            w.setToolTip("\n".join(p["device"] + " " + p["description"] for p in st["writer"]))
        else:
            w.setText("○ ロムライタ未接続")
            w.setProperty("role", "chip-idle")
            w.setToolTip("USB で接続すると自動で検出します (FTDI)")
        for x in (g, w):
            x.style().unpolish(x)
            x.style().polish(x)
        self.home.set_status(st)

    # ------------------------------------------------------------------
    # ジョブ (ビルド・書き込み・GBDK のセットアップ)
    # ------------------------------------------------------------------
    def confirm_gbdk(self, body: dict) -> bool:
        st = self.status or self.api.get("/api/status")
        tc = ((self.proj or {}).get("settings", {}).get("build", {}) or {}).get("toolchain", "gbdk")
        if tc != "gbdk" or st["gbdk"]["installed"]:
            return True
        if not ask(self, "GBDK-2020", "コンパイラ GBDK-2020 (約 7MB) がまだありません。\nGitHub の公式リリースからダウンロードして"
                   "自動セットアップしますか？\n(チェックサムで検証し、ユーザーフォルダの .gbforge に保存します)"):
            return False
        body["install_gbdk"] = True
        return True

    def run_job(self, url: str, body: dict | None = None, title: str | None = None, need_gbdk: bool = True,
                on_finish=None):
        """ジョブを始めて、終わるまで進み具合を表示する (画面は止まらない)。終わると on_finish(job)。"""
        if self.busy:
            self.log("別の処理が実行中です", "w")
            return
        body = dict(body or {})
        if need_gbdk and url != "/api/gbdk/install" and not self.confirm_gbdk(body):
            return
        self.save_all(True)
        try:
            job = self.api.post(url, body)
        except ApiFail as e:
            self.log(f"エラー: {e}", "e")
            return
        self.busy = True
        dlg = JobDialog(self, title) if title else None
        if dlg:
            dlg.show()
        state = {"since": 0}

        def poll():
            try:
                j = self.api.get(f"/api/job/{job['id']}", since=state["since"])
            except ApiFail as e:
                self.log(f"エラー: {e}", "e")
                t.stop()
                self.busy = False
                return
            for ln in j["log"]:
                self.log(ln)
            state["since"] = j["log_len"]
            if dlg:
                p = j["progress"]
                if p["total"]:
                    dlg.bar.setRange(0, p["total"])
                    dlg.bar.setValue(p["done"])
                    dlg.msg.setText(f"{p['label']} {p['done']}/{p['total']}")
                elif j["log"]:
                    dlg.msg.setText(j["log"][-1])
            if j["done"]:
                t.stop()
                self.busy = False
                if dlg:
                    dlg.bar.setRange(0, 100)
                    dlg.bar.setValue(100 if j["ok"] else dlg.bar.value())
                    dlg.msg.setText("完了しました！" if j["ok"] else "失敗しました。コンソールを確認してください")
                    dlg.close_btn.setEnabled(True)
                r = j.get("result") or {}
                if j["ok"] and r.get("glyphs"):
                    self.log(f"ROM {r['rom_size'] // 1024}KB / 使用文字 {r['glyphs']} / データ {r['data_bytes']} バイト", "o")
                if self.api.root:
                    try:
                        self.load_project()
                    except ApiFail:
                        pass
                self.refresh_status()
                if on_finish:
                    on_finish(j)
        t = QTimer(self)
        t.timeout.connect(poll)
        t.start(250)
        return t

    def build(self, run: bool):
        if self.api.root:
            self.run_job("/api/build", {"run": run})

    def flash(self):
        if not self.api.root:
            return
        if not ask(self, "カートリッジに書き込み", "ビルドしてロムライタ (CUBIC STYLE / GB Cart Flasher) でカートリッジに書き込みます。よろしいですか？"):
            return
        self.run_job("/api/flash", {}, "カートリッジに書き込み中")

    def install_gbdk(self):
        if self.status and self.status["gbdk"]["installed"]:
            return
        if not ask(self, "GBDK-2020", "GBDK-2020 を GitHub の公式リリースからダウンロードしてセットアップします (約 7MB)。よろしいですか？"):
            return
        self.run_job("/api/gbdk/install", {}, "GBDK-2020 をセットアップ中", need_gbdk=False)

    def open_ide(self, file: str | None = None):
        if not self.api.root:
            return
        self.save_all(True)
        if file is None:
            v = self.current_view()
            if v is not None and v.kind == "file":
                file = v.name
            elif v is not None and v.kind == "script":
                file = f"scripts/{v.name}.c"
        try:
            r = self.api.post("/api/ide/open", {"file": file})
            self.log(f"{r['ide']} で開きました (GBDK と gbjp のインクルード設定済み)", "o")
        except ApiFail as e:
            self.log(f"エラー: {e}", "e")

    # ------------------------------------------------------------------
    def closeEvent(self, e):
        self.save_all(True)
        st = QSettings("GBForge", "GBForge")
        st.setValue("geometry", self.saveGeometry())
        st.setValue("state", self.saveState())
        super().closeEvent(e)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    root = None
    pending = None
    if argv and argv[0]:
        p = Path(argv[0].strip('"')).expanduser().resolve()
        if p.name == "project.gbproj":
            p = p.parent
        if (p / "project.gbproj").exists():
            root = p
        elif p.is_dir():
            pending = p
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("GBForge")
    app.setOrganizationName("GBForge")
    core.apply_theme(app)
    from . import views  # noqa: F401  (ビューを登録)
    win = MainWindow(LocalApi(root, pending))
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
