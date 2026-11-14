/*
 * gbmedia — GB Media Studio 再生ライブラリ (実装)。gbmedia.h の説明を参照。
 *
 * このファイルはバンク 0 に置く (データの ROM バンクを切り替えながら読むため)。
 *
 * レコード形式 (エンコーダ gbforge/media/encode.py と一致させること)
 *   u8 flags        bit0 タイル, bit1 マップ, bit2 属性 (CGB), bit3 パレット (CGB), bit4 BGP (DMG)
 *   タイル:  u8 runs, { u16 slot, u8 count, count*16 バイト }     slot 256〜511 は VRAM バンク 1
 *   マップ:  u8 runs, { u16 pos, u8 len, len バイト }             pos = y*32 + x (裏の BG マップへ)
 *   属性:    u8 runs, { u16 pos, u8 len, len バイト }             VBK=1
 *   パレット: u8 first, u8 count, count*8 バイト (RGB555, 画面切り替えと同時に反映)
 *   BGP:     u8 (初代: パレットだけで表せるフレーム。画面切り替えと同時に反映)
 *
 * BG タイルは 0x8800 方式 (LCDC.4 = 0): 番号 0〜127 → 0x9000、128〜255 → 0x8800。
 * これで BG に 256 枚を使いつつ、スプライト用に 0x8000〜0x87FF (128 枚) が残る。
 *
 * タイミング: VBlank の最初には「前のフレームの終わりに計算しておいた値」をレジスタに書くだけにする
 * (パレット計算などを VBlank 中にすると、書き込みが表示開始に間に合わず画面の上の数ラインが古い色になる)。
 */
#include <gbdk/platform.h>
#include <string.h>
#include "gbmedia.h"

uint16_t gbmedia_frame;
uint16_t gbmedia_late_frames;
uint16_t gbmedia_max_lag;
uint16_t gbmedia_cut_updates;
uint16_t gbmedia_video_frame;
int8_t   gbmedia_level;
uint8_t  gbmedia_flips;

/* dark[k][v] = v * k / 31 (k = 0..31, v = 0..31)。白へは 31 - dark[31-k][31-v]。
 * 実行時に割り算をしないための表 (ROM に置く。プレビューも同じ式で計算する)。 */
