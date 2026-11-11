/*
 * GBForge ランタイム: 日本語テキスト表示
 *
 * フォント (美咲フォント 8x8) はビルダーがプロジェクト内で使われている文字だけを抽出し、
 * 1bpp (1文字 8 バイト) でROMバンクに格納する。表示時に 1 文字ずつ VRAM のスロットへ
 * 転送するので、漢字を何文字使っても VRAM は 54 タイルしか消費しない。
 *
 * ウィンドウ: 20x5 タイル (枠 + 18文字 x 3行), 画面下部 (WY=104)
 */
#include <gb/gb.h>
#include <string.h>
#include "gbforge.h"
#include "gbf_types.h"

#define WIN_Y        104U
#define TXT_COLS     18U
#define TXT_ROWS     3U
#define T_BORDER     ((uint8_t)(TILE_TEXT_BASE + 0U))   /* 8 タイル */
#define T_BLANK      ((uint8_t)(TILE_TEXT_BASE + 8U))
#define T_SLOT0      ((uint8_t)(TILE_TEXT_BASE + 9U))   /* 54 タイル */
#define T_MARK       ((uint8_t)(TILE_TEXT_BASE + 63U))  /* ▼ / ▶ */

uint8_t gbf_msg_open;
extern void gbf_redraw(void);

static int16_t msg_value;
static uint8_t cur_line, cur_col, last_line;
static uint8_t glyph_raw[8];
static uint8_t tile16[16];

/* 枠線 1bpp: TL, T, TR, L, R, BL, B, BR */
static const uint8_t border_1bpp[64] = {
    0x00,0x00,0x00,0x1F,0x10,0x10,0x10,0x10,
    0x00,0x00,0x00,0xFF,0x00,0x00,0x00,0x00,
    0x00,0x00,0x00,0xF8,0x08,0x08,0x08,0x08,
    0x10,0x10,0x10,0x10,0x10,0x10,0x10,0x10,
    0x08,0x08,0x08,0x08,0x08,0x08,0x08,0x08,
    0x10,0x10,0x10,0x10,0x1F,0x00,0x00,0x00,
    0x00,0x00,0x00,0x00,0xFF,0x00,0x00,0x00,
    0x08,0x08,0x08,0x08,0xF8,0x00,0x00,0x00,
};

static void expand_1bpp(const uint8_t *src) {
    uint8_t i;
    for (i = 0; i < 8; i++) { tile16[i << 1] = src[i]; tile16[(i << 1) + 1] = src[i]; }
}

static void load_glyph(uint8_t tile, uint16_t g) {
    const BankPtr *c = &gbf_font_chunks[g >> 11];
    uint8_t save = CURRENT_BANK;
    SWITCH_ROM(c->bank);
    memcpy(glyph_raw, c->ptr + ((g & (GLYPHS_PER_CHUNK - 1U)) << 3), 8);
    SWITCH_ROM(save);
    expand_1bpp(glyph_raw);
    set_bkg_data(tile, 1, tile16);
}

static uint16_t read_word(const BankPtr *t, uint16_t index) {
    uint16_t w;
    uint8_t save = CURRENT_BANK;
    const uint8_t *p = t->ptr + (index << 1);
    SWITCH_ROM(t->bank);
    w = p[0] | ((uint16_t)p[1] << 8);
    SWITCH_ROM(save);
    return w;
}

void gbf_text_init(void) {
    uint8_t i;
    for (i = 0; i < 8; i++) {
        expand_1bpp(&border_1bpp[i << 3]);
        set_bkg_data(T_BORDER + i, 1, tile16);
    }
    memset(tile16, 0, 16);
    set_bkg_data(T_BLANK, 1, tile16);
    gbf_msg_open = 0;
}

void msg_number(int16_t value) { msg_value = value; }

/* ---- ウィンドウ ---- */
static void clear_text_area(void) {
    fill_win_rect(1, 1, TXT_COLS, TXT_ROWS, T_BLANK);
    cur_line = 0; cur_col = 0;
}

static void open_window(void) {
    uint8_t t;
    t = (uint8_t)(T_BORDER + 0); set_win_tiles(0, 0, 1, 1, &t);
    fill_win_rect(1, 0, 18, 1, (uint8_t)(T_BORDER + 1));
    t = (uint8_t)(T_BORDER + 2); set_win_tiles(19, 0, 1, 1, &t);
    fill_win_rect(0, 1, 1, 3, (uint8_t)(T_BORDER + 3));
    fill_win_rect(19, 1, 1, 3, (uint8_t)(T_BORDER + 4));
    t = (uint8_t)(T_BORDER + 5); set_win_tiles(0, 4, 1, 1, &t);
    fill_win_rect(1, 4, 18, 1, (uint8_t)(T_BORDER + 6));
    t = (uint8_t)(T_BORDER + 7); set_win_tiles(19, 4, 1, 1, &t);
    clear_text_area();
    move_win(7, WIN_Y);
    gbf_msg_open = 1;
    gbf_redraw();
    SHOW_WIN;
}

