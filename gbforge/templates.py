"""新規プロジェクトのテンプレート (サンプル素材を Pillow で生成)。"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

from .project import DEFAULT_SETTINGS, PROJECT_FILE, Project, ProjectError, new_script_code

W, G, L, B = (255, 255, 255, 255), (96, 96, 96, 255), (170, 170, 170, 255), (0, 0, 0, 255)
CLEAR = (0, 0, 0, 0)


# ---------------------------------------------------------------------------
# スプライト
# ---------------------------------------------------------------------------
def _person(d: ImageDraw.ImageDraw, ox: int, direction: str, step: int, hair, body):
    # 足
    if step == 0:
        d.rectangle([ox + 5, 13, ox + 6, 15], fill=B)
        d.rectangle([ox + 9, 13, ox + 10, 15], fill=B)
    else:
        d.rectangle([ox + 4, 13, ox + 5, 15], fill=B)
        d.rectangle([ox + 10, 13, ox + 11, 14], fill=B)
    # 体
    d.rectangle([ox + 4, 9, ox + 11, 13], fill=body, outline=B)
    # 頭
    d.ellipse([ox + 3, 0, ox + 12, 9], fill=W, outline=B)
    if direction == "up":
        d.ellipse([ox + 3, 0, ox + 12, 9], fill=hair, outline=B)
    else:
        d.chord([ox + 3, 0, ox + 12, 9], 180, 360, fill=hair, outline=B)
        if direction == "down":
            d.point([(ox + 6, 6), (ox + 9, 6)], fill=B)
        elif direction == "left":
            d.point([(ox + 5, 6)], fill=B)
            d.rectangle([ox + 9, 2, ox + 11, 6], fill=hair)
        else:
            d.point([(ox + 10, 6)], fill=B)
            d.rectangle([ox + 4, 2, ox + 6, 6], fill=hair)


def make_person_sheet(path: Path, hair, body, frames8: bool):
    dirs = ["down", "up", "left", "right"]
    n = 8 if frames8 else 4
    im = Image.new("RGBA", (16 * n, 16), CLEAR)
    d = ImageDraw.Draw(im)
    i = 0
    for dname in dirs:
        for step in ((0, 1) if frames8 else (0,)):
            _person(d, i * 16, dname, step, hair, body)
            i += 1
    im.save(path)


def make_cat_sheet(path: Path):
    im = Image.new("RGBA", (64, 16), CLEAR)
    d = ImageDraw.Draw(im)
    for i, dname in enumerate(["down", "up", "left", "right"]):
        ox = i * 16
        d.ellipse([ox + 3, 7, ox + 13, 15], fill=W, outline=B)          # 体
        d.ellipse([ox + 4, 2, ox + 12, 10], fill=W, outline=B)          # 頭
        d.polygon([(ox + 4, 4), (ox + 5, 0), (ox + 7, 3)], fill=B)       # 耳
        d.polygon([(ox + 12, 4), (ox + 11, 0), (ox + 9, 3)], fill=B)
        if dname == "down":
            d.point([(ox + 6, 6), (ox + 10, 6)], fill=B)
        elif dname == "left":
            d.point([(ox + 5, 6)], fill=B)
            d.line([(ox + 13, 10), (ox + 15, 7)], fill=B)
        elif dname == "right":
            d.point([(ox + 11, 6)], fill=B)
            d.line([(ox + 2, 10), (ox + 0, 7)], fill=B)
        else:
            d.line([(ox + 8, 11), (ox + 8, 15)], fill=G)
    im.save(path)


def make_sign(path: Path):
    im = Image.new("RGBA", (16, 16), CLEAR)
    d = ImageDraw.Draw(im)
    d.rectangle([7, 9, 8, 15], fill=G, outline=B)
    d.rectangle([1, 2, 14, 10], fill=W, outline=B)
    d.line([(4, 5), (11, 5)], fill=G)
    d.line([(4, 7), (9, 7)], fill=G)
    im.save(path)


# ---------------------------------------------------------------------------
# 背景 (16x16 メタタイル)
# ---------------------------------------------------------------------------
def _meta(kind: str) -> Image.Image:
    im = Image.new("RGBA", (16, 16), W)
    d = ImageDraw.Draw(im)
    if kind in (".", "F"):
        d.point([(3, 4), (12, 11), (10, 2)], fill=L)
        if kind == "F":
            for cx, cy in ((4, 10), (11, 5)):
                d.point([(cx, cy - 1), (cx - 1, cy), (cx + 1, cy), (cx, cy + 1)], fill=G)
                d.point([(cx, cy)], fill=B)
    elif kind == "T":
        d.ellipse([1, 0, 14, 12], fill=G, outline=B)
        d.arc([4, 3, 10, 9], 200, 320, fill=W)
        d.rectangle([6, 12, 9, 15], fill=B)
    elif kind == "=":
        im.paste(L, [0, 0, 16, 16])
        d.point([(4, 3), (11, 9), (6, 13)], fill=G)
    elif kind == "R":   # 屋根
        im.paste(G, [0, 0, 16, 16])
        for y in range(1, 16, 4):
            d.line([(0, y), (15, y)], fill=B)
    elif kind == "W":   # 壁
        d.rectangle([0, 0, 15, 15], outline=B)
        d.rectangle([4, 4, 11, 10], fill=L, outline=B)
        d.line([(7, 4), (7, 10)], fill=B)
    elif kind == "D":   # ドア
        d.rectangle([0, 0, 15, 15], outline=B)
        d.rectangle([3, 2, 12, 15], fill=G, outline=B)
        d.point([(10, 9)], fill=W)
    elif kind == "~":   # 水
        im.paste(L, [0, 0, 16, 16])
        d.arc([1, 3, 7, 7], 200, 340, fill=W)
        d.arc([8, 10, 14, 14], 200, 340, fill=W)
    elif kind == "#":   # 室内の壁
        im.paste(G, [0, 0, 16, 16])
        d.line([(0, 15), (15, 15)], fill=B)
        d.line([(0, 7), (15, 7)], fill=B)
        d.line([(7, 0), (7, 7)], fill=B)
        d.line([(3, 8), (3, 15)], fill=B)
        d.line([(11, 8), (11, 15)], fill=B)
    elif kind == "_":   # 床
        d.rectangle([0, 0, 15, 15], outline=L)
    elif kind == "M":   # じゅうたん
        im.paste(L, [0, 0, 16, 16])
        d.rectangle([1, 1, 14, 14], outline=G)
    elif kind == "t":   # テーブル
        d.rectangle([1, 3, 14, 12], fill=G, outline=B)
        d.line([(2, 5), (13, 5)], fill=W)
    elif kind == "E":   # 出口マット
        im.paste(B, [0, 0, 16, 16])
        d.rectangle([2, 2, 13, 13], outline=L)
    return im


SOLID_KINDS = set("TRW~#t")

TOWN = [
    "TTTTTTTTTTTTTTTT",
    "T..............T",
    "T..RRR.....F...T",
    "T..RRR.........T",
    "T..WDW....~~~..T",
    "T...=.....~~~..T",
    "T...=======....T",
    "T.F.......=....T",
    "T.........=..F.T",
    "T....F....=....T",
    "T.........=....T",
    "TTTTTTTTTTTTTTTT",
]

HOUSE = [
    "##########",
    "##########",
    "_________t",
    "__MMM____t",
    "__MMM_____",
    "__________",
    "_t________",
    "__________",
    "____E_____",
]


def make_background(path: Path, rows: list[str]) -> list[str]:
    mw, mh = len(rows[0]), len(rows)
    im = Image.new("RGBA", (mw * 16, mh * 16), W)
    coll = []
    cache: dict[str, Image.Image] = {}
    for my, row in enumerate(rows):
        crow = ""
        for mx, k in enumerate(row):
            if k not in cache:
                cache[k] = _meta(k)
            im.paste(cache[k], (mx * 16, my * 16))
            crow += "11" if k in SOLID_KINDS else "00"
        coll += [crow, crow]
    im.save(path)
    return coll


# ---------------------------------------------------------------------------
# プロジェクト作成
# ---------------------------------------------------------------------------
# IDE 設定は PC ごとの絶対パスを含むので Git には入れない (ビルドのたびに再生成される)
IDE_GITIGNORE = "build/\n.vs/\n*.sln\n*.vcxproj\n*.vcxproj.filters\n*.vcxproj.user\nCppProperties.json\n" \
                ".vscode/c_cpp_properties.json\n.vscode/tasks.json\n"

TEXTS = {
    "welcome": {"text": "ようこそ GBForge へ！\nここは ゲームボーイの まち。\n---\nじゅうじキーで いどう、\nAボタンで しらべる/はなす。", "choice": False},
    "sign": {"text": "【かんばん】\nみなみ：はじまりの むら\nきた：ちょうろうの いえ", "choice": False},
    "villager": {"text": "こんにちは！\nきょうは いい てんきですね。\n日本語の漢字も ひょうじできます。", "choice": False},
    "cat": {"text": "ねこ「にゃーん。」", "choice": False},
    "elder_ask": {"text": "ちょうろう「はなしを きくかね？」\nはい\nいいえ", "choice": True},
    "elder_yes": {"text": "ちょうろう「この せかいは\nGBForgeで つくられたのじゃ。\nなんでも つくれるぞ。」\n---\nはなしを きいた かいすう：{n}かい", "choice": False},
    "elder_no": {"text": "ちょうろう「そうか、ざんねんじゃ。」", "choice": False},
}

TOWN_SCRIPT = """\
#include "gbforge.h"