static const uint8_t dark_tab[32][32] = {
    { 0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0 },
    { 0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1 },
    { 0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,2 },
    { 0,0,0,0,0,0,0,0,0,0,0,1,1,1,1,1,1,1,1,1,1,2,2,2,2,2,2,2,2,2,2,3 },
    { 0,0,0,0,0,0,0,0,1,1,1,1,1,1,1,1,2,2,2,2,2,2,2,2,3,3,3,3,3,3,3,4 },
    { 0,0,0,0,0,0,0,1,1,1,1,1,1,2,2,2,2,2,2,3,3,3,3,3,3,4,4,4,4,4,4,5 },
    { 0,0,0,0,0,0,1,1,1,1,1,2,2,2,2,2,3,3,3,3,3,4,4,4,4,4,5,5,5,5,5,6 },
    { 0,0,0,0,0,1,1,1,1,2,2,2,2,2,3,3,3,3,4,4,4,4,4,5,5,5,5,6,6,6,6,7 },
    { 0,0,0,0,1,1,1,1,2,2,2,2,3,3,3,3,4,4,4,4,5,5,5,5,6,6,6,6,7,7,7,8 },
    { 0,0,0,0,1,1,1,2,2,2,2,3,3,3,4,4,4,4,5,5,5,6,6,6,6,7,7,7,8,8,8,9 },
    { 0,0,0,0,1,1,1,2,2,2,3,3,3,4,4,4,5,5,5,6,6,6,7,7,7,8,8,8,9,9,9,10 },
    { 0,0,0,1,1,1,2,2,2,3,3,3,4,4,4,5,5,6,6,6,7,7,7,8,8,8,9,9,9,10,10,11 },
    { 0,0,0,1,1,1,2,2,3,3,3,4,4,5,5,5,6,6,6,7,7,8,8,8,9,9,10,10,10,11,11,12 },
    { 0,0,0,1,1,2,2,2,3,3,4,4,5,5,5,6,6,7,7,7,8,8,9,9,10,10,10,11,11,12,12,13 },
    { 0,0,0,1,1,2,2,3,3,4,4,4,5,5,6,6,7,7,8,8,9,9,9,10,10,11,11,12,12,13,13,14 },
    { 0,0,0,1,1,2,2,3,3,4,4,5,5,6,6,7,7,8,8,9,9,10,10,11,11,12,12,13,13,14,14,15 },
    { 0,0,1,1,2,2,3,3,4,4,5,5,6,6,7,7,8,8,9,9,10,10,11,11,12,12,13,13,14,14,15,16 },
    { 0,0,1,1,2,2,3,3,4,4,5,6,6,7,7,8,8,9,9,10,10,11,12,12,13,13,14,14,15,15,16,17 },
    { 0,0,1,1,2,2,3,4,4,5,5,6,6,7,8,8,9,9,10,11,11,12,12,13,13,14,15,15,16,16,17,18 },
    { 0,0,1,1,2,3,3,4,4,5,6,6,7,7,8,9,9,10,11,11,12,12,13,14,14,15,15,16,17,17,18,19 },
    { 0,0,1,1,2,3,3,4,5,5,6,7,7,8,9,9,10,10,11,12,12,13,14,14,15,16,16,17,18,18,19,20 },
    { 0,0,1,2,2,3,4,4,5,6,6,7,8,8,9,10,10,11,12,12,13,14,14,15,16,16,17,18,18,19,20,21 },
    { 0,0,1,2,2,3,4,4,5,6,7,7,8,9,9,10,11,12,12,13,14,14,15,16,17,17,18,19,19,20,21,22 },
    { 0,0,1,2,2,3,4,5,5,6,7,8,8,9,10,11,11,12,13,14,14,15,16,17,17,18,19,20,20,21,22,23 },
    { 0,0,1,2,3,3,4,5,6,6,7,8,9,10,10,11,12,13,13,14,15,16,17,17,18,19,20,20,21,22,23,24 },
    { 0,0,1,2,3,4,4,5,6,7,8,8,9,10,11,12,12,13,14,15,16,16,17,18,19,20,20,21,22,23,24,25 },
    { 0,0,1,2,3,4,5,5,6,7,8,9,10,10,11,12,13,14,15,15,16,17,18,19,20,20,21,22,23,24,25,26 },
    { 0,0,1,2,3,4,5,6,6,7,8,9,10,11,12,13,13,14,15,16,17,18,19,20,20,21,22,23,24,25,26,27 },
    { 0,0,1,2,3,4,5,6,7,8,9,9,10,11,12,13,14,15,16,17,18,18,19,20,21,22,23,24,25,26,27,28 },
    { 0,0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29 },
    { 0,0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30 },
    { 0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31 },
};

static const gbm_scene_t *sc;
static uint16_t t0, ev_i;
static uint8_t  is_cgb, visible, pending_flip, fade_obj;
static uint8_t  bgp_base, obp0_base, obp1_base, bgp_next, bgp_pending;
static uint16_t pal_base[32], pal_next[32], obj_base[32];
static uint8_t  obj_n, pal_pending;
/* カラーの基本パレットの「63 段階の表」(ROM)。フェードは表の 1 行 (64 バイト) を書くだけになる。
 * 表がない時 (レコード内でパレットが変わった時など) は実行時に計算する (等速のカラーでは時間がかかる) */
static gbm_ref_t base_tab, next_tab;

/* 次の VBlank で書く値 (stage() が用意する) */
static uint8_t  st_flip, st_dirty, st_bgp, st_obp0, st_obp1, st_valid;
static int8_t   st_level;
static uint16_t st_pal[32], st_obj[32];
static uint8_t  st_tab_chunk;          /* 255 以外: st_pal の代わりに表のこの行を書く */
static uint16_t st_tab_off;

/* 動画 */
static const gbm_video_t *vid;
static gbm_video_t vid_copy;
static uint16_t vframe, due;
static uint8_t  vready, vbase, vfirst;

/* 転送 (少しずつ進める) */
static uint8_t        up_active, up_chunk, up_flags, up_sect, up_runs, up_buf, up_vbk, up_is_image;
static const uint8_t *up_ptr;
static uint8_t       *up_dst, *up_map;
static uint16_t       up_rem;

/* ------------------------------------------------------------------ */
static uint8_t *map_addr(uint8_t buf) { return (uint8_t *)(buf ? 0x9C00u : 0x9800u); }

