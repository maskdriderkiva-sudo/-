/*
 * gbmedia — GB Media Studio 再生ライブラリ (GBDK-2020, ゲームボーイ / ゲームボーイカラー)
 *
 *   #include "gbmedia.h"
 *   #include "gbmedia_data.h"         // GB Media Studio が書き出したデータ
 *
 *   gbmedia_init();
 *   gbmedia_load_scene(GBM_SCENE_OPENING);
 *   gbmedia_play();                   // 最後まで再生して戻る (ループするシーンは戻らない)
 *
 * ゲームループに組み込む場合は gbmedia_play() の代わりに
 *   vsync();
 *   gbmedia_load_scene(GBM_SCENE_OPENING);      // vsync() の直後に呼ぶ
 *   while (gbmedia_update()) { ...ゲームの処理...; vsync(); }
 * gbmedia_update() は必ず vsync() の直後 (VBlank の始め) に呼ぶこと。パレットと画面の切り替えは
 * update の最初に書き込むので、遅れて呼ぶと画面の上の方が古い色のまま表示される。
 * また、転送は走査線 GBM_LY_STOP で打ち切るので、ゲームの処理はそれ以降の時間 (と update の後) で行う。
 *
 * 仕組み
 * - 画像と動画のフレームは「タイル・マップ・属性・パレット」の更新リスト (レコード) として ROM にある。
 * - レコードは毎フレーム GBM_BYTES_PER_UPDATE バイトずつ VRAM へ転送する (LCD はオンのまま。
 *   画面を消すために LCD を止めることはしない)。連続区間 1 つごとに GBM_RUN_COST を差し引く。
 *   この規則はエンコーダーとプレビュー (シミュレーター) でも同じなので、プレビューと実機は同じフレームで切り替わる。
 * - BG マップは 0x9800 / 0x9C00 の 2 枚を交互に使い、転送が終わったら VBlank 中に LCDC で切り替える
 *   (ちらつき・書き換え途中の表示が出ない)。
 * - フェードはパレットだけで行う: 初代は BGP (と OBP0/OBP1) を 4 階調で段階的に、
 *   カラーは set_bkg_palette() / set_sprite_palette() で RGB555 を 31 段階で変える。
 */
#ifndef GBMEDIA_H
#define GBMEDIA_H

#include <gbdk/platform.h>
#include <stdint.h>

/* ---- 書き出しデータとの取り決め (gbmedia_data.c が定義する) ---- */
typedef struct {
    uint8_t  chunk;          /* データのかたまり番号 (ROM バンクは gbm_chunk_bank() で調べる) */
    uint16_t off;            /* かたまり内のオフセット */
} gbm_ref_t;

typedef struct {
    gbm_ref_t       rec;     /* 更新レコード (タイル + マップ + 属性 + パレット) */
    uint8_t         bgp;     /* 初代: BGP の基本値 */
    const uint16_t *pal;     /* カラー: BG パレット 8 本 x 4 色 (RGB555), 初代は NULL */
    gbm_ref_t       fade;    /* カラー: 明るさ -31〜+31 の 63 段階のパレット (63 x 64 バイト)。chunk 255 = なし */
} gbm_image_t;

typedef struct {
    uint16_t        nframes;
    uint8_t         loop;          /* 1 = 最後まで行ったら先頭へ */
    uint8_t         bgp;
    const uint16_t *pal;           /* カラー: 最初のパレット (フレームごとの変更はレコード内) */
    gbm_ref_t       fade;          /* カラー: 63 段階のパレット表 (gbm_image_t と同じ) */
    const uint8_t  *frame_chunk;   /* フレームごとのかたまり番号 */
    const uint16_t *frame_off;     /* フレームごとのオフセット */
    const uint8_t  *frame_hold;    /* フレームごとの表示 VBlank 数 */
} gbm_video_t;

#define GBM_NO_FADE_TABLE 255u

#define GBM_OP_IMAGE      1   /* arg = 画像番号: 裏の BG マップに読み込み、終わったら切り替え */
#define GBM_OP_VIDEO      2   /* arg = 動画番号: 再生開始 */
#define GBM_OP_LEVEL      3   /* arg = 明るさ (int8: -31 = 黒, 0 = 元の色, +31 = 白) */
#define GBM_OP_VIDEO_STOP 4

typedef struct {
    uint16_t frame;          /* シーン開始からの VBlank 数 */
    uint8_t  op;
    uint8_t  arg;
} gbm_event_t;

typedef struct {
    uint16_t           length;     /* シーンの長さ (VBlank 数) */
    uint8_t            loop;
    uint8_t            fade_obj;   /* 1 = スプライトのパレットもフェード */
    uint16_t           nevents;
    const gbm_event_t *events;
} gbm_scene_t;

extern const gbm_image_t gbm_images[];
extern const gbm_video_t gbm_videos[];
extern const gbm_scene_t gbm_scenes[];
extern const uint8_t *const gbm_chunk_ptr[];
uint8_t gbm_chunk_bank(uint8_t chunk);

/* ---- 設定 (GB Media Studio が書き出す gbmedia_config.h) ---- */
#include "gbmedia_config.h"
#ifndef GBM_LY_STOP
#define GBM_LY_STOP 128              /* この走査線を過ぎたら、そのフレームの転送をやめる (はみ出し防止) */
#endif
#ifndef GBM_PIECE
#define GBM_PIECE 64                 /* 1 回の set_data で送る最大バイト数 (LY を確認する間隔) */
#endif
#ifndef GBM_RUN_COST
#define GBM_RUN_COST 22              /* 連続区間 1 つの手間 (バイト換算)。gbforge/media/__init__.py の RUN_COST と同じ値 */
#endif
#ifndef GBM_BYTES_PER_UPDATE
#define GBM_BYTES_PER_UPDATE 512     /* 1 フレームに VRAM へ送るバイト数の上限 */
#endif

/* ---- API ---- */
void    gbmedia_init(void);
void    gbmedia_load_scene(uint8_t scene);
uint8_t gbmedia_update(void);        /* 毎フレーム 1 回 (vsync の直後)。終わったら 0 */
void    gbmedia_play(void);          /* シーンが終わるまで再生 */
void    gbmedia_stop(void);
void    gbmedia_set_level(int8_t level);                           /* 明るさを直接設定 */
void    gbmedia_set_obj_palettes(const uint16_t *pal, uint8_t n);  /* カラー: フェードさせるスプライトのパレット */

/* ---- 状態 (デバッグ・検証用) ---- */
extern uint16_t gbmedia_frame;        /* シーン開始からの VBlank 数 */
extern uint16_t gbmedia_late_frames;  /* 予定より遅れて表示された動画フレームの数 (実機での実測) */
extern uint16_t gbmedia_max_lag;      /* そのうち最大の遅れ (フレーム数) */
extern uint16_t gbmedia_cut_updates;  /* 時間切れで転送を途中で止めた回数 (0 ならプレビューと完全に同じ進み方) */
extern uint16_t gbmedia_video_frame;  /* 表示中の動画フレーム番号 */
extern int8_t   gbmedia_level;        /* 現在の明るさ */
extern uint8_t  gbmedia_flips;        /* 画面を切り替えた回数 (下位 8 ビット) */

#endif
