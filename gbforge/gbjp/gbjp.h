/*
 * gbjp - GBDK-2020 用 日本語フォントライブラリ (ゲームボーイ / ゲームボーイカラー)
 *
 *   #include "gbjp.h"
 *
 *   jp_init(128, 128);                         // VRAM タイル 128〜255 を文字用に使う
 *   jp_print(1, 1, "こんにちは、世界！");        // C の文字列 (UTF-8) をそのまま表示
 *   jp_print_box(1, 3, 18, 4, "長い文章は自動で折り返します。");
 *
 * - フォントは美咲フォント (8x8, JIS 第1・第2水準 約7000字)。
 *   `gbforge jpfont` が lib/gbjp/font/ にフォントデータ (.c) を生成する。
 *   ソースで使っている文字だけを入れる「スキャン」モードと、全文字を入れる「フル」モードがある。
 * - フォントデータは自動バンク配置 (#pragma bank 255) されるので、リンク時に lcc -autobank を付けること
 *   (GBForge のビルドは自動で付ける)。MBC1/MBC5 などバンク切替のあるカートリッジが必要。
 * - 1 文字 = 1 タイル。同じ文字は VRAM タイルを共有するので、画面上の「異なる文字数」だけタイルを使う。
 *   画面を書き換えるときは jp_reset() で割り当てをクリアする。
 * - 半角英数字は全角で表示する。ゲームボーイカラーでは jp_set_palette() で BG パレットを指定できる。
 */
#ifndef GBJP_H
#define GBJP_H

#include <gbdk/platform.h>
#include <stdint.h>

#define JP_BKG 0   /* 背景レイヤーに描く */
#define JP_WIN 1   /* ウィンドウレイヤーに描く */

/* 初期化: VRAM タイル first_tile から count 枚 (2〜128) を文字用に確保する。
 * 先頭の 1 枚は空白タイルとして使われる。 */
void jp_init(uint8_t first_tile, uint8_t count);

/* タイル割り当てをクリア (画面の文字を全部消して書き直すとき) */
void jp_reset(void);

/* 文字色・背景色 (0〜3, BGP パレット上の色番号)。既定は 文字 3 / 背景 0 */
void jp_set_color(uint8_t fg, uint8_t bg);

/* 描画先 JP_BKG / JP_WIN */
void jp_set_layer(uint8_t layer);

/* ゲームボーイカラー: 文字に使う BG パレット番号 (0〜7)。DMG では無視される */
void jp_set_palette(uint8_t palette);

/* 1 文字ごとに待つフレーム数 (0 = 一瞬で表示)。メッセージの「タイプライター」表示 */
void jp_set_speed(uint8_t frames);

/* (x, y) から 1 行描画。戻り値は描画後の x。'\n' は無視 */
uint8_t jp_print(uint8_t x, uint8_t y, const char *text);

/* 幅 w × 高さ h の枠に、自動折り返し・行頭禁則つきで描画する。'\n' で改行。
 * 枠に入りきらなかった場合は残りの文字列へのポインタ、全部描けたら NULL を返す
 * (ボタン待ち → jp_clear_box → 続きを描画、でページ送りができる)。 */
const char *jp_print_box(uint8_t x, uint8_t y, uint8_t w, uint8_t h, const char *text);

/* 数値を描画。戻り値は描画後の x */
uint8_t jp_print_int(uint8_t x, uint8_t y, int16_t value);

/* 枠を空白で塗りつぶす */
void jp_clear_box(uint8_t x, uint8_t y, uint8_t w, uint8_t h);

/* 1 文字 (Unicode コードポイント) を描画 */
void jp_put_char(uint8_t x, uint8_t y, uint16_t codepoint);

/* フォントに含まれる文字なら 1 */
uint8_t jp_has_char(uint16_t codepoint);

/* UTF-8 文字列の文字数 */
uint16_t jp_strlen(const char *text);

#endif
