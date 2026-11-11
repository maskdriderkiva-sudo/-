"""美咲フォント TTF から 8x8 1bpp ビットマップを抽出し gbforge/fonts/*.bin を生成する開発用スクリプト。

出力形式: b"GBFF" + uint32 グリフ数 + [uint32 codepoint, 8 bytes bitmap] * N (codepoint 昇順)
"""
import struct, sys
from PIL import Image, ImageDraw, ImageFont

def candidates():
    cps = set(range(0x20, 0x7F))
    # JIS X 0208 全域 (Shift_JIS 2バイト) + 半角カナ
    for hi in list(range(0x81, 0xA0)) + list(range(0xE0, 0xF0)):
        for lo in range(0x40, 0xFD):
            try:
                cps.add(ord(bytes([hi, lo]).decode("shift_jis")))
            except Exception:
                pass
    cps.update(range(0xFF61, 0xFFA0))
    return sorted(cps)

def render(font, ch):
    im = Image.new("1", (8, 8), 0)
    d = ImageDraw.Draw(im)
    d.fontmode = "1"
    d.text((0, 0), ch, font=font, fill=1)
    rows = []
    for y in range(8):
        b = 0
        for x in range(8):
            if im.getpixel((x, y)):
                b |= 0x80 >> x
        rows.append(b)
    return bytes(rows)

def main(ttf, out):
    font = ImageFont.truetype(ttf, 8)
    glyphs = []
    for cp in candidates():
        ch = chr(cp)
        bm = render(font, ch)
        if any(bm) or ch in " 　":
            glyphs.append((cp, bm))
    with open(out, "wb") as f:
        f.write(b"GBFF" + struct.pack("<I", len(glyphs)))
        for cp, bm in glyphs:
            f.write(struct.pack("<I", cp) + bm)
    print(out, len(glyphs), "glyphs")

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
