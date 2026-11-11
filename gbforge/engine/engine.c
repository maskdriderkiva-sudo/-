/*
 * GBForge ランタイム: メインループ / シーン / アクター
 * バンク0 に配置される。ユーザースクリプトは CODE_BANK (通常1) に配置され、
 * メインループ中は常に CODE_BANK がマップされている。
 */
#include <gb/gb.h>
#include <string.h>
#include "gbforge.h"
#include "gbf_types.h"

Actor   actors[MAX_ACTORS];
uint8_t actor_count;
Actor  *player;
uint8_t keys, keys_pressed;
int16_t vars[NUM_VARS];
uint8_t current_scene;
int16_t camera_x, camera_y;

static uint8_t  pending_scene = 0xFFU;
static uint8_t  spawn_tx = 0xFFU, spawn_ty, spawn_dir;
static uint8_t  map_w, map_h;
static uint8_t  scene_bgp = 0xE4U;
static uint8_t  collision[32U * 32U];
static uint16_t rng_state;
static uint8_t  rng_seeded;
static uint8_t  tile_buf[64];

extern void gbf_text_init(void);   /* text.c */
extern uint8_t gbf_msg_open;

/* ------------------------------------------------------------------ */
/* バンク付きデータアクセス                                              */
/* ------------------------------------------------------------------ */
static void banked_memcpy(void *dst, uint8_t bank, const uint8_t *src, uint16_t n) {
    uint8_t save = CURRENT_BANK;
    SWITCH_ROM(bank);
    memcpy(dst, src, n);
    SWITCH_ROM(save);
}

/* ------------------------------------------------------------------ */
/* 乱数 / ユーティリティ                                                 */
/* ------------------------------------------------------------------ */
uint8_t random8(void) {
    if (!rng_seeded) { rng_state ^= (uint16_t)DIV_REG << 8 | DIV_REG; rng_seeded = 1; }
    if (!rng_state) rng_state = 0xACE1U;
    /* xorshift16 */
    rng_state ^= rng_state << 7;
    rng_state ^= rng_state >> 9;
    rng_state ^= rng_state << 8;
    return (uint8_t)rng_state;
}

void wait_frames(uint8_t n) {
    while (n--) wait_vbl_done();
}

static uint8_t fade_pal(uint8_t pal, uint8_t step) {
    /* step 0 = 真っ白, 3 = 元のパレット。各色を step 分だけ暗くする */
    uint8_t out = 0, i, c;
    for (i = 0; i < 8; i += 2) {
        c = (pal >> i) & 3U;
        c = (c > (3U - step)) ? (uint8_t)(c - (3U - step)) : 0U;
        out |= (uint8_t)(c << i);
    }
    return out;
}

void fade_out(void) {
    int8_t s;
    for (s = 3; s >= 0; s--) {
        BGP_REG = fade_pal(scene_bgp, (uint8_t)s);
        OBP0_REG = fade_pal(0xE0U, (uint8_t)s);
        wait_frames(4);
    }
}

void fade_in(void) {
    uint8_t s;
    for (s = 0; s < 4; s++) {
        BGP_REG = fade_pal(scene_bgp, s);
        OBP0_REG = fade_pal(0xE0U, s);
        wait_frames(4);
    }
}

void sfx_beep(uint8_t pitch) {
    uint16_t f = 1600U + ((uint16_t)pitch << 1);
    NR52_REG = 0x80U;
    NR50_REG = 0x77U;
    NR51_REG = 0xFFU;
    NR10_REG = 0x00U;
    NR11_REG = 0x80U;
    NR12_REG = 0x61U;
    NR13_REG = (uint8_t)f;
    NR14_REG = 0x80U | (uint8_t)((f >> 8) & 7U);
}

