"""GBForge プロジェクト形式。

MyGame/
  project.gbproj          プロジェクト設定 (JSON)
  texts.json              日本語テキストテーブル
  scenes/<name>.json      シーン (背景・当たり判定・アクター)
  assets/backgrounds/*.png
  assets/sprites/*.png    16x16 フレームを横に並べたスプライトシート
  scripts/<Name>.c        C スクリプト (Name_start / Name_update / Name_interact)
  build/                  ビルド成果物 (自動生成)
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

PROJECT_FILE = "project.gbproj"
FORMAT = "gbforge-project"
FORMAT_VERSION = 1

IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

BEHAVIOURS = ["none", "player", "talk", "warp", "wander"]
DIRECTIONS = ["down", "up", "left", "right"]

DEFAULT_SETTINGS = {
    "format": FORMAT,
    "version": FORMAT_VERSION,
    "layout": "standard",          # standard (src/ lib/ res/) | flat (既存フォルダをそのまま)
    "stages_output": "stage_data", # ステージエディタの出力ファイル名 (.c/.h)
    "object_types": [],            # ステージに置くオブジェクトの種類
    "type": "engine",              # engine (シーン/アクターのゲームエンジン) | c (GBDK の C プログラム)
    "target": "dmg",               # dmg (初代) | both (初代+カラー対応) | cgb (カラー専用)
    "name": "My Game",
    "rom_title": "GBFORGE",
    "start_scene": "",
    "font": "misaki_gothic",
    "variables": [],
    "build": {
        "toolchain": "gbdk",       # gbdk | sdcc-shim (テスト用)
        "gbdk_home": "",           # 空なら環境変数 GBDK_HOME / PATH の lcc
        "emulator": "",            # 実行ボタンで起動するエミュレータ
        "mode": "gbforge",         # gbforge (GBForge がコンパイル) | script (既存の build.bat / build.sh を実行)
        "script_windows": "",      # 例: build.bat
        "script_unix": "",         # 例: build.sh / make
        "output": "",              # script モードで出来る ROM (例: highway_star_gbc.gb)
    },
    "jpfont": {
        "mode": "scan",            # scan (ソースで使う文字だけ) | full (全 約7000字)
        "extra_chars": "",         # 実行時に組み立てる文字列などで使う追加の文字
    },
    "ide": {
        "editor": "auto",          # auto | vscode | visualstudio
    },
    "flasher": {
        "port": "",
        "baud": 187500,            # 125000 / 187500 / 375000
        "mbc": "MBC5",
        "algorithm": "ALG16",      # ALG16 / ALG12 / ALG16WR / ALG12_M29F
        "wait_mode": "TOGGLE",     # LONGER / DEFAULT / DATA_POLL / TOGGLE
        "erase_before_write": True,
        "verify": True,
    },
}

DEFAULT_ACTOR = {
    "name": "Actor",
    "sprite": None,
    "x": 0,
    "y": 0,
    "dir": "down",
    "behaviour": "none",
    "script": None,
    "text": None,
    "speed": 1,
    "solid": True,
    "hidden": False,
    "warp": {"scene": "", "x": 0, "y": 0},
}


class ProjectError(Exception):
    pass


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def check_ident(name: str, what: str) -> str:
    if not IDENT_RE.match(name or ""):
        raise ProjectError(f"{what}の名前「{name}」は半角英数字と _ のみ (先頭は英字) にしてください")
    return name


class Project:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        pf = self.root / PROJECT_FILE
        if not pf.exists():
            raise ProjectError(f"{pf} がありません (gbforge new で作成してください)")
        raw = json.loads(pf.read_text(encoding="utf-8"))
        if raw.get("format") != FORMAT:
            raise ProjectError(f"{pf} は GBForge プロジェクトではありません")
        self.settings = _deep_merge(DEFAULT_SETTINGS, raw)

    # ---- パス ----
    @property
    def scenes_dir(self) -> Path: return self.root / "scenes"
    @property
    def sprites_dir(self) -> Path: return self.root / "assets" / "sprites"
    @property
    def backgrounds_dir(self) -> Path: return self.root / "assets" / "backgrounds"
    @property
    def scripts_dir(self) -> Path: return self.root / "scripts"
    @property
    def build_dir(self) -> Path: return self.root / "build"
    @property
    def texts_file(self) -> Path: return self.root / "texts.json"
    @property
    def src_dir(self) -> Path: return self.root / "src"
    @property
    def lib_dir(self) -> Path: return self.root / "lib"
    @property
    def res_dir(self) -> Path: return self.root / "res"
    @property
    def is_c(self) -> bool: return self.settings.get("type") == "c"

    def source_files(self) -> list[str]:
        """C プロジェクトのソース (プロジェクトからの相対パス)。"""
        out = []
        if self.is_flat:
            skip = {".git", ".vs", ".vscode", "build", "obj", "__pycache__", ".gbforge", "stages"}
            for f in sorted(self.root.rglob("*")):
                rel = f.relative_to(self.root)
                if f.suffix in (".c", ".h") and not (skip & set(rel.parts)) \
                        and not (f.parent.name == "font" and f.name.startswith("gbjp_")):
                    out.append(rel.as_posix())
            return out
        for base in (self.src_dir, self.lib_dir):
            for f in sorted(base.rglob("*")):
                if f.suffix in (".c", ".h") and not (f.parent.name == "font" and f.name.startswith("gbjp_")):
                    out.append(f.relative_to(self.root).as_posix())
        return out

    def resolve(self, rel: str) -> Path:
        """プロジェクト内の相対パスを安全に解決 (外へは出られない)。"""
        f = (self.root / rel).resolve()
        if self.root != f and self.root not in f.parents:
            raise ProjectError("プロジェクト外のファイルは扱えません")
        return f

    @property
    def is_flat(self) -> bool:
        return self.settings.get("layout") == "flat"

    @property
    def code_root(self) -> Path:
        """ソースを置く場所 (flat ならフォルダ直下)。"""
        return self.root if self.is_flat else self.src_dir

    @property
    def rom_path(self) -> Path:
        b = self.settings.get("build", {})
        if b.get("mode") == "script" and b.get("output"):
            return self.root / b["output"]
        return self.build_dir / f"{self.safe_filename()}.gb"

    def safe_filename(self) -> str:
        s = re.sub(r"[^A-Za-z0-9_-]+", "_", self.settings.get("rom_title") or "game").strip("_")
        return s or "game"

    # ---- 一覧 ----
    def scene_names(self) -> list[str]:
        return sorted(p.stem for p in self.scenes_dir.glob("*.json"))

    def sprite_names(self) -> list[str]:
        return sorted(p.stem for p in self.sprites_dir.glob("*.png"))

    def background_files(self) -> list[str]:
        return sorted(p.name for p in self.backgrounds_dir.glob("*.png"))

    def script_names(self) -> list[str]:
        return sorted(p.stem for p in self.scripts_dir.glob("*.c"))

    # ---- 読み書き ----
    def save_settings(self):
        (self.root / PROJECT_FILE).write_text(
            json.dumps(self.settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def load_scene(self, name: str) -> dict:
        p = self.scenes_dir / f"{name}.json"
        if not p.exists():
            raise ProjectError(f"シーン {name} がありません")
        sc = json.loads(p.read_text(encoding="utf-8"))
        sc.setdefault("background", "")
        sc.setdefault("collision", [])
        sc.setdefault("script", None)
        sc.setdefault("palette", "E4")
        sc["actors"] = [_deep_merge(DEFAULT_ACTOR, a) for a in sc.get("actors", [])]
        return sc

    def save_scene(self, name: str, data: dict):
        check_ident(name, "シーン")
        self.scenes_dir.mkdir(parents=True, exist_ok=True)
        (self.scenes_dir / f"{name}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def delete_scene(self, name: str):
        p = self.scenes_dir / f"{name}.json"
        if p.exists():
            p.unlink()

    def load_texts(self) -> dict:
        if not self.texts_file.exists():
            return {}
        raw = json.loads(self.texts_file.read_text(encoding="utf-8"))
        out = {}
        for k, v in raw.items():
            if isinstance(v, str):
                v = {"text": v, "choice": False}
            out[k] = {"text": v.get("text", ""), "choice": bool(v.get("choice", False))}
        return out

    def save_texts(self, texts: dict):
        for k in texts:
            check_ident(k, "テキスト")
        self.texts_file.write_text(json.dumps(texts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def read_script(self, name: str) -> str:
        return (self.scripts_dir / f"{check_ident(name, 'スクリプト')}.c").read_text(encoding="utf-8")

    def write_script(self, name: str, code: str):
        self.scripts_dir.mkdir(parents=True, exist_ok=True)
        (self.scripts_dir / f"{check_ident(name, 'スクリプト')}.c").write_text(code, encoding="utf-8")


SCRIPT_TEMPLATE = """\
#include "gbforge.h"

/* {name}: アクターまたはシーンに付けるスクリプト。
 * 必要な関数だけ残せば OK (無い関数は呼ばれません)。 */

void {name}_start(Actor *self) {{
    (void)self;
}}

void {name}_update(Actor *self) {{
    (void)self;
}}

void {name}_interact(Actor *self) {{
    (void)self;
}}
"""


def new_script_code(name: str) -> str:
    return SCRIPT_TEMPLATE.format(name=check_ident(name, "スクリプト"))