/* まちシーンのスクリプト: 初回だけ説明を表示してセーブする */
void TownScene_start(Actor *self) {
    (void)self;
    if (!vars[VAR_VISITED]) {
        msg_show(TEXT_WELCOME);
        vars[VAR_VISITED] = 1;
        save_game();
    }
}
"""

ELDER_SCRIPT = """\
#include "gbforge.h"

/* ちょうろう: 話しかけると 2 択を出す */
void Elder_interact(Actor *self) {
    (void)self;
    if (msg_choice(TEXT_ELDER_ASK) == 0) {
        vars[VAR_TALKS]++;
        msg_number(vars[VAR_TALKS]);
        msg_show(TEXT_ELDER_YES);
        save_game();
    } else {
        msg_show(TEXT_ELDER_NO);
    }
}
"""


def _actor(**kw) -> dict:
    a = {"name": "Actor", "sprite": None, "x": 0, "y": 0, "dir": "down", "behaviour": "none", "script": None,
         "text": None, "speed": 1, "solid": True, "hidden": False, "warp": {"scene": "", "x": 0, "y": 0}}
    a.update(kw)
    return a


def create_project(root: Path, name: str = "My Game", sample: bool = True) -> Project:
    root = Path(root)
    if (root / PROJECT_FILE).exists():
        raise ProjectError(f"{root} には既にプロジェクトがあります")
    for sub in ("scenes", "assets/sprites", "assets/backgrounds", "scripts", "build"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    settings["name"] = name
    title = "".join(c for c in name.upper() if c.isascii() and (c.isalnum() or c == " "))[:11].strip()
    settings["rom_title"] = title or "GBFORGE"
    (root / ".gitignore").write_text(IDE_GITIGNORE, encoding="utf-8")

    if sample:
        settings["start_scene"] = "town"
        settings["variables"] = ["visited", "talks"]
        sp = root / "assets" / "sprites"
        make_person_sheet(sp / "hero.png", hair=G, body=G, frames8=True)
        make_person_sheet(sp / "villager.png", hair=B, body=W, frames8=False)
        make_person_sheet(sp / "elder.png", hair=L, body=B, frames8=False)
        make_cat_sheet(sp / "cat.png")
        make_sign(sp / "sign.png")
        bgd = root / "assets" / "backgrounds"
        town_coll = make_background(bgd / "town.png", TOWN)
        house_coll = make_background(bgd / "house.png", HOUSE)
        town = {
            "background": "town.png", "collision": town_coll, "script": "TownScene", "palette": "E4",
            "actors": [
                _actor(name="Hero", sprite="hero", x=72, y=112, behaviour="player", speed=1),
                _actor(name="Villager", sprite="villager", x=176, y=144, behaviour="talk", text="villager", dir="left"),
                _actor(name="Cat", sprite="cat", x=96, y=144, behaviour="wander", text="cat"),
                _actor(name="Sign", sprite="sign", x=128, y=96, behaviour="talk", text="sign"),
                _actor(name="Door", x=64, y=64, behaviour="warp", solid=False,
                       warp={"scene": "house", "x": 8, "y": 14}),
            ],
        }
        house = {
            "background": "house.png", "collision": house_coll, "script": None, "palette": "E4",
            "actors": [
                _actor(name="Hero", sprite="hero", x=64, y=112, behaviour="player", dir="up"),
                _actor(name="Elder", sprite="elder", x=96, y=48, behaviour="none", script="Elder"),
                _actor(name="Exit", x=64, y=128, behaviour="warp", solid=False,
                       warp={"scene": "town", "x": 8, "y": 11}),
            ],
        }
        for n, sc in (("town", town), ("house", house)):
            (root / "scenes" / f"{n}.json").write_text(json.dumps(sc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (root / "texts.json").write_text(json.dumps(TEXTS, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (root / "scripts" / "TownScene.c").write_text(TOWN_SCRIPT, encoding="utf-8")
        (root / "scripts" / "Elder.c").write_text(ELDER_SCRIPT, encoding="utf-8")
    else:
        settings["start_scene"] = "main"
        bgd = root / "assets" / "backgrounds"
        im = Image.new("RGBA", (160, 144), W)
        im.save(bgd / "blank.png")
        (root / "scenes" / "main.json").write_text(json.dumps(
            {"background": "blank.png", "collision": [], "script": None, "palette": "E4", "actors": []},
            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (root / "texts.json").write_text("{}\n", encoding="utf-8")
        (root / "scripts" / "Main.c").write_text(new_script_code("Main"), encoding="utf-8")

    (root / PROJECT_FILE).write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    p = Project(root)
    from .ide import generate_ide_files
    generate_ide_files(p)
    return p


# ---------------------------------------------------------------------------
# C プロジェクト (GBDK でプログラムを書く / ゲームボーイ・カラー対応)
# ---------------------------------------------------------------------------
C_MAIN = r'''/*
 * {name} — GBDK-2020 + gbjp (日本語フォントライブラリ)
 *
 * ビルド: GBForge の「ビルド」, VS Code の Ctrl+Shift+B, Visual Studio の「ビルド」
 * res/sprites/player.png は自動で player.c / player.h に変換される (GBDK の png2asset)
 */
#include <gb/gb.h>
#include <gb/cgb.h>
#include <stdint.h>
#include "gbjp.h"     /* 日本語フォント */
#include "player.h"   /* res/sprites/player.png から自動生成 */

#define BLANK_TILE 128   /* jp_init の先頭タイル = 空白 */

/* ゲームボーイカラー用パレット (初代では無視される) */
const palette_color_t bg_palettes[] = {
    RGB(31, 31, 28), RGB(18, 24, 31), RGB(6, 10, 22), RGB(1, 2, 8),    /* 0: 本文 */
    RGB(31, 31, 28), RGB(31, 20, 8),  RGB(24, 6, 4),  RGB(8, 1, 1),    /* 1: 見出し */
};

static uint8_t px = 80, py = 96, dir = 0, anim = 0;

/* ウィンドウにメッセージを 1 文字ずつ表示して A ボタンを待つ */
static void message(const char *text) {
    const char *rest = text;
    fill_win_rect(0, 0, 20, 5, BLANK_TILE);
    move_win(7, 104);
    SHOW_WIN;
    jp_set_layer(JP_WIN);
    jp_set_speed(2);
    do {
        jp_clear_box(1, 1, 18, 3);
        rest = jp_print_box(1, 1, 18, 3, rest);
        waitpadup();
        waitpad(J_A);
        waitpadup();
    } while (rest);
    jp_set_speed(0);
    jp_set_layer(JP_BKG);
    HIDE_WIN;
}

void main(void) {
    uint8_t keys, prev = 0;

    DISPLAY_OFF;
    if (_cpu == CGB_TYPE) {
        set_bkg_palette(0, 2, bg_palettes);
        set_sprite_palette(0, 1, player_palettes);
    }

    jp_init(BLANK_TILE, 128);                    /* VRAM タイル 128〜255 を文字に使う */
    fill_bkg_rect(0, 0, 32, 32, BLANK_TILE);
    jp_set_palette(1);
    jp_print(1, 1, "{title}");
    jp_set_palette(0);
    jp_print_box(1, 3, 18, 4, "十字キーで歩く。\nAボタンで話す。");

    SPRITES_8x16;
    set_sprite_data(0, player_TILE_COUNT, player_tiles);
    SHOW_BKG;
    SHOW_SPRITES;
    DISPLAY_ON;

    while (1) {
        keys = joypad();
        if (keys & J_LEFT)  { px--; dir = 2; }
        if (keys & J_RIGHT) { px++; dir = 3; }
        if (keys & J_UP)    { py--; dir = 1; }
        if (keys & J_DOWN)  { py++; dir = 0; }
        if (keys & (J_LEFT | J_RIGHT | J_UP | J_DOWN)) anim++;
        if ((keys & ~prev) & J_A) {
            message("こんにちは！これは GBDK で書いた プログラムです。\n日本語の漢字も、ゲームボーイカラーの色も使えます。");
        }
        prev = keys;
        move_metasprite_ex(player_metasprites[(dir << 1) | ((anim >> 3) & 1U)], 0, 0, 0, px, py);
        vsync();
    }
}
'''

GBJP_README = """# gbjp — GBDK 用 日本語フォントライブラリ