/* ------------------------------------------------------------------ */
/* セーブ (MBC5 + SRAM + バッテリー)                                      */
/* ------------------------------------------------------------------ */
#define SRAM ((volatile uint8_t *)0xA000)
void save_game(void) {
    uint8_t i;
    const uint8_t *p = (const uint8_t *)vars;
    ENABLE_RAM;
    SWITCH_RAM(0);
    SRAM[0] = 'G'; SRAM[1] = 'F';
    for (i = 0; i < sizeof(vars); i++) SRAM[2 + i] = p[i];
    DISABLE_RAM;
}

uint8_t load_game(void) {
    uint8_t i, ok;
    uint8_t *p = (uint8_t *)vars;
    ENABLE_RAM;
    SWITCH_RAM(0);
    ok = (SRAM[0] == 'G' && SRAM[1] == 'F');
    if (ok) for (i = 0; i < sizeof(vars); i++) p[i] = SRAM[2 + i];
    DISABLE_RAM;
    return ok;
}

/* ------------------------------------------------------------------ */
/* 当たり判定                                                           */
/* ------------------------------------------------------------------ */
uint8_t tile_solid(int16_t tx, int16_t ty) {
    if (tx < 0 || ty < 0 || tx >= map_w || ty >= map_h) return 1;
    return collision[(uint16_t)ty * map_w + (uint16_t)tx];
}

void set_tile_solid(uint8_t tx, uint8_t ty, uint8_t solid) {
    if (tx < map_w && ty < map_h) collision[(uint16_t)ty * map_w + tx] = solid;
}

/* ヒットボックス: x+2..x+13, y+8..y+15 (足元) */
#define HB_L 2
#define HB_R 13
#define HB_T 8
#define HB_B 15

static uint8_t box_hits_tiles(int16_t x, int16_t y) {
    int16_t l = (x + HB_L) >> 3, r = (x + HB_R) >> 3;
    int16_t t = (y + HB_T) >> 3, b = (y + HB_B) >> 3;
    if (x + HB_L < 0 || y + HB_T < 0) return 1;
    return tile_solid(l, t) | tile_solid(r, t) | tile_solid(l, b) | tile_solid(r, b);
}

static uint8_t boxes_overlap(int16_t ax, int16_t ay, int16_t bx, int16_t by) {
    return (ax + HB_L <= bx + HB_R) && (bx + HB_L <= ax + HB_R) &&
           (ay + HB_T <= by + HB_B) && (by + HB_T <= ay + HB_B);
}

uint8_t actor_overlap(Actor *a, Actor *b) {
    if (!(a->flags & AF_ACTIVE) || !(b->flags & AF_ACTIVE)) return 0;
    return boxes_overlap(a->x, a->y, b->x, b->y);
}

static uint8_t box_hits_actors(Actor *self, int16_t x, int16_t y) {
    uint8_t i;
    Actor *o = actors;
    for (i = 0; i < actor_count; i++, o++) {
        if (o == self) continue;
        if ((o->flags & (AF_ACTIVE | AF_SOLID | AF_HIDDEN)) != (AF_ACTIVE | AF_SOLID)) continue;
        if (boxes_overlap(x, y, o->x, o->y)) return 1;
    }
    return 0;
}

uint8_t actor_move(Actor *a, int8_t dx, int8_t dy) {
    uint8_t moved = 0;
    if (dx) {
        a->dir = dx < 0 ? DIR_LEFT : DIR_RIGHT;
        if (!box_hits_tiles(a->x + dx, a->y) && !box_hits_actors(a, a->x + dx, a->y)) { a->x += dx; moved = 1; }
    }
    if (dy) {
        if (!dx) a->dir = dy < 0 ? DIR_UP : DIR_DOWN;
        if (!box_hits_tiles(a->x, a->y + dy) && !box_hits_actors(a, a->x, a->y + dy)) { a->y += dy; moved = 1; }
    }
    a->flags |= AF_MOVING;
    return moved;
}

