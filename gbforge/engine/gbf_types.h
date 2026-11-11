/* GBForge: ビルダーが生成するデータとエンジンの間の型定義 (ユーザーは通常触らない) */
#ifndef GBF_TYPES_H
#define GBF_TYPES_H
#include <stdint.h>

struct Actor;

typedef struct {
    uint8_t        bank;
    const uint8_t *ptr;
} BankPtr;

typedef struct {
    int16_t  x, y;
    uint8_t  sprite, behaviour, script, flags, dir, speed;
    uint16_t text;
    uint8_t  param[4];
} ActorDef;

typedef struct {
    uint8_t         w, h;          /* タイル数 (20..32 x 18..32) */
    uint8_t         ntiles;        /* 1..128 */
    BankPtr         tiles;         /* 2bpp タイルデータ */
    BankPtr         map;           /* w*h タイル番号 */
    BankPtr         collision;     /* w*h (0=通行可, 1=壁) */
    uint8_t         actor_count;
    const ActorDef *actors;
    uint8_t         script;
    uint8_t         player;        /* プレイヤーアクター番号 / 0xFF */
    uint8_t         bgp;           /* BG パレット */
} SceneDef;

typedef struct {
    uint8_t frames;                /* 16x16 フレーム数 */
    BankPtr data;                  /* frames * 64 bytes (TL,TR,BL,BR) */
} SpriteDef;

typedef struct {
    void (*start)(struct Actor *self);
    void (*update)(struct Actor *self);
    void (*interact)(struct Actor *self);
} ScriptDef;

/* テキスト制御コード (uint16 単位でエンコード) */
#define TC_END     0xFFFFU
#define TC_NEWLINE 0xFFFEU
#define TC_PAGE    0xFFFDU
#define TC_NUMBER  0xFFFCU

/* 固定グリフ番号 (ビルダーが必ず先頭に配置) */
#define GLYPH_SPACE   0U
#define GLYPH_DIGIT0  1U   /* 1..10 = '0'..'9' */
#define GLYPH_MINUS   11U
#define GLYPH_CURSOR  12U  /* ▶ */
#define GLYPH_MORE    13U  /* ▼ */
#define GLYPHS_PER_CHUNK 2048U

extern const SceneDef  gbf_scenes[];
extern const SpriteDef gbf_sprites[];
extern const BankPtr   gbf_texts[];
extern const BankPtr   gbf_font_chunks[];
extern const ScriptDef gbf_scripts[];

#endif
