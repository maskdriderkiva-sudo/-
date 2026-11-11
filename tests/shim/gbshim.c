/* テスト専用 GBDK 互換シムの実装 */
#include <gb/gb.h>

volatile uint8_t _current_bank;
volatile uint8_t shim_vbl_count;

#define VRAM_WAIT() while (STAT_REG & 0x02U)

uint8_t joypad(void) {
    uint8_t a, b;
    P1_REG = 0x20; a = P1_REG; a = P1_REG; a = ~a & 0x0F;   /* 十字キー */
    P1_REG = 0x10; b = P1_REG; b = P1_REG; b = P1_REG; b = ~b & 0x0F; /* ボタン */
    P1_REG = 0x30;
    return (uint8_t)(a | (b << 4));
}

void wait_vbl_done(void) {
    uint8_t c = shim_vbl_count;
    if (!(LCDC_REG & 0x80U)) return;
    while (c == shim_vbl_count) { __asm__("halt"); }
}

void delay(uint16_t d) { while (d--) { volatile uint8_t i = 100; while (i--) ; } }

void display_off(void) {
    if (!(LCDC_REG & 0x80U)) return;
    while (LY_REG != 145) ;
    LCDC_REG &= ~0x80U;
}

/* VRAM 書き込みは STAT 待ちの直後に行う (アドレス計算を待ちの前に済ませる) */
static void vram_copy(volatile uint8_t *dst, const uint8_t *src, uint16_t n) {
    uint8_t v;
    while (n--) { v = *src++; VRAM_WAIT(); *dst = v; dst++; }
}

void set_bkg_data(uint8_t first_tile, uint8_t nb_tiles, const uint8_t *data) {
    uint16_t n = nb_tiles ? nb_tiles : 256;
    vram_copy((volatile uint8_t *)(0x8000 + ((uint16_t)first_tile << 4)), data, n << 4);
}
void set_sprite_data(uint8_t first_tile, uint8_t nb_tiles, const uint8_t *data) {
    set_bkg_data(first_tile, nb_tiles, data);
}
static void map_copy(uint16_t base, uint8_t x, uint8_t y, uint8_t w, uint8_t h, const uint8_t *tiles) {
    uint8_t i, j;
    uint8_t *row;
    for (j = 0; j < h; j++) {
        row = (uint8_t *)(base + ((uint16_t)((uint8_t)(y + j) & 31U) << 5));
        for (i = 0; i < w; i++) {
            volatile uint8_t *p = row + ((uint8_t)(x + i) & 31U);
            uint8_t v = *tiles++;
            VRAM_WAIT();
            *p = v;
        }
    }
}
static void map_fill(uint16_t base, uint8_t x, uint8_t y, uint8_t w, uint8_t h, uint8_t tile) {
    uint8_t i, j;
    uint8_t *row;
    for (j = 0; j < h; j++) {
        row = (uint8_t *)(base + ((uint16_t)((uint8_t)(y + j) & 31U) << 5));
        for (i = 0; i < w; i++) {
            volatile uint8_t *p = row + ((uint8_t)(x + i) & 31U);
            VRAM_WAIT();
            *p = tile;
        }
    }
}
void set_bkg_tiles(uint8_t x, uint8_t y, uint8_t w, uint8_t h, const uint8_t *tiles) { map_copy(0x9800, x, y, w, h, tiles); }
void set_win_tiles(uint8_t x, uint8_t y, uint8_t w, uint8_t h, const uint8_t *tiles) { map_copy(0x9C00, x, y, w, h, tiles); }
void fill_bkg_rect(uint8_t x, uint8_t y, uint8_t w, uint8_t h, uint8_t tile) { map_fill(0x9800, x, y, w, h, tile); }
void fill_win_rect(uint8_t x, uint8_t y, uint8_t w, uint8_t h, uint8_t tile) { map_fill(0x9C00, x, y, w, h, tile); }