static uint8_t *tile_addr(uint16_t slot) {
    uint8_t b = (uint8_t)slot;
    if (b < 128u) return (uint8_t *)(0x9000u + ((uint16_t)b << 4));
    return (uint8_t *)(0x8800u + ((uint16_t)(b - 128u) << 4));
}

/* 初代: 明るさ -31..31 → 段階 -3..3 (四捨五入。プレビューと同じ式で作った表) */
static const int8_t dmg_step[63] = { -3,-3,-3,-3,-3,-3,-2,-2,-2,-2,-2,-2,-2,-2,-2,-2,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,0,0,0,0,0,0,0,0,0,0,0,1,1,1,1,1,1,1,1,1,1,2,2,2,2,2,2,2,2,2,2,3,3,3,3,3,3 };
/* 段階 d のときの色番号の置き換え (暗くする = 3 (黒) に近づける) */
static const uint8_t dmg_map[7][4] = { { 3,3,3,3 }, { 2,3,3,3 }, { 1,2,3,3 }, { 0,1,2,3 }, { 0,0,1,2 }, { 0,0,0,1 }, { 0,0,0,0 } };

static uint8_t dmg_level(uint8_t pal, int8_t lv) {
    const uint8_t *m = dmg_map[(uint8_t)(dmg_step[(uint8_t)(lv + 31)] + 3)];
    return (uint8_t)(m[pal & 3u] | (uint8_t)(m[(pal >> 2) & 3u] << 2) |
                     (uint8_t)(m[(pal >> 4) & 3u] << 4) | (uint8_t)(m[pal >> 6] << 6));
}

/* 明るさ lv の時の 0..31 → 0..31 の対応表。256 バイト境界をまたがない位置に置く
 * (アセンブリで上位バイトを固定したまま 8 ビットの足し算だけで引けるように) */
static uint8_t  row_buf[64];
uint8_t        *gbm_row;
uint8_t        *gbm_lv_ptr;      /* cgb_level_n: その場で変換する RGB555 の配列 */
uint8_t         gbm_lv_n;        /* 色数 */

static void make_row(int8_t lv) {
    uint8_t v;
    const uint8_t *t;
    if (lv <= 0) {
        t = dark_tab[(uint8_t)(31 + lv)];
        for (v = 0; v < 32; v++) gbm_row[v] = t[v];
    } else {
        t = dark_tab[(uint8_t)(31 - lv)];
        for (v = 0; v < 32; v++) gbm_row[v] = (uint8_t)(31u - t[31u - v]);
    }
}

/* gbm_lv_ptr の RGB555 を gbm_lv_n 色、gbm_row で変換する (その場で書き換え)。1 色 約 200 クロック */
static void cgb_level_n(void) __naked {
    __asm
    ld   a, (_gbm_lv_n)
    or   a
    ret  z
    ld   hl, #_gbm_lv_ptr
    ld   e, (hl)
    inc  hl
    ld   d, (hl)
    ld   hl, #_gbm_row
    ld   c, (hl)            ; c = 表の下位バイト
    inc  hl
    ld   h, (hl)            ; h = 表の上位バイト (固定)
00001$:
    ld   a, (de)            ; lo = gggrrrrr
    and  #0x1f
    add  a, c
    ld   l, a
    ld   a, (hl)            ; r (新)
    push af
    ld   a, (de)
    rlca
    rlca
    rlca
    and  #0x07              ; g の下位 3 ビット
    ld   l, a
    inc  de
    ld   a, (de)            ; hi = 0bbbbbgg
    and  #0x03
    add  a, a
    add  a, a
    add  a, a
    or   l
    add  a, c
    ld   l, a
    ld   a, (hl)            ; g (新)
    ld   b, a
    ld   a, (de)
    rrca
    rrca
    and  #0x1f
    add  a, c
    ld   l, a
    ld   a, (hl)            ; b (新)
    add  a, a
    add  a, a
    ld   l, a
    ld   a, b
    rrca
    rrca
    rrca
    and  #0x03
    or   l
    ld   (de), a            ; 新しい hi
    dec  de
    ld   a, b
    rrca
    rrca
    rrca
    and  #0xe0
    ld   l, a
    pop  af
    or   l
    ld   (de), a            ; 新しい lo
    inc  de
    inc  de
    ld   a, (_gbm_lv_n)
    dec  a
    ld   (_gbm_lv_n), a
    jr   nz, 00001$
    ret
    __endasm;
}

