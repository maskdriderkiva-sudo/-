"""pty 経由で pyserial を使った実際のシリアル経路をテスト (Linux/macOS のみ)。"""
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

serial = pytest.importorskip("serial")
pty = pytest.importorskip("pty")

from fake_flasher import FakeCartFlasher  # noqa: E402
from gbforge.flasher import CartFlasher  # noqa: E402
from test_flasher import make_rom  # noqa: E402


def test_flash_over_pty():
    import tty
    master, slave = pty.openpty()
    tty.setraw(master)
    dev = FakeCartFlasher()
    stop = threading.Event()

    def device():
        while not stop.is_set():
            try:
                data = os.read(master, 4096)
            except OSError:
                return
            dev.write(data)
            out = dev.read(1 << 20)
            if out:
                os.write(master, out)

    th = threading.Thread(target=device, daemon=True)
    th.start()
    name = os.ttyname(slave)
    try:
        fl = CartFlasher.open(name, 115200)  # pty は非標準ボーレート不可のため
        rom = make_rom(2)
        fl.flash_rom(rom, erase=True, verify=True, log=lambda m: None)
        assert bytes(dev.flash[:len(rom)]) == rom
        fl.close()
    finally:
        stop.set()
        os.close(slave)
        os.close(master)