`#include "gbjp.h"` するだけで、C の文字列 (UTF-8) をそのまま表示できます。

```c
jp_init(128, 128);                     // VRAM タイル 128〜255 を文字用に
jp_print(1, 1, "こんにちは、世界！");
jp_print_box(1, 3, 18, 4, "自動で折り返して、行頭禁則も処理します。");
jp_print_int(1, 8, score);
```

- `font/` はビルドのたびに `gbforge jpfont` が自動生成します (ソースで使った文字だけを抽出)。
  全文字を入れたいときは project.gbproj の `jpfont.mode` を `"full"` にします。
- フォントデータはバンク自動配置 (`#pragma bank 255`) なので、リンクに `lcc -autobank` が必要です。
- フォント: 美咲フォント (© Num Kadoma, 自由に利用・再配布可)。
"""


def create_c_project(root: Path, name: str = "My App", target: str = "both") -> Project:
    from .jpfont import install_library
    root = Path(root)
    if (root / PROJECT_FILE).exists():
        raise ProjectError(f"{root} には既にプロジェクトがあります")
    if target not in ("dmg", "both", "cgb"):
        raise ProjectError("target は dmg / both / cgb のいずれかです")
    for sub in ("src", "res/sprites", "res/backgrounds", "lib", "build"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    settings.update({"type": "c", "target": target, "name": name})
    title = "".join(c for c in name.upper() if c.isascii() and (c.isalnum() or c == " "))[:11].strip()
    settings["rom_title"] = title or "GBAPP"
    (root / ".gitignore").write_text(IDE_GITIGNORE + "lib/gbjp/font/\n", encoding="utf-8")
    (root / "src" / "main.c").write_text(
        C_MAIN.replace("{name}", name).replace("{title}", name.replace('"', "")), encoding="utf-8")
    make_person_sheet(root / "res" / "sprites" / "player.png", hair=B, body=G, frames8=True)  # 3色+透明
    install_library(root / "lib" / "gbjp")
    (root / "lib" / "gbjp" / "README.md").write_text(GBJP_README, encoding="utf-8")
    (root / PROJECT_FILE).write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    p = Project(root)
    from .ide import generate_ide_files
    generate_ide_files(p)
    return p