Actor *actor_get(uint8_t index) { return index < actor_count ? &actors[index] : NULL; }
void actor_show(Actor *a) { a->flags &= ~AF_HIDDEN; }
void actor_hide(Actor *a) { a->flags |= AF_HIDDEN; }
void actor_set_frame(Actor *a, uint8_t frame) { a->frame = frame; }
void actor_face(Actor *a, uint8_t dir) { a->dir = dir; }

/* ------------------------------------------------------------------ */
/* スプライト描画                                                        */
/* ------------------------------------------------------------------ */
static void actor_pick_frame(Actor *a) {
    uint8_t n;
    if (a->sprite == NO_SPRITE) return;
    n = gbf_sprites[a->sprite].frames;
    if (a->flags & AF_MOVING) a->anim_tick++;
    if (n == 4U) a->frame = a->dir;
    else if (n == 8U) a->frame = (uint8_t)((a->dir << 1) | ((a->flags & AF_MOVING) ? ((a->anim_tick >> 3) & 1U) : 0U));
    else if (n > 1U && a->behaviour != BHV_PLAYER && a->script == NO_SCRIPT) {
        a->anim_tick++;
        a->frame = (uint8_t)((a->anim_tick >> 3) % n);
    }
    if (a->frame >= n) a->frame = 0;
}

static void actor_upload(uint8_t i, Actor *a) {
    const SpriteDef *s;
    if (a->sprite == NO_SPRITE || a->loaded_frame == a->frame) return;
    s = &gbf_sprites[a->sprite];
    banked_memcpy(tile_buf, s->data.bank, s->data.ptr + ((uint16_t)a->frame << 6), 64);
    set_sprite_data(TILE_SPRITE_BASE + (i << 2), 4, tile_buf);
    a->loaded_frame = a->frame;
}

static void draw_actors(void) {
    uint8_t i, hw;
    int16_t sx, sy;
    Actor *a = actors;
    for (i = 0; i < MAX_ACTORS; i++, a++) {
        hw = i << 2;
        if (i >= actor_count || !(a->flags & AF_ACTIVE) || (a->flags & AF_HIDDEN) || a->sprite == NO_SPRITE) {
            hide_sprite(hw); hide_sprite(hw + 1); hide_sprite(hw + 2); hide_sprite(hw + 3);
            continue;
        }
        actor_pick_frame(a);
        actor_upload(i, a);
        sx = a->x - camera_x + 8;
        sy = a->y - camera_y + 16;
        if (sx <= -16 || sx >= 176 || sy <= -16 || sy >= 168 ||
            (gbf_msg_open && sy + 16 > 104 + 16)) {
            hide_sprite(hw); hide_sprite(hw + 1); hide_sprite(hw + 2); hide_sprite(hw + 3);
        } else {
            move_sprite(hw,     (uint8_t)sx,       (uint8_t)sy);
            move_sprite(hw + 1, (uint8_t)(sx + 8), (uint8_t)sy);
            move_sprite(hw + 2, (uint8_t)sx,       (uint8_t)(sy + 8));
            move_sprite(hw + 3, (uint8_t)(sx + 8), (uint8_t)(sy + 8));
        }
        a->flags &= ~AF_MOVING;
    }
}

void gbf_redraw(void) { draw_actors(); }

static void update_camera(void) {
    int16_t mx = (int16_t)map_w * 8 - 160, my = (int16_t)map_h * 8 - 144;
    if (player) {
        camera_x = player->x + 8 - 80;
        camera_y = player->y + 8 - 72;
    }
    if (camera_x > mx) camera_x = mx;
    if (camera_y > my) camera_y = my;
    if (camera_x < 0) camera_x = 0;
    if (camera_y < 0) camera_y = 0;
    move_bkg((uint8_t)camera_x, (uint8_t)camera_y);
}

/* ------------------------------------------------------------------ */
/* シーン                                                              */
/* ------------------------------------------------------------------ */
void scene_load(uint8_t scene) { pending_scene = scene; }