/* パレット RAM へ書く (VBlank 中に呼ぶ)。gbm_pw_reg = 0x68 (BG) / 0x6A (OBJ)、gbm_pw_n バイト */
const uint8_t *gbm_pw_src;
uint8_t        gbm_pw_reg, gbm_pw_n;

static void pal_write(void) __naked {
    __asm
    ld   hl, #_gbm_pw_src
    ld   a, (hl+)
    ld   h, (hl)
    ld   l, a
    ld   a, (_gbm_pw_reg)
    ld   c, a
    ld   a, #0x80           ; 0 番から、書くたびに次へ
    ldh  (c), a
    inc  c
    ld   a, (_gbm_pw_n)
    ld   b, a
00001$:
    ld   a, (hl+)
    ldh  (c), a
    ld   a, (hl+)
    ldh  (c), a
    dec  b
    dec  b
    jr   nz, 00001$
    ret
    __endasm;
}

/* ---- 次の VBlank の準備 / 書き込み ---- */
static void stage(uint16_t next) {
    uint16_t i = ev_i;
    int8_t lv = gbmedia_level;
    uint8_t flip, base_changes;
    const uint16_t *pb;
    const gbm_ref_t *tb;
    uint8_t bb;
    while (i < sc->nevents && sc->events[i].frame <= next) {
        if (sc->events[i].op == GBM_OP_LEVEL) lv = (int8_t)sc->events[i].arg;
        i++;
    }
    flip = pending_flip || (vid && vready && (int16_t)(next - due) >= 0);
    base_changes = flip && (pal_pending || bgp_pending);
    st_flip = flip;
    if (st_valid && lv == st_level && !base_changes) return;      /* 変化なし */
    pb = (flip && pal_pending) ? pal_next : pal_base;
    tb = (flip && pal_pending) ? &next_tab : &base_tab;
    bb = (flip && bgp_pending) ? bgp_next : bgp_base;
    if (is_cgb) {
        if (tb->chunk != GBM_NO_FADE_TABLE) {
            st_tab_chunk = tb->chunk;
            st_tab_off = tb->off + (uint16_t)(uint8_t)(lv + 31) * 64u;
        } else {
            st_tab_chunk = GBM_NO_FADE_TABLE;
            make_row(lv);
            memcpy(st_pal, pb, sizeof(st_pal));
            gbm_lv_ptr = (uint8_t *)st_pal;
            gbm_lv_n = 32;
            cgb_level_n();
        }
        if (fade_obj && obj_n) {
            make_row(lv);
            memcpy(st_obj, obj_base, (uint16_t)obj_n * 8u);
            gbm_lv_ptr = (uint8_t *)st_obj;
            gbm_lv_n = (uint8_t)(obj_n * 4u);
            cgb_level_n();
        }
    } else {
        st_bgp = dmg_level(bb, lv);
        st_obp0 = dmg_level(obp0_base, lv);
        st_obp1 = dmg_level(obp1_base, lv);
    }
    st_level = lv;
    st_valid = 1;
    st_dirty = 1;
}

static void write_regs(void) {
    if (is_cgb) {
        /* set_bkg_palette() は等速のカラーだと VBlank に収まらないので専用の書き込みを使う */
        gbm_pw_reg = 0x68u; gbm_pw_n = 64u;
        if (st_tab_chunk != GBM_NO_FADE_TABLE) {
            uint8_t save = CURRENT_BANK;
            SWITCH_ROM(gbm_chunk_bank(st_tab_chunk));
            gbm_pw_src = gbm_chunk_ptr[st_tab_chunk] + st_tab_off;
            pal_write();
            SWITCH_ROM(save);
        } else {
            gbm_pw_src = (const uint8_t *)st_pal;
            pal_write();
        }
        if (fade_obj && obj_n) {
            gbm_pw_src = (const uint8_t *)st_obj; gbm_pw_reg = 0x6Au; gbm_pw_n = (uint8_t)(obj_n * 8u);
            pal_write();
        }
    } else {
        BGP_REG = st_bgp;
        if (fade_obj) { OBP0_REG = st_obp0; OBP1_REG = st_obp1; }
    }
}

