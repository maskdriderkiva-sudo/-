"""GB Cart Flasher ファームウェアの挙動を再現する仮想デバイス (テスト用)。

cubicstyle/GBCartFlasher-1 の main / wait_packet / write_rom_ram / read_rom_ram / send_status /
erase_data の振る舞いを Python で模倣する。fault_every を指定すると N パケットに 1 回
CRC を壊して NAK 経路も検証できる。
"""
from __future__ import annotations

from collections import deque

from gbforge.flasher import (ACK, DATA, END, FRAME_SIZE, NAK, OP_RRAM, OP_RROM, OP_WRAM, OP_WROM,
                             PACKET_SIZE, T_CONFIG, T_ERASE, T_LAST, T_NORMAL, T_STATUS, check_packet,
                             make_packet)

LOGO = bytes.fromhex("CEED6666CC0D000B03730083000C000D0008111F8889000EDCCC6EE6DDDDD999BBBB67636E0EECCCDDDC999FBBB9333E")


class FakeCartFlasher:
    def __init__(self, rom_size: int = 0x100000, fault_every: int = 0):
        self.flash = bytearray(b"\xFF" * rom_size)
        self.sram = bytearray(0x8000)
        self.timeout = 2.0
        self.rx = bytearray()
        self.tx: deque[int] = deque()
        self.mode = "idle"
        self.fault_every = fault_every
        self.counter = 0
        self.nak_count = 0
        self.log: list[str] = []

    # ---- Transport ----
    def write(self, data: bytes) -> int:
        for b in data:
            self._byte(b)
        return len(data)

    def read(self, n: int) -> bytes:
        out = bytearray()
        while self.tx and len(out) < n:
            out.append(self.tx.popleft())
        return bytes(out)

    def reset_input_buffer(self):
        self.tx.clear()

    def close(self):
        pass

    # ---- 内部 ----
    def _send(self, data: bytes):
        self.tx.extend(data)

    def _byte(self, b: int):
        if self.mode == "reading":
            self._read_response(b)
            return
        self.rx.append(b)
        if len(self.rx) < PACKET_SIZE:
            return
        pkt = bytes(self.rx)
        self.rx.clear()
        self.counter += 1
        if self.fault_every and self.counter % self.fault_every == 0:
            pkt = pkt[:-1] + bytes(((pkt[-1] + 1) & 0xFF,))  # 伝送エラーを模擬
        if self.mode == "writing":
            self._write_packet(pkt)
        else:
            self._command(pkt)

    def _command(self, p: bytes):
        if not check_packet(p) or p[0] != DATA:
            self._send(bytes((NAK,)))
            return
        t = p[1]
        if t == T_STATUS:
            self.log.append("status")
            r = bytearray(PACKET_SIZE - 2)
            r[0] = DATA
            r[2], r[3] = 0x02, 0x50
            r[4], r[5], r[6] = 0xBF, 0xB7, 0x00   # SST 39SF040
            r[8] = 1 if self.flash[0x104:0x134] == LOGO else 0
            r[9:25] = self.flash[0x134:0x144]
            r[25:37] = self.flash[0x144:0x150]
            self._send(make_packet(bytes(r)))
        elif t == T_ERASE:
            self.log.append("erase")
            if p[2] == 0:
                self.flash[:] = b"\xFF" * len(self.flash)
            else:
                self.sram[:] = bytes(len(self.sram))
            self._send(bytes((ACK,)))
        elif t == T_CONFIG:
            op = p[2]
            self.num_banks = (p[6] << 8) | p[7]
            self.log.append(f"config op={op} mbc={p[3]} alg={p[4]} wait={p[5]} banks={self.num_banks + 1}")
            if op in (OP_WROM, OP_WRAM):
                self.target = self.flash if op == OP_WROM else self.sram
                self.per_bank = 256 if op == OP_WROM else 128
                self.exp_bank, self.exp_idx = 0, 0
                self.mode = "writing"
                self._send(bytes((ACK,)))
            elif op in (OP_RROM, OP_RRAM):
                self.target = self.flash if op == OP_RROM else self.sram
                self.per_bank = 256 if op == OP_RROM else 128
                self.rd_bank, self.rd_idx = 0, 0
                self.mode = "reading"
                self._send_read_packet()
            else:
                self._send(bytes((NAK,)))
        else:
            self._send(bytes((NAK,)))

    def _write_packet(self, p: bytes):
        if not check_packet(p):
            self.nak_count += 1
            self._send(bytes((NAK,)))
            return
        if p[0] != DATA or p[1] not in (T_NORMAL, T_LAST):
            self._send(bytes((END,)))
            self.mode = "idle"
            return
        bank = (p[4] << 8) | p[5]
        idx = p[3]
        if (idx, bank) != (self.exp_idx & 0xFF, self.exp_bank):
            self._send(bytes((END,)))
            self.mode = "idle"
            return
        off = (bank * self.per_bank + idx) * FRAME_SIZE
        if self.target is self.flash:
            # フラッシュは 1→0 にしか書けない
            for i, v in enumerate(p[6:6 + FRAME_SIZE]):
                self.target[off + i] &= v
        else:
            self.target[off:off + FRAME_SIZE] = p[6:6 + FRAME_SIZE]
        self._send(bytes((ACK,)))
        if p[1] == T_LAST:
            self.mode = "idle"
            return
        self.exp_idx += 1
        if self.exp_idx >= self.per_bank:
            self.exp_idx = 0
            self.exp_bank += 1
            if self.exp_bank > self.num_banks:
                self.mode = "idle"

    def _send_read_packet(self):
        last = self.rd_bank == self.num_banks and self.rd_idx == self.per_bank - 1
        off = (self.rd_bank * self.per_bank + self.rd_idx) * FRAME_SIZE
        body = bytes((DATA, T_LAST if last else T_NORMAL, 0, self.rd_idx, self.rd_bank >> 8, self.rd_bank & 0xFF))
        pkt = make_packet(body + bytes(self.target[off:off + FRAME_SIZE]))
        self.counter += 1
        if self.fault_every and self.counter % self.fault_every == 0:
            pkt = pkt[:10] + bytes(((pkt[10] ^ 0x5A),)) + pkt[11:]
        self._send(pkt)

    def _read_response(self, b: int):
        if b == END:
            self.mode = "idle"
            return
        if b != ACK:
            self.nak_count += 1
            self._send_read_packet()  # 再送
            return
        self.rd_idx += 1
        if self.rd_idx >= self.per_bank:
            self.rd_idx = 0
            self.rd_bank += 1
        if self.rd_bank > self.num_banks:
            self.mode = "idle"
            return
        self._send_read_packet()