static void call_start(uint8_t script, Actor *self) {
    if (script != NO_SCRIPT && gbf_scripts[script].start) gbf_scripts[script].start(self);
}

static void scene_load_now(uint8_t id) {
    const SceneDef *sc = &gbf_scenes[id];
    const ActorDef *d;
    uint8_t i, save;
    Actor *a;

    if (LCDC_REG & 0x80U) fade_out();
    DISPLAY_OFF;
    HIDE_WIN;
    current_scene = id;
    map_w = sc->w; map_h = sc->h;
    scene_bgp = sc->bgp;

    save = CURRENT_BANK;
    SWITCH_ROM(sc->tiles.bank);
    set_bkg_data(TILE_BG_BASE, sc->ntiles, sc->tiles.ptr);
    SWITCH_ROM(sc->map.bank);
    set_bkg_tiles(0, 0, sc->w, sc->h, sc->map.ptr);
    SWITCH_ROM(sc->collision.bank);
    memcpy(collision, sc->collision.ptr, (uint16_t)sc->w * sc->h);
    SWITCH_ROM(save);

    actor_count = sc->actor_count > MAX_ACTORS ? MAX_ACTORS : sc->actor_count;
    player = NULL;
    memset(actors, 0, sizeof(actors));
    for (i = 0, d = sc->actors, a = actors; i < actor_count; i++, d++, a++) {
        a->x = d->x; a->y = d->y;
        a->sprite = d->sprite;
        a->behaviour = d->behaviour;
        a->script = d->script;
        a->flags = d->flags | AF_ACTIVE;
        a->dir = d->dir;
        a->speed = d->speed ? d->speed : 1;
        a->text = d->text;
        a->param[0] = d->param[0]; a->param[1] = d->param[1];
        a->param[2] = d->param[2]; a->param[3] = d->param[3];
        a->loaded_frame = 0xFFU;
        if (d->behaviour == BHV_PLAYER && !player) player = a;
    }
    if (player && spawn_tx != 0xFFU) {
        player->x = (int16_t)spawn_tx << 3;
        player->y = (int16_t)spawn_ty << 3;
        player->dir = spawn_dir;
    }
    spawn_tx = 0xFFU;

    for (i = 0; i < 40; i++) hide_sprite(i);
    update_camera();
    draw_actors();
    BGP_REG = 0x00U; OBP0_REG = 0x00U;
    DISPLAY_ON;
    fade_in();

    call_start(sc->script, NULL);
    for (i = 0, a = actors; i < actor_count; i++, a++) call_start(a->script, a);
}

/* ------------------------------------------------------------------ */
/* 組み込みビヘイビア                                                     */
/* ------------------------------------------------------------------ */
static void player_interact(Actor *p) {
    int16_t px = p->x + 8, py = p->y + 12;
    uint8_t i;
    Actor *o;
    switch (p->dir) {
        case DIR_DOWN:  py += 12; break;
        case DIR_UP:    py -= 12; break;
        case DIR_LEFT:  px -= 12; break;
        default:        px += 12; break;
    }
    for (i = 0, o = actors; i < actor_count; i++, o++) {
        if (o == p || !(o->flags & AF_ACTIVE) || (o->flags & AF_HIDDEN)) continue;
        /* 前方 12x12 の範囲とアクターの 16x16 が重なれば調べる */
        if (px + 6 <= o->x || px - 6 >= o->x + 16 || py + 6 <= o->y || py - 6 >= o->y + 16) continue;
        /* 相手をプレイヤーの方へ向かせる */
        o->dir = p->dir == DIR_DOWN ? DIR_UP : p->dir == DIR_UP ? DIR_DOWN : p->dir == DIR_LEFT ? DIR_RIGHT : DIR_LEFT;
        draw_actors();
        if (o->script != NO_SCRIPT && gbf_scripts[o->script].interact) {
            gbf_scripts[o->script].interact(o);
        } else if (o->text != NO_TEXT) {
            msg_show(o->text);
        }
        return;
    }
}

