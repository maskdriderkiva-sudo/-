/* テスト専用: GBDK-2020 の gb/gb.h のうち GBForge エンジンが使う API だけを再現した最小シム。
 * 実際のビルドでは本物の GBDK-2020 を使用する。 */
#ifndef SHIM_GB_H
#define SHIM_GB_H
#include <stdint.h>

#define J_RIGHT  0x01U
#define J_LEFT   0x02U
#define J_UP     0x04U
#define J_DOWN   0x08U
#define J_A      0x10U
#define J_B      0x20U
#define J_SELECT 0x40U
#define J_START  0x80U

#define __REG(a) (*(volatile uint8_t *)(a))
#define P1_REG   __REG(0xFF00)
#define LCDC_REG __REG(0xFF40)
#define STAT_REG __REG(0xFF41)
#define SCY_REG  __REG(0xFF42)
#define SCX_REG  __REG(0xFF43)
#define LY_REG   __REG(0xFF44)
#define BGP_REG  __REG(0xFF47)
#define OBP0_REG __REG(0xFF48)
#define OBP1_REG __REG(0xFF49)
#define WY_REG   __REG(0xFF4A)
#define WX_REG   __REG(0xFF4B)
#define DIV_REG  __REG(0xFF04)
#define NR10_REG __REG(0xFF10)
#define NR11_REG __REG(0xFF11)
#define NR12_REG __REG(0xFF12)
#define NR13_REG __REG(0xFF13)
#define NR14_REG __REG(0xFF14)
#define NR50_REG __REG(0xFF24)
#define NR51_REG __REG(0xFF25)
#define NR52_REG __REG(0xFF26)

extern volatile uint8_t _current_bank;
#define CURRENT_BANK _current_bank
#define SWITCH_ROM(b) (_current_bank = (b), (*(volatile uint8_t *)0x2000) = (b))
#define ENABLE_RAM  ((*(volatile uint8_t *)0x0000) = 0x0A)
#define DISABLE_RAM ((*(volatile uint8_t *)0x0000) = 0x00)
#define SWITCH_RAM(b) ((*(volatile uint8_t *)0x4000) = (b))

#define DISPLAY_ON   (LCDC_REG |= 0x80U)
#define SHOW_BKG     (LCDC_REG |= 0x01U)
#define HIDE_BKG     (LCDC_REG &= ~0x01U)
#define SHOW_SPRITES (LCDC_REG |= 0x02U)
#define HIDE_SPRITES (LCDC_REG &= ~0x02U)
#define SHOW_WIN     (LCDC_REG |= 0x20U)
#define HIDE_WIN     (LCDC_REG &= ~0x20U)
#define SPRITES_8x8  (LCDC_REG &= ~0x04U)
void display_off(void);
#define DISPLAY_OFF display_off()

typedef struct OAM_item_t { uint8_t y, x, tile, prop; } OAM_item_t;
extern volatile OAM_item_t shadow_OAM[40];

uint8_t joypad(void);
void wait_vbl_done(void);
#define vsync wait_vbl_done
void delay(uint16_t d);

void set_bkg_data(uint8_t first_tile, uint8_t nb_tiles, const uint8_t *data);
void set_sprite_data(uint8_t first_tile, uint8_t nb_tiles, const uint8_t *data);
void set_bkg_tiles(uint8_t x, uint8_t y, uint8_t w, uint8_t h, const uint8_t *tiles);
void set_win_tiles(uint8_t x, uint8_t y, uint8_t w, uint8_t h, const uint8_t *tiles);
void fill_bkg_rect(uint8_t x, uint8_t y, uint8_t w, uint8_t h, uint8_t tile);
void fill_win_rect(uint8_t x, uint8_t y, uint8_t w, uint8_t h, uint8_t tile);

static inline void move_bkg(uint8_t x, uint8_t y) { SCX_REG = x; SCY_REG = y; }
static inline void move_win(uint8_t x, uint8_t y) { WX_REG = x; WY_REG = y; }
static inline void set_sprite_tile(uint8_t nb, uint8_t tile) { shadow_OAM[nb].tile = tile; }
static inline void set_sprite_prop(uint8_t nb, uint8_t prop) { shadow_OAM[nb].prop = prop; }
static inline void move_sprite(uint8_t nb, uint8_t x, uint8_t y) { shadow_OAM[nb].y = y; shadow_OAM[nb].x = x; }
static inline void hide_sprite(uint8_t nb) { shadow_OAM[nb].y = 0; }

#define S_FLIPX 0x20U
#define S_FLIPY 0x40U
#define S_PRIORITY 0x80U
#endif
