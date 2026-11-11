import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gbforge.assets import decode_tile, encode_tile  # noqa: E402
from gbforge.builder import BANK_SIZE, DataItem, detect_script_functions, finalize_header, pack_banks, rom_bank_count  # noqa: E402
from gbforge.font import FIXED_GLYPHS, TC_END, TC_NEWLINE, TC_NUMBER, TC_PAGE, GlyphTable, layout, load_font  # noqa: E402


def test_font_has_kana_kanji():
    f = load_font("misaki_gothic")
    for ch in "あア漢字。「」０Ａ▶▼":
        assert ch in f, ch
    assert len(f) > 6000


def test_fixed_glyph_order():
    g = GlyphTable()
    assert g.chars[:len(FIXED_GLYPHS)] == FIXED_GLYPHS
    assert g.index["０"] == 1 and g.index["９"] == 10


def test_encode_controls():
    g = GlyphTable()
    w = g.encode("あ\nい\n---\nう{n}")
    assert w[-1] == TC_END and TC_NEWLINE in w and TC_PAGE in w and TC_NUMBER in w
    # 半角は全角に
    assert g.encode("A")[0] == g.index["Ａ"]


def test_autowrap_and_kinsoku():
    pages = layout("あ" * 17 + "い。")
    lines = pages[0]
    assert len(lines) == 2
    assert [t[1] for t in lines[1]] == ["い", "。"]   # 「。」は行頭に来ない
    assert len(layout("あ" * 18 * 4)) == 2              # 3 行で自動改ページ


def test_dakuten_combining():
    g = GlyphTable()
    assert g.encode("が")[0] == g.index["が"]


def test_tile_roundtrip():
    px = [[(x + y) & 3 for x in range(8)] for y in range(8)]
    assert decode_tile(encode_tile(px)) == px


def test_pack_banks():
    items = [DataItem(f"d{i}", bytes(n), "x") for i, n in enumerate([9000, 9000, 7000, 100])]
    last = pack_banks(items)
    for b in range(2, last + 1):
        assert sum(len(i.data) for i in items if i.bank == b) <= BANK_SIZE
    assert last == 3
    assert rom_bank_count(3) == 4 and rom_bank_count(5) == 8


def test_detect_script_functions():
    code = "void Foo_start(Actor *self) {}\n/* void Foo_update(Actor *self) */\nvoid Foo_interact( struct Actor * a){}"
    assert detect_script_functions("Foo", code) == {"start": True, "update": False, "interact": True}


def test_header_checksum():
    rom = bytearray(0x10000)
    finalize_header(rom, "Hello", 4)
    assert rom[0x134:0x139] == b"HELLO" and rom[0x147] == 0x1B and rom[0x148] == 1
    assert ((sum(rom) - rom[0x14E] - rom[0x14F]) & 0xFFFF) == (rom[0x14E] << 8 | rom[0x14F])
