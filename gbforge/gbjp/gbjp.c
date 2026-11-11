/*
 * gbjp - GBDK-2020 用 日本語フォントライブラリ (実装)
 * フォントデータ (gbjp_font_*.c) は `gbforge jpfont` で生成する。
 */
#include <gbdk/platform.h>
#include <stdint.h>
#include <string.h>
#include "gbjp.h"

/* ---- 生成されたフォントデータ (lib/gbjp/font/) ---- */
extern const uint16_t gbjp_count;              /* 文字数 */
extern const uint16_t gbjp_codes[];            /* コードポイント (昇順, バンク配置) */
extern const uint8_t *const gbjp_chunks[];     /* 1024 文字ごとのグリフ (1 文字 8 バイト) */
uint8_t gbjp_codes_bank(void);
uint8_t gbjp_chunk_bank(uint8_t chunk);

#define JP_MAX_SLOTS 128U
#define JP_NONE 0xFFFFU

static uint8_t first_tile, slot_count, next_slot;
static uint16_t slot_glyph[JP_MAX_SLOTS];
static uint8_t fg_color = 3, bg_color = 0, cur_layer = JP_BKG, cur_pal = 0, cur_speed = 0;
static uint8_t raw[8];
static uint8_t tile_buf[16];

/* 行頭に来てはいけない文字 (行頭禁則) */
static const uint16_t kinsoku[] = {
    0x3001, 0x3002, 0xFF0C, 0xFF0E, 0xFF01, 0xFF1F, 0x300D, 0x300F, 0xFF09, 0x30FC, 0x301C, 0x2026, 0x30FB,
    0x3041, 0x3043, 0x3045, 0x3047, 0x3049, 0x3063, 0x3083, 0x3085, 0x3087, 0x308E,
    0x30A1, 0x30A3, 0x30A5, 0x30A7, 0x30A9, 0x30C3, 0x30E3, 0x30E5, 0x30E7, 0x30EE,
    0x002C, 0x002E, 0x0021, 0x003F, 0x0029,
};

/* ------------------------------------------------------------------ */
static const char *utf8_next(const char *s, uint16_t *cp) {
    uint8_t c = (uint8_t)*s++;
    if (c < 0x80U) {
        *cp = c;
    } else if ((c & 0xE0U) == 0xC0U) {
        *cp = ((uint16_t)(c & 0x1FU) << 6) | ((uint8_t)*s++ & 0x3FU);
    } else if ((c & 0xF0U) == 0xE0U) {
        uint16_t v = (uint16_t)(c & 0x0FU) << 12;
        v |= (uint16_t)((uint8_t)*s++ & 0x3FU) << 6;
        v |= (uint8_t)*s++ & 0x3FU;
        *cp = v;
    } else {
        /* 4 バイト文字 (絵文字など) は非対応 */
        if (*s) s++;
        if (*s) s++;
        if (*s) s++;
        *cp = '?';
    }
    return s;
}

static uint16_t find_glyph(uint16_t cp) {
    uint16_t lo = 0, hi = gbjp_count, mid, v;
    uint8_t save = CURRENT_BANK;
    SWITCH_ROM(gbjp_codes_bank());
    while (lo < hi) {
        mid = (lo + hi) >> 1;
        v = gbjp_codes[mid];
        if (v == cp) { SWITCH_ROM(save); return mid; }
        if (v < cp) lo = mid + 1; else hi = mid;
    }
    SWITCH_ROM(save);
    return JP_NONE;
}

/* コードポイント → グリフ番号。空白は JP_NONE (空白タイル) */
static uint16_t glyph_of(uint16_t cp) {
    uint16_t g;
    if (cp == 0x20U || cp == 0x3000U) return JP_NONE;
    if (cp > 0x20U && cp < 0x7FU) {             /* 半角 → 全角 */
        g = find_glyph(cp + 0xFEE0U);
        if (g != JP_NONE) return g;
    }
    g = find_glyph(cp);
    if (g != JP_NONE) return g;
    g = find_glyph(0xFF1FU);                    /* ？ */
    return g;
}

static void load_tile(uint8_t tile, uint16_t g) {
    uint8_t i, b, lo, hi, save = CURRENT_BANK;
    const uint8_t *src = gbjp_chunks[g >> 10] + ((g & 1023U) << 3);
    SWITCH_ROM(gbjp_chunk_bank((uint8_t)(g >> 10)));
    memcpy(raw, src, 8);
    SWITCH_ROM(save);
    for (i = 0; i < 8; i++) {
        b = raw[i];
        lo = ((fg_color & 1U) ? b : 0U) | ((bg_color & 1U) ? (uint8_t)~b : 0U);
        hi = ((fg_color & 2U) ? b : 0U) | ((bg_color & 2U) ? (uint8_t)~b : 0U);
        tile_buf[i << 1] = lo;
        tile_buf[(i << 1) + 1] = hi;
    }
    set_bkg_data(tile, 1, tile_buf);
}

static void load_blank(void) {
    uint8_t lo = (bg_color & 1U) ? 0xFFU : 0x00U, hi = (bg_color & 2U) ? 0xFFU : 0x00U, i;
    for (i = 0; i < 16; i += 2) { tile_buf[i] = lo; tile_buf[i + 1] = hi; }
    set_bkg_data(first_tile, 1, tile_buf);
}