/* ---- VRAM へのコピー (LCD オンのまま) ----
 * STAT のモード 2/3 でない (= モード 0/1) のを確かめてから 2 バイト書く。確認した直後にモード 2 に入っても、
 * VRAM が読めなくなるモード 3 までは 80 クロックあり、2 バイトの書き込み (約 56 クロック) は間に合う。
 * 確認から書き込みまでの間に割り込みが入るとモード 3 にずれ込むので、その間だけ割り込みを止める。 */
uint8_t       *gbm_vc_dst;
const uint8_t *gbm_vc_src;
uint8_t        gbm_vc_n;

static void vcopy(void) __naked {
    __asm
    ld   hl, #_gbm_vc_dst
    ld   e, (hl)
    inc  hl
    ld   d, (hl)
    ld   hl, #_gbm_vc_n
    ld   b, (hl)
    ld   hl, #_gbm_vc_src
    ld   a, (hl+)
    ld   h, (hl)
    ld   l, a
    srl  b                 ; b = 組の数, キャリー = 奇数
    jr   nc, 00002$
00001$:
    di
    ldh  a, (#0x41)        ; STAT
    and  #2
    jr   z, 00011$
    ei
    jr   00001$
00011$:
    ld   a, (hl+)
    ld   (de), a
    inc  de
    ei
00002$:
    ld   a, b
    or   a
    ret  z
00003$:
    di
    ldh  a, (#0x41)
    and  #2
    jr   z, 00004$
    ei
    jr   00003$
00004$:
    ld   a, (hl+)
    ld   (de), a
    inc  de
    ld   a, (hl+)
    ld   (de), a
    inc  de
    ei
    dec  b
    jr   nz, 00003$
    ret
    __endasm;
}

/* ---- 転送の状態機械 ---- */
/* SDCC は構造体の値渡しで内部エラーになることがあるので、番号とオフセットを別々に渡す */
static void up_begin(uint8_t chunk, uint16_t off, uint8_t buf, uint8_t is_image) {
    uint8_t save = CURRENT_BANK;
    up_chunk = chunk;
    SWITCH_ROM(gbm_chunk_bank(up_chunk));
    up_ptr = gbm_chunk_ptr[up_chunk] + off;
    up_flags = *up_ptr++;
    SWITCH_ROM(save);
    up_sect = 0;
    up_runs = 0;
    up_rem = 0;
    up_buf = buf;
    up_map = map_addr(buf);
    up_is_image = is_image;
    up_active = 1;
}

/* VRAM へ budget バイトまで転送。全部終わったら 1 */
static uint8_t up_step(uint16_t budget) {
    uint8_t save = CURRENT_BANK, cnt;
    uint16_t n, pos;
    if (!up_active) return 1;
    SWITCH_ROM(gbm_chunk_bank(up_chunk));
    for (;;) {
        if (up_rem == 0) {
            if (up_runs == 0) {
                /* 次の区画へ */
                do { up_sect++; } while (up_sect <= 5 && !(up_flags & (1u << (up_sect - 1))));
                if (up_sect > 5) { up_active = 0; break; }
                if (up_sect == 4) {          /* パレット: 次の切り替えで反映 */
                    uint8_t first = *up_ptr++, count = *up_ptr++;
                    if (!pal_pending) memcpy(pal_next, pal_base, sizeof(pal_next));
                    next_tab.chunk = GBM_NO_FADE_TABLE;      /* 表と違う色になったので実行時に計算する */
                    memcpy(&pal_next[first * 4u], up_ptr, (uint16_t)count * 8u);
                    up_ptr += (uint16_t)count * 8u;
                    pal_pending = 1;
                    continue;
                }
                if (up_sect == 5) {          /* BGP (初代): 次の切り替えで反映 */
                    bgp_next = *up_ptr++;
                    bgp_pending = 1;
                    continue;
                }
                up_runs = *up_ptr++;
                continue;
            }
            /* 次の連続区間: 始めるには GBM_RUN_COST 必要 (足りなければ次のフレーム。エンコーダー・プレビューと同じ規則) */
            if (budget < GBM_RUN_COST) break;
            budget -= GBM_RUN_COST;
            pos = (uint16_t)up_ptr[0] | ((uint16_t)up_ptr[1] << 8);
            cnt = up_ptr[2];
            up_ptr += 3;
            up_runs--;
            if (up_sect == 1) {
                up_dst = tile_addr(pos);
                up_rem = cnt;
                up_rem <<= 4;          /* (uint16_t)cnt << 4 を SDCC が 8 ビットで計算することがあるため分ける */
                up_vbk = (uint8_t)((pos >> 8) & 1u);
            } else {
                up_dst = up_map + pos;
                up_rem = cnt;
                up_vbk = (up_sect == 3) ? 1u : 0u;
            }
            continue;
        }
        if (!budget) break;
        /* 安全装置: 次の VBlank が近いのに転送が残っていたら、このフレームはここまで
         * (はみ出すと vsync() が 1 フレーム丸ごと待つことになり、かえって遅くなる) */
        if (LY_REG >= GBM_LY_STOP && LY_REG < 144u) { gbmedia_cut_updates++; break; }
        n = up_rem < budget ? up_rem : budget;
        if (n > GBM_PIECE) n = GBM_PIECE;
        if (is_cgb) VBK_REG = up_vbk;
        gbm_vc_dst = up_dst;
        gbm_vc_src = up_ptr;
        gbm_vc_n = (uint8_t)n;
        vcopy();
        if (is_cgb) VBK_REG = 0;
        up_dst += n;
        up_ptr += n;
        up_rem -= n;
        budget -= n;
    }
    SWITCH_ROM(save);
    return up_active ? 0 : 1;
}

static void load_base_palette(const uint16_t *pal, const gbm_ref_t *tab, uint8_t bgp) {
    bgp_next = bgp;            /* 画面を切り替えた時に反映 */
    bgp_pending = 1;
    if (pal) memcpy(pal_next, pal, sizeof(pal_next));
    else memcpy(pal_next, pal_base, sizeof(pal_next));
    next_tab = *tab;
    pal_pending = 1;
}

static void apply_event(const gbm_event_t *e) {
    switch (e->op) {
        case GBM_OP_IMAGE: {
            const gbm_image_t *im = &gbm_images[e->arg];
            vid = 0;
            up_begin(im->rec.chunk, im->rec.off, visible ^ 1u, 1);
            load_base_palette(im->pal, &im->fade, im->bgp);
            break;
        }
        case GBM_OP_VIDEO:
            memcpy(&vid_copy, &gbm_videos[e->arg], sizeof(vid_copy));
            vid = &vid_copy;
            vframe = 0;
            vready = 0;
            vfirst = 1;
            vbase = visible ^ 1u;       /* フレーム 0 は今見えていない方のマップへ */
            due = e->frame;
            load_base_palette(vid->pal, &vid->fade, vid->bgp);
            up_active = 0;
            break;
        case GBM_OP_LEVEL:
            gbmedia_level = (int8_t)e->arg;   /* レジスタは stage() で準備済み */
            break;
        case GBM_OP_VIDEO_STOP:
            vid = 0;
            break;
    }
}

/* ------------------------------------------------------------------ */
void gbmedia_init(void) {
#if defined(__TARGET_gb) || defined(__TARGET_ap) || defined(__TARGET_duck)
    is_cgb = (_cpu == CGB_TYPE) ? 1u : 0u;
#endif
#ifdef GBM_CPU_FAST
    if (is_cgb) cpu_fast();       /* カラー: CPU 倍速 (VRAM 転送が約 2 倍になる) */
#endif
    {   /* BG マップ 2 枚と 0 番タイルを消す (何も読み込んでいない間にゴミが見えないように) */
        static const uint8_t zero[32] = { 0 };
        uint8_t i;
        for (i = 0; i < 64; i++) set_data((uint8_t *)(0x9800u + (uint16_t)i * 32u), zero, 32);
        set_data((uint8_t *)0x9000u, zero, 16);
        if (is_cgb) {
            VBK_REG = 1;
            for (i = 0; i < 64; i++) set_data((uint8_t *)(0x9800u + (uint16_t)i * 32u), zero, 32);
            VBK_REG = 0;
        }
    }
    gbm_row = row_buf;
    if ((uint8_t)(uint16_t)gbm_row > 224u) gbm_row += (uint8_t)(0u - (uint8_t)(uint16_t)gbm_row);   /* 次の 256 境界へ */
    obp0_base = OBP0_REG;
    obp1_base = OBP1_REG;
    bgp_base = 0xE4u;
    memset(pal_base, 0, sizeof(pal_base));
    base_tab.chunk = GBM_NO_FADE_TABLE;
    st_tab_chunk = GBM_NO_FADE_TABLE;
    LCDC_REG &= (uint8_t)~(LCDCF_BG8000 | LCDCF_BG9C00);   /* BG: 0x8800 方式, マップ 0x9800 */
    visible = 0;
    sc = 0;
    st_valid = 0;
}

void gbmedia_set_obj_palettes(const uint16_t *pal, uint8_t n) {
    if (n > 8) n = 8;
    memcpy(obj_base, pal, (uint16_t)n * 8u);
    obj_n = n;
    st_valid = 0;
}

void gbmedia_load_scene(uint8_t scene) {
    sc = &gbm_scenes[scene];
    fade_obj = sc->fade_obj;
    ev_i = 0;
    vid = 0;
    up_active = 0;
    pending_flip = 0;
    gbmedia_late_frames = 0;
    gbmedia_max_lag = 0;
    gbmedia_cut_updates = 0;
    gbmedia_video_frame = 0;
    gbmedia_frame = 0;
    t0 = sys_time;
    st_valid = 0;
    stage(0);
}

void gbmedia_set_level(int8_t level) {
    gbmedia_level = level;
    st_valid = 0;
    if (sc) stage(gbmedia_frame + 1u);      /* 次の update (VBlank の始め) で書き込む */
}

void gbmedia_stop(void) {
    sc = 0;
    vid = 0;
    up_active = 0;
}

uint8_t gbmedia_update(void) {
    uint16_t now;
    if (!sc) return 0;
    now = sys_time - t0;
    gbmedia_frame = now;

    /* 1. VBlank の最初: 準備しておいた値を書くだけ (画面の切り替え + パレット) */
    if (st_flip) {
        visible ^= 1u;
        if (visible) LCDC_REG |= LCDCF_BG9C00;
        else LCDC_REG &= (uint8_t)~LCDCF_BG9C00;
    }
    if (st_dirty) {
        write_regs();
        st_dirty = 0;
    }
    if (st_flip) {
        st_flip = 0;
        gbmedia_flips++;
        if (pal_pending) { memcpy(pal_base, pal_next, sizeof(pal_base)); base_tab = next_tab; pal_pending = 0; }
        if (bgp_pending) { bgp_base = bgp_next; bgp_pending = 0; }
        if (pending_flip) {
            pending_flip = 0;
        } else if (vid) {
            if (vfirst) { due = now; vfirst = 0; }   /* 最初のフレームを表示した時刻を基準にする */
            if (vframe > 0 && now != due) {       /* 予定より遅れて表示した動画フレーム */
                gbmedia_late_frames++;
                if (now - due > gbmedia_max_lag) gbmedia_max_lag = now - due;
            }
            gbmedia_video_frame = vframe;
            due += vid->frame_hold[vframe];
            vframe++;
            vready = 0;
            if (vframe >= vid->nframes) {
                if (vid->loop) vframe = 0;
                else vid = 0;
            }
        }
    }

    /* 2. この時刻までのイベント */
    while (ev_i < sc->nevents && sc->events[ev_i].frame <= now) {
        apply_event(&sc->events[ev_i]);
        ev_i++;
    }

    /* 3. 転送 (1 フレームに GBM_BYTES_PER_UPDATE バイトまで) */
    if (up_active) {
        if (up_step(GBM_BYTES_PER_UPDATE)) {
            if (up_is_image) pending_flip = 1;   /* 画像: 次の VBlank で切り替え */
            else vready = 1;                     /* 動画: 表示予定時刻に切り替え */
        }
    } else if (vid && !vready) {
        up_begin(vid->frame_chunk[vframe], vid->frame_off[vframe], (vframe & 1u) ? (uint8_t)(vbase ^ 1u) : vbase, 0);
        if (up_step(GBM_BYTES_PER_UPDATE)) vready = 1;
    }

    /* 4. シーンの終わり */
    if (now >= sc->length) {
        if (sc->loop) {
            gbmedia_load_scene((uint8_t)(sc - gbm_scenes));
            stage(1);
            return 1;
        }
        sc = 0;
        return 0;
    }

    /* 5. 次の VBlank の準備 */
    stage(now + 1u);
    return 1;
}

void gbmedia_play(void) {
    vsync();                 /* 最初の update も VBlank の始めに合わせる */
    t0 = sys_time;
    while (gbmedia_update()) vsync();
}
