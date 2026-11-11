import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fake_flasher import LOGO, FakeCartFlasher  # noqa: E402
from gbforge.flasher import CartFlasher, crc16, make_packet  # noqa: E402

LUT1 = [0x00, 0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0x81, 0x91, 0xA1, 0xB1, 0xC1, 0xD1, 0xE1, 0xF1]
LUT0 = [0x00, 0x21, 0x42, 0x63, 0x84, 0xA5, 0xC6, 0xE7, 0x08, 0x29, 0x4A, 0x6B, 0x8C, 0xAD, 0xCE, 0xEF]


def firmware_crc(data):
    """ファームウェア (add_crc / compute_crc) のニブル実装をそのまま移植"""
    hi = lo = 0
    for b in data:
        for nib in (b >> 4, b & 0xF):
            ix = nib ^ (hi >> 4)
            hi = (((hi << 4) | (lo >> 4)) & 0xFF) ^ LUT1[ix]
            lo = ((lo << 4) & 0xFF) ^ LUT0[ix]
    return (hi << 8) | lo


def test_crc_matches_firmware():
    rnd = random.Random(1)
    for _ in range(200):
        d = bytes(rnd.randrange(256) for _ in range(70))
        assert crc16(d) == firmware_crc(d)
    assert len(make_packet(b"\x55\x04")) == 72


def make_rom(banks=4):
    rnd = random.Random(42)
    rom = bytearray(rnd.randrange(256) for _ in range(banks * 0x4000))
    rom[0x104:0x134] = LOGO
    rom[0x134:0x144] = b"TESTGAME".ljust(16, b"\x00")
    rom[0x147] = 0x1B
    return bytes(rom)


def test_flash_and_verify():
    dev = FakeCartFlasher()
    fl = CartFlasher(dev, mbc="MBC5", algorithm="ALG16", wait_mode="TOGGLE")
    rom = make_rom(4)
    prog = []
    fl.flash_rom(rom, erase=True, verify=True, progress=lambda d, t, m: prog.append((d, t)), log=lambda m: None)
    assert bytes(dev.flash[:len(rom)]) == rom
    assert prog[-1][0] == prog[-1][1]
    assert any(l.startswith("config op=2 mbc=5 alg=0 wait=3 banks=4") for l in dev.log)
    info = fl.status()
    assert info.title == "TESTGAME" and info.logo_ok and info.cart_type_name == "MBC5+RAM+BATTERY"


def test_retransmission_on_crc_errors():
    dev = FakeCartFlasher(fault_every=37)
    fl = CartFlasher(dev)
    rom = make_rom(2)
    fl.erase_flash()
    fl.write_rom(rom)
    back = fl.read_rom(len(rom))
    assert back == rom
    assert dev.nak_count > 10


def test_sram_roundtrip():
    dev = FakeCartFlasher()
    fl = CartFlasher(dev)
    save = bytes(range(256)) * 32
    fl.write_sram(save)
    assert fl.read_sram(1) == save