static void close_window(void) {
    HIDE_WIN;
    gbf_msg_open = 0;
    gbf_redraw();
}

static void put_glyph(uint16_t g, uint8_t to_bg, uint8_t bx, uint8_t by) {
    uint8_t slot, tile;
    if (cur_col >= TXT_COLS) { cur_col = 0; cur_line++; }
    if (cur_line >= TXT_ROWS) return;
    slot = cur_line * TXT_COLS + cur_col;
    tile = T_SLOT0 + slot;
    if (g == GLYPH_SPACE) tile = T_BLANK;
    else load_glyph(tile, g);
    if (to_bg) set_bkg_tiles(bx + cur_col, by + cur_line, 1, 1, &tile);
    else set_win_tiles(1 + cur_col, 1 + cur_line, 1, 1, &tile);
    last_line = cur_line;
    cur_col++;
}

static void put_number(int16_t v, uint8_t to_bg, uint8_t bx, uint8_t by) {
    uint8_t digits[6], n = 0;
    uint16_t u;
    if (v < 0) { put_glyph(GLYPH_MINUS, to_bg, bx, by); u = (uint16_t)(-v); }
    else u = (uint16_t)v;
    do { digits[n++] = (uint8_t)(u % 10U); u /= 10U; } while (u);
    while (n) put_glyph(GLYPH_DIGIT0 + digits[--n], to_bg, bx, by);
}

static void wait_confirm(void) {
    uint8_t k, prev = joypad(), blink = 0, t;
    load_glyph(T_MARK, GLYPH_MORE);
    while (1) {
        wait_vbl_done();
        k = joypad();
        t = (++blink & 0x10U) ? (uint8_t)(T_BORDER + 6) : T_MARK;
        set_win_tiles(18, 4, 1, 1, &t);
        if ((k & ~prev) & (J_A | J_B)) break;
        prev = k;
    }
    t = (uint8_t)(T_BORDER + 6);
    set_win_tiles(18, 4, 1, 1, &t);
    sfx_beep(200);
}

/* テキストを描画する。wait_end=1 なら最後にボタン待ち */
static void run_text(uint16_t text_id, uint8_t wait_end) {
    const BankPtr *t = &gbf_texts[text_id];
    uint16_t i = 0, c;
    uint8_t k;
    while (1) {
        c = read_word(t, i++);
        if (c == TC_END) break;
        if (c == TC_NEWLINE) { cur_line++; cur_col = 0; if (cur_line < TXT_ROWS) continue; c = TC_PAGE; }
        if (c == TC_PAGE) { wait_confirm(); clear_text_area(); continue; }
        if (c == TC_NUMBER) put_number(msg_value, 0, 0, 0);
        else put_glyph(c, 0, 0, 0);
        k = joypad();
        if (!(k & (J_A | J_B)) || (cur_col & 3U) == 0) wait_vbl_done();
    }
    if (wait_end) wait_confirm();
}

void msg_show(uint16_t text_id) {
    open_window();
    run_text(text_id, 1);
    close_window();
}

uint8_t msg_choice(uint16_t text_id) {
    uint8_t sel = 0, k, prev, t, blank = T_BLANK, first;
    open_window();
    run_text(text_id, 0);
    first = last_line ? last_line - 1 : 0;
    load_glyph(T_MARK, GLYPH_CURSOR);
    prev = joypad();
    while (1) {
        t = T_MARK;
        set_win_tiles(1, 1 + first + sel, 1, 1, &t);
        set_win_tiles(1, 1 + first + (sel ^ 1U), 1, 1, &blank);
        wait_vbl_done();
        k = joypad();
        if ((k & ~prev) & (J_UP | J_DOWN)) { sel ^= 1U; sfx_beep(150); }
        if ((k & ~prev) & J_A) break;
        if ((k & ~prev) & J_B) { sel = 1; break; }
        prev = k;
    }
    sfx_beep(200);
    close_window();
    return sel;
}

void text_print(uint8_t x, uint8_t y, uint16_t text_id) {
    const BankPtr *t = &gbf_texts[text_id];
    uint16_t i = 0, c;
    cur_line = 0; cur_col = 0;
    while (1) {
        c = read_word(t, i++);
        if (c == TC_END) break;
        if (c == TC_NEWLINE || c == TC_PAGE) { cur_line++; cur_col = 0; continue; }
        if (c == TC_NUMBER) put_number(msg_value, 1, x, y);
        else put_glyph(c, 1, x, y);
    }
}

void text_clear(void) { cur_line = 0; cur_col = 0; }
