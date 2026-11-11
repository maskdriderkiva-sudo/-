"""CUBIC STYLE 版 GB Cart Flasher (ATmega8515 + FT232RL) ドライバ。

ファームウェア (cubicstyle/GBCartFlasher-1, Kraku & Chroost / Tauwasser 実装) のプロトコル:

* UART 8N1。ボーレートはジャンパで 125000 / 187500 (既定) / 375000
* パケットは 72 バイト固定: ペイロード 70 バイト + CRC16 (CCITT/XMODEM, 初期値0) 上位・下位
* 1 バイト応答: ACK=0xAA, NAK=0xF0 (再送要求), END=0x0F (中止)
* 要求ヘッダ: [0x55, type, operation, mbc, algorithm, wait_mode, banks_hi, banks_lo]
  - type: CONFIG=0, NORMAL_DATA=1, LAST_DATA=2, ERASE=3, STATUS=4
  - CONFIG の operation: RROM=0, RRAM=1, WROM=2, WRAM=3
* データパケット: [0x55, type, 0, packet_index, bank_hi, bank_lo, 64 バイト]
  ROM は 1 バンク = 256 パケット, SRAM は 1 バンク = 128 パケット
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Protocol

PACKET_SIZE = 72
FRAME_SIZE = 64
ACK, NAK, END, DATA = 0xAA, 0xF0, 0x0F, 0x55
T_CONFIG, T_NORMAL, T_LAST, T_ERASE, T_STATUS = 0, 1, 2, 3, 4
OP_RROM, OP_RRAM, OP_WROM, OP_WRAM = 0, 1, 2, 3
OP_EFLA, OP_ERAM = 0, 1
READ_INFO = 1

MBC = {"AUTO": 0, "MBC1": 1, "MBC2": 2, "MBC3": 3, "ROMONLY": 4, "MBC5": 5, "RUMBLE": 6}
ALGORITHM = {"ALG16": 0, "ALG12": 1, "ALG16WR": 2, "ALG12_M29F": 3}
WAIT_MODE = {"LONGER": 0, "DEFAULT": 1, "DATA_POLL": 2, "TOGGLE": 3}
BAUD_RATES = (125000, 187500, 375000)
FTDI_IDS = {(0x0403, 0x6001), (0x0403, 0x6015)}

FLASH_MAKERS = {0x01: "AMD/Spansion", 0x04: "Fujitsu", 0x1F: "Atmel", 0x20: "ST", 0x37: "AMIC",
                0x89: "Intel", 0xBF: "SST", 0xC2: "Macronix", 0xDA: "Winbond", 0xC8: "GigaDevice"}
CART_TYPES = {0x00: "ROM ONLY", 0x01: "MBC1", 0x02: "MBC1+RAM", 0x03: "MBC1+RAM+BATTERY", 0x05: "MBC2",
              0x06: "MBC2+BATTERY", 0x0F: "MBC3+TIMER+BATTERY", 0x10: "MBC3+TIMER+RAM+BATTERY", 0x11: "MBC3",
              0x13: "MBC3+RAM+BATTERY", 0x19: "MBC5", 0x1A: "MBC5+RAM", 0x1B: "MBC5+RAM+BATTERY",
              0x1C: "MBC5+RUMBLE", 0x1E: "MBC5+RUMBLE+RAM+BATTERY"}


class FlasherError(Exception):
    pass


class Transport(Protocol):
    timeout: float | None

    def write(self, data: bytes) -> int: ...
    def read(self, n: int) -> bytes: ...
    def reset_input_buffer(self) -> None: ...
    def close(self) -> None: ...


def crc16(data: bytes) -> int:
    """CRC-16/XMODEM (多項式 0x1021, 初期値 0)。ファームウェアのニブルテーブル実装と同一。"""
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


def make_packet(payload: bytes) -> bytes:
    if len(payload) > PACKET_SIZE - 2:
        raise ValueError("payload too long")
    p = payload.ljust(PACKET_SIZE - 2, b"\x00")
    c = crc16(p)
    return p + bytes((c >> 8, c & 0xFF))


def check_packet(pkt: bytes) -> bool:
    return len(pkt) == PACKET_SIZE and crc16(pkt[:-2]) == (pkt[-2] << 8 | pkt[-1])


@dataclass
class CartInfo:
    firmware: str
    flash_maker_id: int
    flash_device_id: int
    sector_protect: int
    logo_ok: bool
    title: str
    cart_type: int
    rom_size_code: int
    ram_size_code: int
    header_checksum: int
    global_checksum: int

    @property
    def flash_maker(self) -> str:
        return FLASH_MAKERS.get(self.flash_maker_id, f"不明(0x{self.flash_maker_id:02X})")

    @property
    def cart_type_name(self) -> str:
        return CART_TYPES.get(self.cart_type, f"0x{self.cart_type:02X}")

    def as_dict(self) -> dict:
        return {
            "firmware": self.firmware, "flash_maker": self.flash_maker,
            "flash_maker_id": f"0x{self.flash_maker_id:02X}", "flash_device_id": f"0x{self.flash_device_id:02X}",
            "logo_ok": self.logo_ok, "title": self.title, "cart_type": self.cart_type_name,
            "rom_kb": (32 << self.rom_size_code) if self.rom_size_code < 9 else None,
        }


ProgressFn = Callable[[int, int, str], None]


def _noop(done: int, total: int, msg: str):
    pass


class CartFlasher:
    def __init__(self, transport: Transport, mbc: str = "MBC5", algorithm: str = "ALG16",
                 wait_mode: str = "TOGGLE", retries: int = 8):
        self.t = transport
        self.mbc = MBC[mbc]
        self.alg = ALGORITHM[algorithm]
        self.wait = WAIT_MODE[wait_mode]
        self.retries = retries

    # ---- 接続 ----
    @classmethod
    def open(cls, port: str, baud: int = 187500, **kw) -> "CartFlasher":
        try:
            import serial
        except ImportError as e:  # pragma: no cover
            raise FlasherError("pyserial が必要です: pip install pyserial") from e
        try:
            s = serial.Serial(port, baudrate=baud, bytesize=8, parity="N", stopbits=1, timeout=2.0)
        except serial.SerialException as e:
            raise FlasherError(f"ポート {port} を開けません: {e}") from e
        time.sleep(0.05)
        s.reset_input_buffer()
        return cls(s, **kw)

    def close(self):
        self.t.close()

    # ---- 低レベル ----
    def _read_exact(self, n: int, timeout: float) -> bytes:
        old = self.t.timeout
        self.t.timeout = timeout
        try:
            buf = b""
            deadline = time.monotonic() + timeout
            while len(buf) < n and time.monotonic() < deadline + 0.05:
                chunk = self.t.read(n - len(buf))
                if chunk:
                    buf += chunk
                elif time.monotonic() >= deadline:
                    break
            return buf
        finally:
            self.t.timeout = old

    def _read_byte(self, timeout: float = 2.0) -> int | None:
        b = self._read_exact(1, timeout)
        return b[0] if b else None

    def _request(self, ptype: int, op: int, banks: int = 0) -> bytes:
        nb = max(0, banks - 1)
        return make_packet(bytes((DATA, ptype, op, self.mbc, self.alg, self.wait, nb >> 8, nb & 0xFF)))

    # ---- コマンド ----
    def status(self) -> CartInfo:
        """ファームウェア版数・フラッシュID・カートリッジヘッダを読む。"""
        last = None
        for _ in range(3):
            self.t.reset_input_buffer()
            self.t.write(self._request(T_STATUS, READ_INFO))
            pkt = self._read_exact(PACKET_SIZE, 3.0)
            if check_packet(pkt):
                return self._parse_info(pkt)
            last = pkt
        if not last:
            raise FlasherError("ロムライタから応答がありません。ポート・ボーレート・USB接続を確認してください")
        raise FlasherError(f"ステータス応答が不正です ({len(last)} バイト, CRC 不一致)")

    @staticmethod
    def _parse_info(p: bytes) -> CartInfo:
        raw = bytes(p[9:25]).split(b"\x00")[0]
        title = "".join(chr(c) for c in raw if 0x20 <= c < 0x7F).strip()   # 空のフラッシュ (0xFF) は空文字
        return CartInfo(
            firmware=f"{p[2] >> 4}{p[2] & 15}.{p[3] >> 4}{p[3] & 15}",
            flash_maker_id=p[4], flash_device_id=p[5], sector_protect=p[6], logo_ok=bool(p[8]),
            title=title, cart_type=p[28], rom_size_code=p[29], ram_size_code=p[30],
            header_checksum=p[34], global_checksum=(p[35] << 8) | p[36])

    def erase_flash(self, timeout: float = 60.0):
        """フラッシュ全消去 (完了まで最大数十秒)。"""
        self.t.reset_input_buffer()
        self.t.write(make_packet(bytes((DATA, T_ERASE, OP_EFLA, self.mbc, self.alg, self.wait))))
        r = self._read_byte(timeout)
        if r != ACK:
            raise FlasherError(f"消去に失敗しました (応答 {r if r is None else hex(r)})")

    def erase_sram(self, ram_banks: int = 1):
        self.t.reset_input_buffer()
        self.t.write(make_packet(bytes((DATA, T_ERASE, OP_ERAM, self.mbc, max(0, ram_banks - 1), self.wait))))
        r = self._read_byte(10.0)
        if r != ACK:
            raise FlasherError("SRAM の消去に失敗しました")

    def _write(self, data: bytes, op: int, per_bank: int, progress: ProgressFn, label: str):
        bank_bytes = per_bank * FRAME_SIZE
        if len(data) % bank_bytes:
            data = data + b"\xFF" * (bank_bytes - len(data) % bank_bytes)
        banks = len(data) // bank_bytes
        total = len(data) // FRAME_SIZE
        self.t.reset_input_buffer()
        self.t.write(self._request(T_CONFIG, op, banks))
        r = self._read_byte(3.0)
        if r != ACK:
            raise FlasherError(f"書き込み開始が拒否されました (応答 {r if r is None else hex(r)})")
        n = 0
        for bank in range(banks):
            for idx in range(per_bank):
                off = (bank * per_bank + idx) * FRAME_SIZE
                last = n == total - 1
                pkt = make_packet(bytes((DATA, T_LAST if last else T_NORMAL, 0, idx & 0xFF, bank >> 8, bank & 0xFF))
                                  + data[off:off + FRAME_SIZE])
                for attempt in range(self.retries + 1):
                    self.t.write(pkt)
                    r = self._read_byte(3.0)
                    if r == ACK:
                        break
                    if r == END:
                        raise FlasherError(f"ロムライタが書き込みを中止しました (バンク {bank}, パケット {idx})")
                    # NAK / タイムアウト → 再送
                else:
                    raise FlasherError(f"書き込みに失敗しました (バンク {bank}, パケット {idx})")
                n += 1
                if n % 16 == 0 or last:
                    progress(n, total, label)

    def _read(self, banks: int, op: int, per_bank: int, progress: ProgressFn, label: str) -> bytes:
        total = banks * per_bank
        out = bytearray(total * FRAME_SIZE)
        self.t.reset_input_buffer()
        self.t.write(self._request(T_CONFIG, op, banks))
        n = 0
        fails = 0
        while n < total:
            pkt = self._read_exact(PACKET_SIZE, 3.0)
            if not check_packet(pkt) or pkt[0] != DATA:
                fails += 1
                if fails > self.retries or not pkt:
                    self.t.write(bytes((END,)))
                    raise FlasherError(f"読み込みに失敗しました ({n}/{total} パケット)")
                self.t.reset_input_buffer()
                self.t.write(bytes((NAK,)))
                continue
            bank = (pkt[4] << 8) | pkt[5]
            idx = pkt[3]
            pos = bank * per_bank + idx
            if pos != n:
                if pos == n - 1:      # ACK が失われて再送された
                    self.t.write(bytes((ACK,)))
                    continue
                self.t.write(bytes((END,)))
                raise FlasherError(f"パケット順序エラー (期待 {n}, 受信 {pos})")
            out[n * FRAME_SIZE:(n + 1) * FRAME_SIZE] = pkt[6:6 + FRAME_SIZE]
            fails = 0
            self.t.write(bytes((ACK,)))
            n += 1
            if n % 16 == 0 or n == total:
                progress(n, total, label)
        return bytes(out)

    def write_rom(self, rom: bytes, progress: ProgressFn = _noop):
        if len(rom) < 0x8000:
            rom = rom + b"\xFF" * (0x8000 - len(rom))
        self._write(rom, OP_WROM, 256, progress, "書き込み")

    def read_rom(self, size: int, progress: ProgressFn = _noop) -> bytes:
        return self._read(max(2, size // 0x4000), OP_RROM, 256, progress, "読み込み")

    def write_sram(self, data: bytes, progress: ProgressFn = _noop):
        self._write(data, OP_WRAM, 128, progress, "SRAM 書き込み")

    def read_sram(self, banks: int = 1, progress: ProgressFn = _noop) -> bytes:
        return self._read(banks, OP_RRAM, 128, progress, "SRAM 読み込み")

    # ---- 高レベル ----
    def flash_rom(self, rom: bytes, erase: bool = True, verify: bool = True,
                  progress: ProgressFn = _noop, log: Callable[[str], None] = print) -> bool:
        info = self.status()
        log(f"ロムライタ FW {info.firmware} / フラッシュ {info.flash_maker} (ID 0x{info.flash_device_id:02X})")
        if info.title:
            log(f"現在のカートリッジ: {info.title} [{info.cart_type_name}]")
        else:
            log("現在のカートリッジ: (空)")
        if erase:
            log("フラッシュを消去しています…")
            progress(0, 1, "消去")
            self.erase_flash()
        log(f"ROM を書き込んでいます ({len(rom) // 1024}KB)…")
        self.write_rom(rom, progress)
        if verify:
            log("ベリファイ中…")
            back = self.read_rom(len(rom), progress)
            for i in range(0, len(rom)):
                if back[i] != rom[i]:
                    raise FlasherError(f"ベリファイ失敗: アドレス 0x{i:06X} (書込 {rom[i]:02X}, 読出 {back[i]:02X})")
            log("ベリファイ OK")
        return True


def list_ports() -> list[dict]:
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        return []
    out = []
    for p in lp.comports():
        out.append({
            "device": p.device, "description": p.description or "",
            "likely": (p.vid, p.pid) in FTDI_IDS,
        })
    out.sort(key=lambda d: (not d["likely"], d["device"]))
    return out
