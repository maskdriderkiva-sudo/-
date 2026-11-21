"""ネイティブアプリ (Qt) のテスト。画面なし (offscreen) で実際にウィンドウを作り、ユーザーと同じ操作をして結果を確かめる。"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from gbforge import builder  # noqa: E402
from gbforge.app import core  # noqa: E402
from gbforge.app.local import LocalApi  # noqa: E402
from gbforge.app.main import MainWindow  # noqa: E402
from gbforge.templates import create_c_project, create_project  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    core.apply_theme(a)
    from gbforge.app import views  # noqa: F401
    return a


def pump(app, sec=0.3):
    end = time.time() + sec
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def window(app, root):
    win = MainWindow(LocalApi(root))
    win.show()
    pump(app, 0.5)
    return win


def wait_job(app, win, timeout=300):
    end = time.time() + timeout
    while win.busy and time.time() < end:
        pump(app, 0.1)
    assert not win.busy, "ジョブが終わりません"


def console(win) -> str:
    return win.console.toPlainText()


# ---------------------------------------------------------------------------
def test_start_screen_create_and_open(app, tmp_path):
    win = window(app, None)
    assert win.stack.currentWidget() is win.home
    win.home.new_type.setCurrentIndex(win.home.new_type.findData("c"))
    win.home.new_name.setText("New C")
    win.home.new_path.setText(str(tmp_path / "newc"))
    win.home.create()
    pump(app)
    assert (tmp_path / "newc" / "project.gbproj").exists()
    assert win.stack.currentWidget() is win.tabs
    assert win.current_view().kind == "file" and win.current_view().name.endswith("main.c")
    win.close_project()
    assert win.stack.currentWidget() is win.home
    # 既存の GBDK フォルダ (project.gbproj なし) は取り込みの確認が出る
    old = tmp_path / "OldGame_v1.0"
    old.mkdir()
    (old / "main.c").write_text("void main(void) {}\n")
    (old / "build.bat").write_text("lcc -o game.gb main.c\n")
    from gbforge.app import home
    seen = {}

    def fake_exec(self):
        seen["info"] = self.values()
        return home.QDialog.Accepted
    orig = home.AdoptDialog.exec
    home.AdoptDialog.exec = fake_exec
    try:
        win.home.open_path(str(old))
    finally:
        home.AdoptDialog.exec = orig
    assert seen["info"]["build_script_windows"] == "build.bat"
    assert (old / "project.gbproj").exists() and (old / "build.bat").read_text().startswith("lcc")
    win.close()


def test_engine_editors(app, tmp_path):
    root = tmp_path / "eng"
    create_project(root, "Sample Game")
    win = window(app, root)
    # テキスト: 書き換えて保存 → texts.json
    v = win.open_tab("texts", "texts")
    v.list.setCurrentRow(0)
    pump(app)
    key = v.sel
    v.body.setPlainText("テストの ぶんしょう")
    pump(app, 0.4)
    assert v.is_dirty() and "未保存" in win.save_state.text()
    v.save(False)
    assert json.loads((root / "texts.json").read_text(encoding="utf-8"))[key]["text"] == "テストの ぶんしょう"
    assert v.pv_lay.count() >= 1               # プレビュー (GB のメッセージウィンドウ)
    # スクリプト
    sv = win.open_tab("script", "Elder")
    sv.edit.setPlainText(sv.edit.toPlainText() + "\n/* edited */\n")
    win.save_all(False)
    assert "/* edited */" in (root / "scripts" / "Elder.c").read_text(encoding="utf-8")
    # アセット
    for kind, name in (("sprite", "hero"), ("background", "town.png")):
        av = win.open_tab(kind, name)
        assert av is not None
    # 設定
    st = win.open_tab("settings", "settings")
    st.f["name"].setText("改名テスト")
    st.save(False)
    assert json.loads((root / "project.gbproj").read_text(encoding="utf-8"))["name"] == "改名テスト"
    assert "改名テスト" in win.windowTitle()
    win.close()


def test_c_editors(app, tmp_path):
    root = tmp_path / "cproj"
    create_c_project(root, "C Test", target="cgb")
    win = window(app, root)
    # 変換ツール: res の PNG を変換して保存
    png = next(root.rglob("*.png")).relative_to(root).as_posix()
    win.extra["cv_preset"] = png
    cv = win.open_tab("convert", "convert")
    cv.out_name.setText("conv_test")
    cv.run(True)
    pump(app)
    assert (root / "src" / "conv_test.c").exists() and (root / "src" / "conv_test.h").exists()
    assert "タイル" in cv.stats.itemAt(0).widget().text()
    # C 配列: パレットを書き戻す
    from gbforge.app.views.carray import arrays
    pal = next(a for a in arrays(win, True) if a["kind"] == "palette")
    pv = win.open_tab("carray", f"{pal['file']}#{pal['name']}")
    pv.vals[0] = 31 | (5 << 5) | (9 << 10)      # RGB(31, 5, 9)
    pv._save_values()
    assert "RGB(31, 5, 9)" in (root / pal["file"]).read_text(encoding="utf-8")
    # バージョン
    vv = win.open_tab("versions", "versions")
    assert vv.table.rowCount() >= 1
    assert "エラー" not in console(win)
    win.close()


@pytest.mark.skipif(not builder.find_gbdk(None), reason="GBDK-2020 が必要")
def test_build_from_app(app, tmp_path):
    root = tmp_path / "cb"
    create_c_project(root, "Build Test", target="both")
    win = window(app, root)
    win.build(run=False)
    wait_job(app, win)
    assert "成功" in console(win), console(win)[-2000:]
    assert any(root.joinpath("build").glob("*.gb*"))
    win.close()


def test_scene_editor_mouse(app, tmp_path):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    root = tmp_path / "eng2"
    create_project(root, "Sample Game")
    win = window(app, root)
    v = win.open_tab("scene", "town")
    n = len(v.scene["actors"])
    z = v.zoom
    # アクターを追加 (A ツールでクリック)
    v.set_tool("actor")
    QTest.mouseClick(v.canvas, Qt.LeftButton, Qt.NoModifier, QPoint(100 * z, 120 * z))
    pump(app)
    assert len(v.scene["actors"]) == n + 1 and v.sel == n and v.tool == "select"
    a = v.scene["actors"][n]
    assert (a["x"], a["y"]) == (88, 112)                      # 8px スナップ (ブラウザ版と同じ式)
    # ドラッグで移動
    QTest.mousePress(v.canvas, Qt.LeftButton, Qt.NoModifier, QPoint((a["x"] + 4) * z, (a["y"] + 4) * z))
    QTest.mouseMove(v.canvas, QPoint((a["x"] + 4 + 16) * z, (a["y"] + 4) * z))
    QTest.mouseRelease(v.canvas, Qt.LeftButton, Qt.NoModifier, QPoint((a["x"] + 4 + 16) * z, (a["y"] + 4) * z))
    assert a["x"] == 104
    # 当たり判定を塗る
    v.set_tool("collision")
    QTest.mouseClick(v.canvas, Qt.LeftButton, Qt.NoModifier, QPoint(12 * 8 * z + 2, 9 * 8 * z + 2))
    assert v.scene["collision"][9][12] == "1"
    # インスペクター・ヒエラルキー
    assert win.hier.topLevelItemCount() == n + 2
    win.save_all(False)
    sc = json.loads((root / "scenes" / "town.json").read_text(encoding="utf-8"))
    assert len(sc["actors"]) == n + 1 and sc["collision"][9][12] == "1" and "_bg_size" not in sc
    win.close()


def test_stage_editor_mouse(app, tmp_path):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    root = tmp_path / "cst"
    create_c_project(root, "Stage Test", target="cgb")
    png = next(root.rglob("*.png")).relative_to(root).as_posix()
    win = window(app, root)
    win.api.post("/api/stage/lv", {"from": "new", "width": 20, "height": 18, "tileset": {"kind": "png", "path": png}})
    win.load_project()
    v = win.open_tab("stage", "lv")
    assert v.tile_count > 1
    z = v.zoom
    v.sel = 3
    QTest.mouseClick(v.canvas, Qt.LeftButton, Qt.NoModifier, QPoint(2 * 8 * z + 1, 1 * 8 * z + 1))
    assert v.data["tiles"][1 * 20 + 2] == 3
    # 矩形
    v.set_tool("rect")
    v.sel = 5
    QTest.mousePress(v.canvas, Qt.LeftButton, Qt.NoModifier, QPoint(4 * 8 * z + 1, 4 * 8 * z + 1))
    QTest.mouseMove(v.canvas, QPoint(6 * 8 * z + 1, 5 * 8 * z + 1))
    QTest.mouseRelease(v.canvas, Qt.LeftButton, Qt.NoModifier, QPoint(6 * 8 * z + 1, 5 * 8 * z + 1))
    assert all(v.data["tiles"][y * 20 + x] == 5 for y in (4, 5) for x in (4, 5, 6))
    v.do_undo()
    assert v.data["tiles"][4 * 20 + 4] != 5
    win.save_all(False)
    out = (root / "src" / "stage_data.c").read_text(encoding="utf-8")
    assert "stage_lv" in out
    win.close()