static uint8_t tile_for(uint16_t g) {
    uint8_t i;
    if (g == JP_NONE) return first_tile;
    for (i = 1; i < slot_count; i++)
        if (slot_glyph[i] == g) return first_tile + i;
    i = next_slot;
    if (++next_slot >= slot_count) next_slot = 1;   /* 足りなくなったら古いものから再利用 */
    slot_glyph[i] = g;
    load_tile(first_tile + i, g);
    return first_tile + i;
}

static void put_tile(uint8_t x, uint8_t y, uint8_t t) {
    if (cur_layer == JP_WIN) set_win_tiles(x, y, 1, 1, &t);
    else set_bkg_tiles(x, y, 1, 1, &t);
#if defined(__TARGET_gb) || defined(__TARGET_ap) || defined(__TARGET_duck)
    if (_cpu == CGB_TYPE) {
        VBK_REG = 1;
        if (cur_layer == JP_WIN) set_win_tiles(x, y, 1, 1, &cur_pal);
        else set_bkg_tiles(x, y, 1, 1, &cur_pal);
        VBK_REG = 0;
    }
#endif
}

static void wait_speed(void) {
    uint8_t n = cur_speed;
    while (n--) vsync();
}

static uint8_t is_kinsoku(uint16_t cp) {
    uint8_t i;
    for (i = 0; i < sizeof(kinsoku) / sizeof(kinsoku[0]); i++)
        if (kinsoku[i] == cp) return 1;
    return 0;
}

/* ------------------------------------------------------------------ */
void jp_init(uint8_t first, uint8_t count) {
    if (count > JP_MAX_SLOTS) count = JP_MAX_SLOTS;
    if (count < 2) count = 2;
    first_tile = first;
    slot_count = count;
    jp_reset();
}

void jp_reset(void) {
    uint8_t i;
    for (i = 0; i < JP_MAX_SLOTS; i++) slot_glyph[i] = JP_NONE;
    next_slot = 1;
    load_blank();
}

void jp_set_color(uint8_t fg, uint8_t bg) {
    fg_color = fg & 3U;
    bg_color = bg & 3U;
    jp_reset();   /* タイルに色が焼き込まれるため作り直す */
}

void jp_set_layer(uint8_t layer) { cur_layer = layer; }
void jp_set_palette(uint8_t palette) { cur_pal = palette & 7U; }
void jp_set_speed(uint8_t frames) { cur_speed = frames; }

uint8_t jp_has_char(uint16_t cp) { return find_glyph(cp) != JP_NONE; }

void jp_put_char(uint8_t x, uint8_t y, uint16_t cp) {
    put_tile(x, y, tile_for(glyph_of(cp)));
}

uint8_t jp_print(uint8_t x, uint8_t y, const char *s) {
    uint16_t cp;
    while (*s) {
        s = utf8_next(s, &cp);
        if (cp == '\n') continue;
        put_tile(x++, y, tile_for(glyph_of(cp)));
        wait_speed();
    }
    return x;
}

const char *jp_print_box(uint8_t x, uint8_t y, uint8_t w, uint8_t h, const char *s) {
    uint8_t col = 0, row = 0;
    uint16_t cp, ncp;
    const char *p;
    while (*s) {
        p = utf8_next(s, &cp);
        if (cp == '\n') {
            s = p; col = 0;
            if (++row >= h) return *s ? s : NULL;
            continue;
        }
        if (col >= w) {
            col = 0;
            if (++row >= h) return s;
            if (cp == 0x20U || cp == 0x3000U) { s = p; continue; }   /* 行頭の空白は捨てる */
        }
        /* 行末の文字の次が行頭禁則文字なら、この文字から次の行へ送る */
        if (col == w - 1U && *p && col > 0) {
            utf8_next(p, &ncp);
            if (is_kinsoku(ncp) && !is_kinsoku(cp)) {
                col = 0;
                if (++row >= h) return s;
            }
        }
        put_tile(x + col, y + row, tile_for(glyph_of(cp)));
        wait_speed();
        col++;
        s = p;
    }
    return NULL;
}

uint8_t jp_print_int(uint8_t x, uint8_t y, int16_t value) {
    char buf[8];
    uint8_t n = 0, i;
    uint16_t u;
    if (value < 0) { put_tile(x++, y, tile_for(glyph_of('-'))); u = (uint16_t)(-value); }
    else u = (uint16_t)value;
    do { buf[n++] = (char)('0' + (u % 10U)); u /= 10U; } while (u);
    for (i = n; i; i--) put_tile(x++, y, tile_for(glyph_of((uint8_t)buf[i - 1])));
    return x;
}

void jp_clear_box(uint8_t x, uint8_t y, uint8_t w, uint8_t h) {
    if (cur_layer == JP_WIN) fill_win_rect(x, y, w, h, first_tile);
    else fill_bkg_rect(x, y, w, h, first_tile);
}

uint16_t jp_strlen(const char *s) {
    uint16_t n = 0, cp;
    while (*s) { s = utf8_next(s, &cp); n++; }
    return n;
}
