"""CLI とエディタで共通の操作: ビルド / エミュレータ起動 / ロムライタ書き込み。"""
from __future__ import annotations

import importlib.util
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Callable

from . import builder
from .flasher import CartFlasher, FlasherError
from .project import Project

Log = Callable[[str], None]


def self_command() -> list[str]:
    """自分自身 (gbforge) を起動するコマンド。配布版 (PyInstaller) では実行ファイルそのもの。"""
    import os
    if os.environ.get("GBFORGE_LAUNCHER"):       # Windows 版 GBForge.exe
        return [os.environ["GBFORGE_LAUNCHER"]]
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "gbforge"]


def play_rom(rom: Path, scale: int = 3):
    """内蔵エミュレータ (PyBoy) で ROM を遊ぶ。ウィンドウを閉じるまで戻らない。"""
    from pyboy import PyBoy
    pb = PyBoy(str(rom), scale=scale, sound_emulated=True)
    try:
        while pb.tick():
            pass
    finally:
        pb.stop()   # セーブデータ (.ram) は ROM の隣に保存される


def ensure_gbdk(project: Project | None, log: Log = print, progress=None, ask=None) -> bool:
    """GBDK が無ければ (確認のうえ) 自動インストールする。"""
    from . import toolchain
    if builder.find_gbdk(project):
        return True
    if ask and not ask():
        return False
    log(f"GBDK-2020 {toolchain.GBDK_VERSION} をセットアップします (約 7MB)…")
    toolchain.install(progress=progress, log=log)
    return True


def build_project(project: Project, log: Log = print, toolchain: str | None = None) -> builder.BuildResult:
    r = builder.build(project, log=log, toolchain=toolchain)
    try:   # Unity のように、IDE 用の設定 (.sln / .vscode) があれば最新にする
        from .ide import generate_ide_files
        if (project.root / ".vscode" / "c_cpp_properties.json").exists() or list(project.root.glob("*.sln")):
            generate_ide_files(project)
    except Exception as e:  # noqa: BLE001
        log(f"警告: IDE 設定の生成に失敗しました: {e}")
    return r


def launch_emulator(project: Project, rom: Path, log: Log = print) -> bool:
    emu = (project.settings["build"].get("emulator") or "").strip()
    if emu:
        cmd = shlex.split(emu, posix=(sys.platform != "win32"))
        if any("{rom}" in c for c in cmd):
            cmd = [c.replace("{rom}", str(rom)) for c in cmd]
        else:
            cmd.append(str(rom))
    elif importlib.util.find_spec("pyboy"):
        cmd = self_command() + ["play", str(rom)]
    else:
        log("エミュレータが設定されていません。設定でエミュレータのパスを指定するか `pip install pyboy` してください")
        return False
    log("$ " + " ".join(cmd))
    try:
        subprocess.Popen(cmd, cwd=str(rom.parent))
    except OSError as e:
        log(f"エミュレータを起動できません: {e}")
        return False
    return True


def mbc_from_header(rom: bytes) -> str:
    t = rom[0x147] if len(rom) > 0x147 else 0x19
    if t == 0x00:
        return "ROMONLY"
    if 0x01 <= t <= 0x03:
        return "MBC1"
    if t in (0x05, 0x06):
        return "MBC2"
    if 0x0F <= t <= 0x13:
        return "MBC3"
    if t in (0x1C, 0x1D, 0x1E):
        return "RUMBLE"
    return "MBC5"


def flash_rom(project: Project | None, rom: Path, log: Log = print,
              progress: Callable[[int, int, str], None] | None = None, **overrides) -> bool:
    cfg = dict(project.settings["flasher"]) if project else {}
    cfg.update({k: v for k, v in overrides.items() if v is not None})
    port = cfg.get("port")
    if not port:
        from .flasher import list_ports
        ports = [p for p in list_ports() if p["likely"]]
        if len(ports) == 1:
            port = ports[0]["device"]
            log(f"ロムライタを自動検出: {port}")
        else:
            raise FlasherError("ロムライタのポートを指定してください (gbforge ports で一覧表示)")
    data = Path(rom).read_bytes()
    if cfg.get("mbc") in (None, "", "ROM"):
        cfg["mbc"] = mbc_from_header(data)
        log(f"ROM ヘッダーのカートリッジ種別 0x{data[0x147]:02X} → {cfg['mbc']}")
    fl = CartFlasher.open(port, int(cfg.get("baud", 187500)), mbc=cfg.get("mbc", "MBC5"),
                          algorithm=cfg.get("algorithm", "ALG16"), wait_mode=cfg.get("wait_mode", "TOGGLE"))
    try:
        fl.flash_rom(data, erase=bool(cfg.get("erase_before_write", True)), verify=bool(cfg.get("verify", True)),
                     progress=progress or (lambda d, t, m: None), log=log)
    finally:
        fl.close()
    log("書き込み完了！カートリッジをゲームボーイに挿して電源を入れてください")
    return True
