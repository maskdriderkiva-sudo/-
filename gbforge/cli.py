"""gbforge コマンドラインインターフェース。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .flasher import BAUD_RATES, CartFlasher, FlasherError, list_ports
from .project import Project, ProjectError


def _project(path: str) -> Project:
    p = Path(path or ".")
    if p.is_file():
        p = p.parent
    return Project(p)


_last_label = [None]


def _progress_bar(done: int, total: int, label: str):
    if label != _last_label[0] and _last_label[0] is not None:
        sys.stdout.write("\n")
    _last_label[0] = label
    if not total:
        return
    w = 30
    n = int(w * done / total)
    sys.stdout.write(f"\r{label}: [{'#' * n}{'.' * (w - n)}] {100 * done // total:3d}%")
    if done >= total:
        sys.stdout.write("\n")
        _last_label[0] = None
    sys.stdout.flush()


def cmd_new(a):
    from .templates import create_c_project, create_project
    if a.type == "c":
        p = create_c_project(Path(a.path), a.name or Path(a.path).name, target=a.target)
    else:
        p = create_project(Path(a.path), a.name or Path(a.path).name, sample=not a.empty)
    print(f"プロジェクトを作成しました: {p.root}")
    print(f"  エディタ起動:  gbforge editor {a.path}")
    print(f"  ビルド:        gbforge build {a.path}")
    print(f"  IDE で開く:    gbforge ide {a.path}   (Visual Studio / VS Code)")


def _ask_install() -> bool:
    if not sys.stdin.isatty():
        print("GBDK-2020 がありません。`gbforge setup` でインストールしてください")
        return False
    ans = input("GBDK-2020 が見つかりません。今ダウンロードしてセットアップしますか？ [Y/n] ").strip().lower()
    return ans in ("", "y", "yes")


def _prepare_toolchain(project, toolchain_name):
    from .actions import ensure_gbdk
    tc = toolchain_name or project.settings["build"].get("toolchain", "gbdk")
    if tc == "gbdk":
        from .toolchain import ToolchainError
        try:
            ensure_gbdk(project, progress=_progress_bar, ask=_ask_install)
        except ToolchainError as e:
            print(f"エラー: {e}")


def cmd_adopt(a):
    """既存の GBDK フォルダを GBForge プロジェクトとして使えるようにする (project.gbproj を追加するだけ)。"""
    from . import adopt
    info = adopt.inspect(Path(a.path))
    for k in ("name", "build_script_windows", "build_script_unix", "output", "target", "gbdk"):
        print(f"  {k:22s}: {info.get(k)}")
    if info["already_project"]:
        print("既に GBForge のプロジェクトです")
        return 0
    adopt.adopt(Path(a.path), info)
    print(f"project.gbproj を追加しました。gbforge editor \"{info['path']}\" で開けます")
    return 0


def cmd_clean(a):
    import shutil
    p = _project(a.path)
    if p.build_dir.exists():
        shutil.rmtree(p.build_dir)
    print("build/ を削除しました")
    return 0


def cmd_ide(a):
    from .ide import generate_ide_files, open_in_ide
    p = _project(a.path)
    if a.no_open:
        for f in generate_ide_files(p):
            print("生成:", f)
        return 0
    try:
        name = open_in_ide(p, a.editor, a.file)
    except RuntimeError as e:
        print(f"エラー: {e}")
        return 1
    print(f"{name} で開きました")
    return 0


def cmd_jpfont(a):
    from . import jpfont
    out = Path(a.out)
    jpfont.install_library(out, overwrite=a.update_lib)
    chars = None if a.full else jpfont.scan_dirs([Path(x) for x in (a.scan or ["src"])]) | set(a.chars or "")
    r = jpfont.generate(out, chars, a.font)
    print(f"{out}/font に {r['glyphs']} 文字 (約 {r['bytes'] // 1024}KB) を生成しました")
    if r["missing"]:
        print("フォントに無い文字:", "".join(r["missing"]))
    print('使い方: #include "gbjp.h" して jp_init() → jp_print()。リンク時に lcc -autobank が必要です')
    return 0


def cmd_build(a):
    from .actions import build_project
    if getattr(a, "clean", False):
        cmd_clean(a)
    _prepare_toolchain(_project(a.path), a.toolchain)
    r = build_project(_project(a.path), toolchain=a.toolchain)
    if r.ok and a.run:
        from .actions import launch_emulator
        launch_emulator(_project(a.path), r.rom)
    return 0 if r.ok else 1


def cmd_run(a):
    a.run = True
    return cmd_build(a)


def cmd_flash(a):
    from .actions import build_project, flash_rom
    target = Path(a.target or ".")
    project = None
    if target.suffix.lower() in (".gb", ".gbc"):
        rom = target
    else:
        project = _project(str(target))
        if a.no_build and project.rom_path.exists():
            rom = project.rom_path
        else:
            _prepare_toolchain(project, a.toolchain)
            r = build_project(project, toolchain=a.toolchain)
            if not r.ok:
                return 1
            rom = r.rom
    try:
        flash_rom(project, rom, progress=_progress_bar, port=a.port, baud=a.baud,
                  erase_before_write=False if a.no_erase else None, verify=False if a.no_verify else None,
                  algorithm=a.algorithm, wait_mode=a.wait_mode, mbc=a.mbc)
    except FlasherError as e:
        print(f"\nエラー: {e}")
        return 1
    return 0


def cmd_ports(a):
    ports = list_ports()
    if not ports:
        print("シリアルポートが見つかりません (pyserial 未インストール、またはデバイス未接続)")
    for p in ports:
        mark = "★ " if p["likely"] else "  "
        print(f"{mark}{p['device']:20s} {p['description']}")
    if any(p["likely"] for p in ports):
        print("★ = FTDI (GB Cart Flasher の可能性が高い)")


def _open(a) -> CartFlasher:
    return CartFlasher.open(a.port, a.baud, mbc=a.mbc or "MBC5", algorithm=a.algorithm or "ALG16",
                            wait_mode=a.wait_mode or "TOGGLE")


def cmd_info(a):
    try:
        fl = _open(a)
        info = fl.status()
        fl.close()
    except FlasherError as e:
        print(f"エラー: {e}")
        return 1
    for k, v in info.as_dict().items():
        print(f"{k:16s}: {v}")
    return 0


def cmd_dump(a):
    try:
        fl = _open(a)
        data = fl.read_rom(a.size * 1024, _progress_bar)
        fl.close()
    except FlasherError as e:
        print(f"エラー: {e}")
        return 1
    Path(a.out).write_bytes(data)
    print(f"{a.out} に保存しました ({len(data) // 1024}KB)")
    return 0


def cmd_sram(a):
    try:
        fl = _open(a)
        if a.action == "read":
            data = fl.read_sram(a.banks, _progress_bar)
            Path(a.file).write_bytes(data)
            print(f"{a.file} に保存しました")
        else:
            fl.write_sram(Path(a.file).read_bytes(), _progress_bar)
            print("SRAM に書き込みました")
        fl.close()
    except FlasherError as e:
        print(f"エラー: {e}")
        return 1
    return 0


def cmd_editor(a):
    from .server import serve
    path = Path(a.path) if a.path else None
    serve(path, port=a.port, open_browser=not a.no_browser)


def cmd_setup(a):
    from . import toolchain
    st = toolchain.status()
    if st["installed"] and not a.force:
        print(f"GBDK-2020 はセットアップ済みです: {st['home']} ({st['source']})")
        return 0
    try:
        toolchain.install(archive=Path(a.archive) if a.archive else None, progress=_progress_bar)
    except toolchain.ToolchainError as e:
        print(f"エラー: {e}")
        return 1
    return 0


def cmd_doctor(a):
    """GBDK / ロムライタ / エミュレータの状態をまとめて表示。"""
    import importlib.util
    from . import toolchain
    st = toolchain.status()
    ok = "✔"
    ng = "✘"
    print(f"{ok if st['installed'] else ng} GBDK-2020   : " + (f"{st['home']} ({st['source']})" if st["installed"]
                                                       else "未インストール → gbforge setup"))
    ports = [p for p in list_ports() if p["likely"]]
    print(f"{ok if ports else ng} ロムライタ   : " + (", ".join(p["device"] for p in ports) if ports
                                                 else "FTDI デバイスが見つかりません (USB 接続・ドライバを確認)"))
    has_pyboy = importlib.util.find_spec("pyboy") is not None
    print(f"{ok if has_pyboy else ng} エミュレータ : " + ("PyBoy (内蔵)" if has_pyboy else "PyBoy 未インストール (pip install pyboy) / 設定で外部エミュレータを指定可"))
    return 0


def cmd_play(a):
    from .actions import play_rom
    play_rom(Path(a.rom), scale=a.scale)
    return 0


def cmd_text(a):
    """テキストが GB 上でどう表示されるかをターミナルに表示する。"""
    from .font import load_font, render_preview
    pages, missing = render_preview(a.text, a.font, a.choice)
    font = load_font(a.font)
    for pi, page in enumerate(pages):
        print(f"--- ページ {pi + 1} ---")
        for row in page:
            for y in range(8):
                line = ""
                for ch in row:
                    bm = font.get(ch, bytes(8))
                    line += "".join("█" if bm[y] & (0x80 >> x) else " " for x in range(8))
                print(line)
    if missing:
        print("フォントに無い文字:", "".join(missing))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gbforge", description="GBForge - ゲームボーイ用ゲームエンジン (GBDK-2020)")
    ap.add_argument("--version", action="version", version=f"gbforge {__version__}")
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("new", help="新しいプロジェクトを作成")
    s.add_argument("path")
    s.add_argument("--name")
    s.add_argument("--empty", action="store_true", help="サンプル無しの空プロジェクト")
    s.add_argument("--type", choices=["engine", "c"], default="engine",
                   help="engine = シーン/アクターのゲームエンジン, c = GBDK で C プログラムを書く")
    s.add_argument("--target", choices=["dmg", "both", "cgb"], default="both",
                   help="C プロジェクトの対象: dmg=初代, both=初代+カラー, cgb=カラー専用")
    s.set_defaults(fn=cmd_new)

    for name, fn, hlp in (("build", cmd_build, "ROM をビルド"), ("run", cmd_run, "ビルドしてエミュレータで実行")):
        s = sub.add_parser(name, help=hlp)
        s.add_argument("path", nargs="?", default=".")
        s.add_argument("--toolchain", choices=["gbdk", "sdcc-shim"])
        s.add_argument("--run", action="store_true", help=argparse.SUPPRESS)
        s.add_argument("--clean", action="store_true", help="build/ を消してからビルド")
        s.set_defaults(fn=fn)

    def flasher_args(s, need_port=True):
        s.add_argument("--port", required=need_port, help="シリアルポート (例: COM3, /dev/ttyUSB0)")
        s.add_argument("--baud", type=int, default=187500, choices=BAUD_RATES)
        s.add_argument("--mbc", choices=["MBC1", "MBC2", "MBC3", "MBC5", "ROMONLY", "RUMBLE", "AUTO"])
        s.add_argument("--algorithm", choices=["ALG16", "ALG12", "ALG16WR", "ALG12_M29F"])
        s.add_argument("--wait-mode", dest="wait_mode", choices=["LONGER", "DEFAULT", "DATA_POLL", "TOGGLE"])

    s = sub.add_parser("flash", help="ロムライタでカートリッジに書き込み")
    s.add_argument("target", nargs="?", default=".", help="プロジェクトフォルダまたは .gb ファイル")
    flasher_args(s, need_port=False)
    s.set_defaults(baud=None)
    s.add_argument("--no-erase", action="store_true")
    s.add_argument("--no-verify", action="store_true")
    s.add_argument("--no-build", action="store_true", help="ビルド済み ROM をそのまま書く")
    s.add_argument("--toolchain", choices=["gbdk", "sdcc-shim"])
    s.set_defaults(fn=cmd_flash)

    s = sub.add_parser("ports", help="シリアルポート一覧")
    s.set_defaults(fn=cmd_ports)

    s = sub.add_parser("info", help="ロムライタとカートリッジの情報を表示")
    flasher_args(s)
    s.set_defaults(fn=cmd_info)

    s = sub.add_parser("dump", help="カートリッジの ROM を吸い出す")
    flasher_args(s)
    s.add_argument("--size", type=int, default=1024, help="KB 単位 (既定 1024)")
    s.add_argument("out")
    s.set_defaults(fn=cmd_dump)

    s = sub.add_parser("sram", help="セーブデータ (SRAM) の読み書き")
    flasher_args(s)
    s.add_argument("action", choices=["read", "write"])
    s.add_argument("file")
    s.add_argument("--banks", type=int, default=1)
    s.set_defaults(fn=cmd_sram)

    s = sub.add_parser("adopt", help="既存の GBDK フォルダ (main.c / build.bat) を GBForge で開けるようにする")
    s.add_argument("path")
    s.set_defaults(fn=cmd_adopt)

    s = sub.add_parser("clean", help="ビルド成果物 (build/) を削除")
    s.add_argument("path", nargs="?", default=".")
    s.set_defaults(fn=cmd_clean)

    s = sub.add_parser("ide", help="Visual Studio / VS Code でプロジェクトを開く (IntelliSense 設定を生成)")
    s.add_argument("path", nargs="?", default=".")
    s.add_argument("--editor", choices=["auto", "vscode", "visualstudio"])
    s.add_argument("--file", help="開くファイル (プロジェクトからの相対パス)")
    s.add_argument("--no-open", action="store_true", help="設定ファイルの生成だけ")
    s.set_defaults(fn=cmd_ide)

    s = sub.add_parser("jpfont", help="GBDK 用日本語フォントライブラリ (gbjp) とフォントデータを生成")
    s.add_argument("--out", default="lib/gbjp", help="出力先 (既定 lib/gbjp)")
    s.add_argument("--scan", nargs="*", help="文字を抽出するソースのフォルダ/ファイル (既定 src)")
    s.add_argument("--full", action="store_true", help="全文字 (約 7000 字) を入れる")
    s.add_argument("--chars", help="追加で入れる文字")
    s.add_argument("--font", default="misaki_gothic", choices=["misaki_gothic", "misaki_mincho"])
    s.add_argument("--update-lib", action="store_true", help="gbjp.h / gbjp.c も最新版で上書き")
    s.set_defaults(fn=cmd_jpfont)

    s = sub.add_parser("setup", help="GBDK-2020 を自動ダウンロードしてセットアップ")
    s.add_argument("--archive", help="ダウンロード済みの公式アーカイブを使う (オフライン用)")
    s.add_argument("--force", action="store_true", help="既にあっても入れ直す")
    s.set_defaults(fn=cmd_setup)

    s = sub.add_parser("doctor", help="GBDK / ロムライタ / エミュレータの状態を確認")
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("play", help="内蔵エミュレータ (PyBoy) で ROM を遊ぶ")
    s.add_argument("rom")
    s.add_argument("--scale", type=int, default=3)
    s.set_defaults(fn=cmd_play)

    s = sub.add_parser("editor", help="エディタを起動 (ブラウザで開く)")
    s.add_argument("path", nargs="?", default=None, help="プロジェクトフォルダ (省略するとスタート画面)")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-browser", action="store_true")
    s.set_defaults(fn=cmd_editor)

    s = sub.add_parser("text", help="日本語テキストの表示プレビュー (ターミナル)")
    s.add_argument("text")
    s.add_argument("--font", default="misaki_gothic")
    s.add_argument("--choice", action="store_true")
    s.set_defaults(fn=cmd_text)

    for stream in (sys.stdout, sys.stderr):   # 日本語を出せないコンソールでも落ちないように
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        argv = ["editor"]
    elif len(argv) == 1 and (argv[0].endswith(".gbproj") or Path(argv[0]).is_dir()):
        # project.gbproj のダブルクリック / フォルダのドラッグ&ドロップ → エディタで開く
        argv = ["editor", str(Path(argv[0]).parent if argv[0].endswith(".gbproj") else argv[0])]
    a = ap.parse_args(argv)
    try:
        return a.fn(a) or 0
    except ProjectError as e:
        print(f"エラー: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
