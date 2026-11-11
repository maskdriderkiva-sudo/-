/*
 * GBForge ランタイム API
 *
 * ユーザースクリプト (scripts フォルダの .c) はこのヘッダーを include して使う。
 * Unity の MonoBehaviour のように、スクリプト名 Foo に対して
 *     void Foo_start(Actor *self);     シーン開始時に1回
 *     void Foo_update(Actor *self);    毎フレーム
 *     void Foo_interact(Actor *self);  プレイヤーがAボタンで話しかけた時
 * を定義するとエンジンから自動で呼ばれる (定義しなかったものは呼ばれない)。
 * シーンに付けたスクリプトでは self は NULL になる。
 */
#ifndef GBFORGE_H
#define GBFORGE_H

#include <gb/gb.h>
#include <stdint.h>
#include "gbf_ids.h"   /* ビルダー生成: SCENE_xxx, TEXT_xxx, SPRITE_xxx, VAR_xxx など */

#ifndef NULL
#define NULL ((void *)0)
#endif

/* ---- 定数 ---- */
#define MAX_ACTORS      10      /* 1アクター = 16x16 = OBJ 4枚 → 40枚上限 */
#define NUM_VARS        32      /* グローバル変数 (セーブ対象) */
#define NO_SPRITE       0xFFU
#define NO_SCRIPT       0xFFU
#define NO_TEXT         0xFFFFU

/* VRAM タイル割り当て (0x8000 アドレッシング, BG/OBJ 共有 256 タイル) */
#define TILE_BG_BASE      0U     /* 0..127   シーン背景 */
#define TILE_SPRITE_BASE  128U   /* 128..167 アクター (4タイル x 10) */
#define TILE_TEXT_BASE    192U   /* 192..255 メッセージウィンドウ */

/* 向き */
#define DIR_DOWN  0U
#define DIR_UP    1U
#define DIR_LEFT  2U
#define DIR_RIGHT 3U

/* アクターフラグ */
#define AF_ACTIVE   0x01U
#define AF_HIDDEN   0x02U
#define AF_SOLID    0x04U
#define AF_MOVING   0x08U

/* 組み込みビヘイビア (エディタの「コンポーネント」) */
#define BHV_NONE     0U
#define BHV_PLAYER   1U   /* 十字キーで移動, Aで調べる */
#define BHV_TALK     2U   /* 調べると text を表示 */
#define BHV_WARP     3U   /* 触れると param[0] シーンの (param[1],param[2]) タイルへ */
#define BHV_WANDER   4U   /* ランダムにうろうろ */

typedef struct Actor {
    int16_t  x, y;         /* ワールド座標 (ピクセル, 左上) */
    uint8_t  flags;
    uint8_t  sprite;       /* スプライトアセット番号 / NO_SPRITE */
    uint8_t  frame;        /* 表示中フレーム */
    uint8_t  dir;
    uint8_t  anim_tick;
    uint8_t  behaviour;
    uint8_t  script;       /* スクリプト番号 / NO_SCRIPT */
    uint8_t  speed;        /* 1フレームの移動量 (px) */
    uint16_t text;         /* BHV_TALK 用テキスト */
    uint8_t  param[4];
    int16_t  vars[4];      /* スクリプトから自由に使える変数 */
    uint8_t  loaded_frame; /* VRAM に転送済みのフレーム (内部用) */
    uint8_t  move_count;   /* 内部用 (BHV_WANDER) */
} Actor;

/* ---- 状態 ---- */
extern Actor   actors[MAX_ACTORS];
extern uint8_t actor_count;
extern Actor  *player;
extern uint8_t keys;          /* 押されているボタン (J_A など) */
extern uint8_t keys_pressed;  /* このフレームで押された瞬間のボタン */
extern int16_t vars[NUM_VARS];
extern uint8_t current_scene;
extern int16_t camera_x, camera_y;

/* ---- シーン ---- */
void    scene_load(uint8_t scene);            /* 次のフレームでシーン切替 */
uint8_t tile_solid(int16_t tx, int16_t ty);   /* タイル座標の当たり判定 */
void    set_tile_solid(uint8_t tx, uint8_t ty, uint8_t solid);

/* ---- アクター ---- */
uint8_t actor_move(Actor *a, int8_t dx, int8_t dy);   /* 当たり判定付き移動, 動けたら1 */
uint8_t actor_overlap(Actor *a, Actor *b);
Actor  *actor_get(uint8_t index);
void    actor_show(Actor *a);
void    actor_hide(Actor *a);
void    actor_set_frame(Actor *a, uint8_t frame);
void    actor_face(Actor *a, uint8_t dir);

/* ---- テキスト (日本語) ---- */
void    msg_show(uint16_t text_id);           /* メッセージウィンドウ表示 (ブロッキング) */
uint8_t msg_choice(uint16_t text_id);         /* 2択 (テキストの最終行2つが選択肢), 0 or 1 */
void    msg_number(int16_t value);            /* 次の msg_show の「{n}」に埋め込む数値 */
void    text_print(uint8_t x, uint8_t y, uint16_t text_id); /* 背景に直接描画 (最大54文字) */
void    text_clear(void);

/* ---- ユーティリティ ---- */
void    wait_frames(uint8_t n);
void    fade_out(void);
void    fade_in(void);
uint8_t random8(void);
void    save_game(void);                      /* vars を SRAM へ */
uint8_t load_game(void);                      /* 成功で1 */
void    sfx_beep(uint8_t pitch);

#endif