static void bhv_player(Actor *a) {
    int8_t dx = 0, dy = 0;
    uint8_t s = a->speed;
    if (keys & J_LEFT) dx = -(int8_t)s; else if (keys & J_RIGHT) dx = (int8_t)s;
    if (keys & J_UP) dy = -(int8_t)s; else if (keys & J_DOWN) dy = (int8_t)s;
    if (dx || dy) actor_move(a, dx, dy);
    if (keys_pressed & J_A) player_interact(a);
}

static void bhv_wander(Actor *a) {
    static const int8_t ddx[4] = { 0, 0, -1, 1 };
    static const int8_t ddy[4] = { 1, -1, 0, 0 };
    if (a->move_count == 0) {
        uint8_t r = random8();
        if (r & 0x80U) { a->move_count = 16; a->dir = r & 3U; }
        else a->move_count = 0x80U | (r & 0x3FU);   /* 待機 */
    }
    if (a->move_count & 0x80U) { if ((--a->move_count & 0x7FU) == 0) a->move_count = 0; return; }
    a->move_count--;
    actor_move(a, ddx[a->dir], ddy[a->dir]);
}

static void bhv_warp(Actor *a) {
    if (!player || !boxes_overlap(player->x, player->y, a->x, a->y)) return;
    spawn_tx = a->param[1];
    spawn_ty = a->param[2];
    spawn_dir = player->dir;
    scene_load(a->param[0]);
}

/* ------------------------------------------------------------------ */
/* メイン                                                              */
/* ------------------------------------------------------------------ */
void main(void) {
    uint8_t i, prev = 0;
    Actor *a;

    DISPLAY_OFF;
    LCDC_REG |= 0x50U;   /* BG/OBJ タイル 0x8000, ウィンドウマップ 0x9C00 */
    SPRITES_8x8;
    BGP_REG = 0xE4U;
    OBP0_REG = 0xE0U;   /* 色0 透明, 1=白, 2,3=黒 系 */
    OBP1_REG = 0xE0U;
    SHOW_BKG;
    SHOW_SPRITES;
    /* アクター i は OBJ i*4..i*4+3 と VRAM タイル 128+i*4.. を固定で使う */
    for (i = 0; i < MAX_ACTORS * 4U; i++) set_sprite_tile(i, TILE_SPRITE_BASE + i);
    SWITCH_ROM(CODE_BANK);
    gbf_text_init();
    load_game();          /* セーブがあれば変数を復元 (無ければ 0) */
    scene_load_now(START_SCENE);

    while (1) {
        keys = joypad();
        keys_pressed = keys & ~prev;
        prev = keys;
        if (keys_pressed) random8();

        for (i = 0, a = actors; i < actor_count; i++, a++) {
            if (!(a->flags & AF_ACTIVE)) continue;
            switch (a->behaviour) {
                case BHV_PLAYER: bhv_player(a); break;
                case BHV_WANDER: bhv_wander(a); break;
                case BHV_WARP:   bhv_warp(a); break;
                default: break;
            }
            if (a->script != NO_SCRIPT && gbf_scripts[a->script].update) gbf_scripts[a->script].update(a);
            if (pending_scene != 0xFFU) break;
        }
        if (pending_scene == 0xFFU && gbf_scenes[current_scene].script != NO_SCRIPT &&
            gbf_scripts[gbf_scenes[current_scene].script].update)
            gbf_scripts[gbf_scenes[current_scene].script].update(NULL);

        if (pending_scene != 0xFFU) {
            i = pending_scene;
            pending_scene = 0xFFU;
            scene_load_now(i);
            prev = joypad();
            continue;
        }
        update_camera();
        draw_actors();
        wait_vbl_done();
    }
}
