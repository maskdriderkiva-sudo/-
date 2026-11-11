"""GBForge エディタ: ローカル HTTP サーバ + ブラウザ UI (標準ライブラリのみ)。"""
from __future__ import annotations

import importlib.util
import json
import mimetypes
import re
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__, actions, adopt, assets, carray, convert, stages, toolchain, versions
from .flasher import CartFlasher, FlasherError, list_ports
from .font import available_fonts, load_font, render_preview
from .jobs import JobManager
from .project import BEHAVIOURS, DIRECTIONS, IDENT_RE, Project, ProjectError, check_ident, new_script_code

EDITOR_DIR = Path(__file__).parent / "editor"
ENGINE_HEADER = Path(__file__).parent / "engine" / "gbforge.h"


class ApiError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


RECENT_MAX = 10


def _recent_file() -> Path:
    return toolchain.gbforge_home() / "recent.json"


def load_recent() -> list[str]:
    try:
        items = json.loads(_recent_file().read_text(encoding="utf-8"))
        return [p for p in items if (Path(p) / "project.gbproj").exists()][:RECENT_MAX]
    except (OSError, ValueError):
        return []


def add_recent(root: Path):
    items = [str(root)] + [p for p in load_recent() if p != str(root)]
    try:
        _recent_file().parent.mkdir(parents=True, exist_ok=True)
        _recent_file().write_text(json.dumps(items[:RECENT_MAX], ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


class EditorState:
    def __init__(self, root: Path | None):
        self.root = root
        self.jobs = JobManager()
        self.lock = threading.RLock()
        if root:
            add_recent(root)

    def open(self, root: Path):
        Project(root)   # 存在チェック
        self.root = root.resolve()
        add_recent(self.root)

    @property
    def project(self) -> Project:
        if not self.root:
            raise ApiError("プロジェクトが開かれていません", 409)
        return Project(self.root)


def _safe_name(name: str, what: str) -> str:
    name = unquote(name)
    check_ident(name, what)
    return name


def make_handler(state: EditorState):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"GBForge/{__version__}"

        def log_message(self, fmt, *args):  # 静かに
            pass

        # ---- 応答 ----
        def _json(self, obj, status=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _file(self, path: Path, download: str | None = None):
            if not path.is_file():
                raise ApiError("ファイルがありません", 404)
            data = path.read_bytes()
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if path.suffix in (".js", ".css", ".html"):
                ctype += "; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            if download:
                self.send_header("Content-Disposition", f'attachment; filename="{download}"')
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> bytes:
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n) if n else b""

        def _jbody(self):
            raw = self._body()
            return json.loads(raw.decode("utf-8")) if raw else {}

        def _check_origin(self):
            # 他サイトからのリクエスト (CSRF) を拒否
            origin = self.headers.get("Origin")
            host = self.headers.get("Host", "")
            if origin and urlparse(origin).netloc != host:
                raise ApiError("不正なオリジンです", 403)

        def _dispatch(self, method: str):
            u = urlparse(self.path)
            path = u.path
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                if method != "GET":
                    self._check_origin()
                with state.lock:
                    self.route(method, path, q)
            except ApiError as e:
                self._json({"error": str(e)}, e.status)
            except (ProjectError, assets.AssetError, FlasherError, ValueError) as e:
                self._json({"error": str(e)}, 400)
            except Exception as e:  # noqa: BLE001
                self._json({"error": f"内部エラー: {e}"}, 500)

        def do_GET(self): self._dispatch("GET")
        def do_POST(self): self._dispatch("POST")
        def do_PUT(self): self._dispatch("PUT")
        def do_DELETE(self): self._dispatch("DELETE")

        # ---- ルーティング ----
        def route(self, m: str, path: str, q: dict):
            if self.route_global(m, path, q):
                return
            p = state.project
            if m == "GET" and path == "/file":
                f = p.resolve(q.get("path", ""))
                if f.suffix.lower() not in (".png", ".gb", ".gbc"):
                    raise ApiError("not found", 404)
                return self._file(f)

            if m == "GET" and path.startswith("/asset/"):
                kind, _, fname = path[len("/asset/"):].partition("/")
                base = {"sprites": p.sprites_dir, "backgrounds": p.backgrounds_dir,
                        "res_sprites": p.res_dir / "sprites", "res_backgrounds": p.res_dir / "backgrounds",
                        "res_tiles": p.res_dir / "tiles"}.get(kind)
                fname = unquote(fname)
                if not base or "/" in fname or "\\" in fname or not fname.endswith(".png"):
                    raise ApiError("not found", 404)
                return self._file(base / fname)

            if path == "/api/project":
                if m == "GET":
                    return self._json(self.project_summary(p))
                if m == "PUT":
                    data = self._jbody()
                    s = p.settings
                    for k in ("name", "rom_title", "start_scene", "font"):
                        if k in data:
                            s[k] = str(data[k])
                    if data.get("target") in ("dmg", "both", "cgb"):
                        s["target"] = data["target"]
                    if "variables" in data:
                        s["variables"] = [check_ident(v, "変数") for v in data["variables"] if v]
                    for sect in ("build", "flasher", "jpfont", "ide"):
                        if isinstance(data.get(sect), dict):
                            s[sect].update(data[sect])
                    p.save_settings()
                    return self._json({"ok": True})

            mm = re.match(r"^/api/scene/([^/]+)$", path)
            if mm:
                name = _safe_name(mm.group(1), "シーン")
                if m == "GET":
                    sc = p.load_scene(name)
                    if sc.get("background"):
                        try:
                            sc["_bg_size"] = assets.image_info(p.backgrounds_dir / sc["background"])
                        except Exception:  # noqa: BLE001
                            sc["_bg_size"] = None
                    return self._json(sc)
                if m == "PUT":
                    data = self._jbody()
                    data.pop("_bg_size", None)
                    p.save_scene(name, data)
                    return self._json({"ok": True})
                if m == "POST":
                    if (p.scenes_dir / f"{name}.json").exists():
                        raise ApiError("同名のシーンが既にあります")
                    data = self._jbody()
                    bg = data.get("background") or (p.background_files() or [""])[0]
                    p.save_scene(name, {"background": bg, "collision": [], "script": None,
                                        "palette": "E4", "actors": []})
                    return self._json({"ok": True})
                if m == "DELETE":
                    p.delete_scene(name)
                    return self._json({"ok": True})

            if path == "/api/texts":
                if m == "GET":
                    return self._json(p.load_texts())
                if m == "PUT":
                    p.save_texts(self._jbody())
                    return self._json({"ok": True})

            if path == "/api/text/preview" and m == "POST":
                d = self._jbody()
                pages, missing = render_preview(d.get("text", ""), p.settings.get("font", "misaki_gothic"),
                                                bool(d.get("choice")))
                return self._json({"pages": pages, "missing": missing})

            if path == "/api/font" and m == "GET":
                # 指定した文字のビットマップ (8 バイト hex)
                font = load_font(q.get("font") or p.settings.get("font", "misaki_gothic"))
                chars = q.get("chars", "")
                return self._json({c: font[c].hex() for c in set(chars) if c in font})

            mm = re.match(r"^/api/script/([^/]+)$", path)
            if mm:
                name = _safe_name(mm.group(1), "スクリプト")
                f = p.scripts_dir / f"{name}.c"
                if m == "GET":
                    return self._json({"name": name, "code": p.read_script(name)})
                if m == "PUT":
                    p.write_script(name, self._jbody().get("code", ""))
                    return self._json({"ok": True})
                if m == "POST":
                    if f.exists():
                        raise ApiError("同名のスクリプトが既にあります")
                    p.write_script(name, new_script_code(name))
                    return self._json({"ok": True})
                if m == "DELETE":
                    if f.exists():
                        f.unlink()
                    return self._json({"ok": True})

            if path == "/api/file":
                rel = q.get("path", "")
                f = p.resolve(rel)
                if f.suffix not in (".c", ".h", ".md", ".txt", ".json", ".gbproj"):
                    raise ApiError("このファイルは編集できません")
                if m == "GET":
                    if not f.exists():
                        raise ApiError("ファイルがありません", 404)
                    return self._json({"path": rel, "code": f.read_text(encoding="utf-8", errors="replace")})
                if m == "PUT":
                    f.parent.mkdir(parents=True, exist_ok=True)
                    f.write_text(self._jbody().get("code", ""), encoding="utf-8")
                    return self._json({"ok": True})
                if m == "POST":
                    if f.exists():
                        raise ApiError("同名のファイルが既にあります")
                    f.parent.mkdir(parents=True, exist_ok=True)
                    guard = re.sub(r"[^A-Z0-9]", "_", f.name.upper())
                    f.write_text(f"#ifndef {guard}\n#define {guard}\n\n#endif\n" if f.suffix == ".h"
                                 else '#include <gb/gb.h>\n#include "gbjp.h"\n\n', encoding="utf-8")
                    return self._json({"ok": True})
                if m == "DELETE":
                    if f.exists():
                        f.unlink()
                    return self._json({"ok": True})

            # ---- C 配列 (既存ソースのタイル・マップ・パレット) ----
            if path == "/api/carrays" and m == "GET":
                arrs = carray.scan_project(p.root, p.source_files())
                return self._json([dict(a.summary(), values=None) for a in arrs])
            if path == "/api/carray":
                if m == "GET":
                    return self._json(carray.find(p.root, q["file"], q["name"]).summary())
                if m == "PUT":
                    d = self._jbody()
                    a = carray.write_back(p.root, q["file"], q["name"], [int(v) for v in d["values"]], d.get("per_line"))
                    return self._json(a.summary())
            if path == "/api/carray/png":
                a = carray.find(p.root, q["file"], q["name"])
                pal = None
                if q.get("pal_name"):
                    pa = carray.find(p.root, q.get("pal_file") or q["file"], q["pal_name"])
                    pal = convert.palette_from_values(pa.values, int(q.get("pal_index", 0)))
                if m == "GET":
                    data = bytes(v & 0xFF for v in (a.values or []))
                    im = convert.tiles_to_image(data, pal, int(q.get("cols", 16)), q.get("order", "tiles"),
                                                int(q.get("sw", 16)), int(q.get("sh", 16)))
                    body = convert.png_bytes(im, int(q.get("scale", 1)))
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    if q.get("download"):
                        self.send_header("Content-Disposition", f'attachment; filename="{a.name}.png"')
                    self.end_headers()
                    self.wfile.write(body)
                    return None
                if m == "POST":   # 編集した PNG を配列へ戻す
                    from io import BytesIO
                    from PIL import Image
                    im = Image.open(BytesIO(self._body()))
                    data = convert.image_to_tiles_with_palette(im, pal or list(carray.DMG_GRAYS))
                    a2 = carray.write_back(p.root, q["file"], q["name"], list(data))
                    return self._json({"ok": True, "count": a2.count, "tiles": a2.count // 16})

            # ---- 変換ツール ----
            if path in ("/api/convert", "/api/convert/save") and m == "POST":
                from io import BytesIO
                from PIL import Image
                raw = self._body()
                src = Image.open(BytesIO(raw)) if raw else Image.open(p.resolve(q["src"]))
                name = q.get("name") or "converted"
                check_ident(name, "出力名")
                res = convert.convert_image(src, name, q.get("mode", "map"), q.get("target", "cgb"),
                                            q.get("dedupe", "1") == "1", int(q.get("sw", 16)), int(q.get("sh", 16)),
                                            int(q.get("origin", 0)))
                c_src, h_src = convert.to_c(res, int(q["bank"]) if q.get("bank") else None)
                import base64
                out = {"name": name, "tile_count": res.tile_count, "frames": res.frames, "map_w": res.map_w,
                       "map_h": res.map_h, "palettes": res.palettes, "warnings": res.warnings,
                       "preview": base64.b64encode(convert.preview_png(res, 3)).decode(), "c": c_src, "h": h_src}
                if path.endswith("/save"):
                    out_dir = p.resolve(q.get("dir") or p.code_root.relative_to(p.root).as_posix() or ".")
                    files = convert.save_c(res, out_dir, int(q["bank"]) if q.get("bank") else None)
                    out["files"] = [f.relative_to(p.root).as_posix() for f in files]
                return self._json(out)

            # ---- ステージ (シーン) ----
            if path == "/api/stages" and m == "GET":
                return self._json({"stages": stages.list_stages(p), "object_types": stages.object_types(p)})
            if path == "/api/object_types" and m == "PUT":
                types = self._jbody()
                for t in types:
                    check_ident(t.get("name", ""), "オブジェクトの種類")
                p.settings["object_types"] = types
                p.save_settings()
                return self._json({"ok": True})
            mm = re.match(r"^/api/stage/([^/]+)(/tileset\.png)?$", path)
            if mm:
                name = _safe_name(mm.group(1), "ステージ")
                if mm.group(2) and m == "GET":
                    st = stages.load(p, name)
                    im, n = stages.tileset_image(p, st.get("tileset") or {}, int(q.get("pal", 0)))
                    body = convert.png_bytes(im)
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("X-Tile-Count", str(n))
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                    return None
                if m == "GET":
                    return self._json(stages.load(p, name))
                if m == "PUT":
                    st = self._jbody()
                    stages.save(p, name, st)
                    logs = []
                    stages.export(p, logs.append, write_arrays=True, only=name)
                    return self._json({"ok": True, "log": logs})
                if m == "POST":
                    d = self._jbody()
                    if (stages.stages_dir(p) / f"{name}.json").exists():
                        raise ApiError("同名のステージが既にあります")
                    if d.get("from") == "array":
                        stages.create_from_array(p, name, d["file"], d["symbol"], d.get("width") or None, d.get("tileset"))
                    else:
                        stages.create_new(p, name, int(d.get("width", 20)), int(d.get("height", 18)), d.get("tileset"),
                                          p.settings.get("target", "dmg") != "dmg")
                    return self._json({"ok": True})
                if m == "DELETE":
                    f = stages.stages_dir(p) / f"{name}.json"
                    if f.exists():
                        f.unlink()
                    return self._json({"ok": True})

            if path == "/api/build_script/add" and m == "POST":
                files = [f for f in self._jbody().get("files", []) if re.fullmatch(r"[\w./-]+\.c", f)]
                changed = adopt.add_to_build_scripts(p, files)
                return self._json({"ok": True, "changed": changed,
                                   "missing": [f for f in files if not adopt.script_has(p, f)]})
            if path == "/api/build_script/has" and m == "GET":
                return self._json({"mode": p.settings.get("build", {}).get("mode"),
                                   "has": {f: adopt.script_has(p, f) for f in q.get("files", "").split(",") if f}})

            # ---- バージョン ----
            if path == "/api/versions":
                if m == "GET":
                    return self._json({"versions": versions.siblings(p.root), "suggest": versions.suggest_next(p.root)})
                if m == "POST":
                    d = self._jbody()
                    r = versions.save_new_version(p.root, d["name"], bool(d.get("zip", True)))
                    if d.get("open"):
                        state.open(Path(r["path"]))
                    return self._json(r)

            if path == "/api/res" and m == "DELETE":
                f = p.resolve(q.get("path", ""))
                if p.res_dir.resolve() not in f.parents or f.suffix != ".png":
                    raise ApiError("res/ の PNG だけ削除できます")
                if f.exists():
                    f.unlink()
                return self._json({"ok": True})

            if path == "/api/ide/open" and m == "POST":
                from .ide import open_in_ide
                d = self._jbody()
                try:
                    name = open_in_ide(p, d.get("editor") or None, d.get("file") or None, d.get("line"))
                except RuntimeError as e:
                    raise ApiError(str(e))
                return self._json({"ok": True, "ide": name})

            if path == "/api/api_reference" and m == "GET":
                hdr = (Path(__file__).parent / "gbjp" / "gbjp.h") if p.is_c else ENGINE_HEADER
                return self._json({"header": hdr.read_text(encoding="utf-8")})

            if path == "/api/upload" and m == "POST":
                kind = q.get("kind")
                name = q.get("name", "")
                stem = Path(name).stem
                if not IDENT_RE.match(stem):
                    raise ApiError("ファイル名は半角英数字と _ にしてください (例: hero.png)")
                base = {"sprite": p.sprites_dir, "background": p.backgrounds_dir,
                        "res_sprite": p.res_dir / "sprites", "res_background": p.res_dir / "backgrounds"}.get(kind)
                if not base:
                    raise ApiError("kind が不正です")
                data = self._body()
                if not data.startswith(b"\x89PNG"):
                    raise ApiError("PNG 画像のみ対応しています")
                base.mkdir(parents=True, exist_ok=True)
                dest = base / f"{stem}.png"
                dest.write_bytes(data)
                try:  # 形式チェック
                    if kind.startswith("res_"):
                        warn = None
                    elif kind == "sprite":
                        assets.sprite_frames(dest)
                    else:
                        assets.background_to_tiles(dest)
                    warn = None
                except assets.AssetError as e:
                    warn = str(e)
                return self._json({"ok": True, "warning": warn})

            mm = re.match(r"^/api/asset/(sprite|background)/([^/]+)$", path)
            if mm and m == "DELETE":
                base = p.sprites_dir if mm.group(1) == "sprite" else p.backgrounds_dir
                stem = _safe_name(Path(unquote(mm.group(2))).stem, "アセット")
                f = base / f"{stem}.png"
                if f.exists():
                    f.unlink()
                return self._json({"ok": True})

            if path == "/api/build" and m == "POST":
                body = self._jbody()
                run = bool(body.get("run"))
                install = bool(body.get("install_gbdk"))

                def job_fn(job):
                    if install and not self._gbdk_job(job):
                        return False
                    r = actions.build_project(state.project, log=job.write)
                    if r.ok:
                        job.result = r.report
                        if run:
                            actions.launch_emulator(state.project, r.rom, log=job.write)
                    return r.ok
                return self._json(state.jobs.start("build", job_fn).snapshot())

            if path == "/api/flash" and m == "POST":
                opts = self._jbody()

                def job_fn(job):
                    pr = state.project
                    if opts.get("install_gbdk") and not self._gbdk_job(job):
                        return False
                    if not opts.get("skip_build"):
                        r = actions.build_project(pr, log=job.write)
                        if not r.ok:
                            return False
                    rom = pr.rom_path
                    if not rom.exists():
                        job.write("ROM がありません。先にビルドしてください")
                        return False
                    return actions.flash_rom(pr, rom, log=job.write, progress=job.set_progress)
                return self._json(state.jobs.start("flash", job_fn).snapshot())

            mm = re.match(r"^/api/job/([0-9a-f]+)$", path)
            if mm and m == "GET":
                job = state.jobs.get(mm.group(1))
                if not job:
                    raise ApiError("ジョブがありません", 404)
                return self._json(job.snapshot(int(q.get("since", 0))))

            if path == "/api/ports" and m == "GET":
                return self._json(list_ports())

            if path == "/api/cart/info" and m == "POST":
                cfg = dict(p.settings["flasher"])
                cfg.update(self._jbody())
                if not cfg.get("port"):
                    raise ApiError("ポートを選択してください")
                fl = CartFlasher.open(cfg["port"], int(cfg.get("baud", 187500)), mbc=cfg.get("mbc", "MBC5"),
                                      algorithm=cfg.get("algorithm", "ALG16"), wait_mode=cfg.get("wait_mode", "TOGGLE"))
                try:
                    info = fl.status()
                finally:
                    fl.close()
                return self._json(info.as_dict())

            if path == "/rom" and m == "GET":
                return self._file(p.rom_path, download=p.rom_path.name)

            raise ApiError("not found", 404)

        def _gbdk_job(self, job) -> bool:
            try:
                return actions.ensure_gbdk(state.project if state.root else None, log=job.write,
                                           progress=job.set_progress)
            except toolchain.ToolchainError as e:
                job.write(f"エラー: {e}")
                return False

        def route_global(self, m: str, path: str, q: dict) -> bool:
            """プロジェクトを開いていなくても使える API。処理したら True。"""
            if m == "GET" and (path == "/" or path.startswith("/editor/")):
                rel = "index.html" if path == "/" else path[len("/editor/"):]
                f = (EDITOR_DIR / rel).resolve()
                if EDITOR_DIR.resolve() not in f.parents:
                    raise ApiError("not found", 404)
                self._file(f)
                return True
            if path == "/api/status" and m == "GET":
                home = None
                if state.root:
                    try:
                        home = state.project.settings["build"].get("gbdk_home") or None
                    except ProjectError:
                        home = None
                ports = list_ports()
                pend = getattr(state, "pending", None)
                state.pending = None
                self._json({
                    "pending_adopt": adopt.inspect(pend) if pend and not state.root else None,
                    "project": str(state.root) if state.root else None,
                    "gbdk": toolchain.status(home),
                    "writer": [p for p in ports if p["likely"]],
                    "emulator": "PyBoy" if importlib.util.find_spec("pyboy") else None,
                    "version": __version__,
                })
                return True
            if path == "/api/gbdk/install" and m == "POST":
                self._json(state.jobs.start("setup", self._gbdk_job).snapshot())
                return True
            if path == "/api/home" and m == "GET":
                items = []
                for r in load_recent():
                    try:
                        items.append({"path": r, "name": Project(Path(r)).settings.get("name")})
                    except ProjectError:
                        pass
                self._json({"recent": items, "default_dir": str(Path.home() / "GBForge")})
                return True
            if path == "/api/open" and m == "POST":
                d = self._jbody()
                target = Path(str(d.get("path", "")).strip().strip('"')).expanduser()
                if target.name == "project.gbproj":
                    target = target.parent
                if target.is_dir() and not (target / "project.gbproj").exists():
                    # 既存の GBDK フォルダ → 取り込みの確認を返す
                    self._json({"ok": False, "needs_adopt": True, "info": adopt.inspect(target)})
                    return True
                state.open(target)
                self._json({"ok": True})
                return True
            # ---- プロジェクト探し / フォルダブラウザ ----
            if path == "/api/projects/scan" and m == "GET":
                from . import browse
                self._json({"roots": browse.scan_projects(),
                            "custom_roots": browse.load_user_settings().get("project_roots", [])})
                return True
            if path == "/api/project_roots" and m in ("POST", "DELETE"):
                from . import browse
                d = self._jbody()
                pth = str(Path(str(d.get("path", "")).strip().strip('"')).expanduser())
                us = browse.load_user_settings()
                roots_ = [r for r in us.get("project_roots", []) if r.lower() != pth.lower()]
                if m == "POST":
                    if not Path(pth).is_dir():
                        raise ApiError(f"フォルダがありません: {pth}")
                    roots_.insert(0, pth)
                us["project_roots"] = roots_
                browse.save_user_settings(us)
                self._json({"ok": True, "project_roots": roots_})
                return True
            if path == "/api/fs/roots" and m == "GET":
                from . import browse
                self._json(browse.roots())
                return True
            if path == "/api/fs/list" and m == "GET":
                from . import browse
                try:
                    self._json(browse.list_dir(q.get("path") or str(Path.home())))
                except (FileNotFoundError, PermissionError) as e:
                    raise ApiError(str(e))
                return True
            if path == "/api/inspect" and m == "POST":
                self._json(adopt.inspect(Path(str(self._jbody().get("path", "")).strip().strip('"')).expanduser()))
                return True
            if path == "/api/adopt" and m == "POST":
                d = self._jbody()
                folder = Path(str(d.get("path", "")).strip().strip('"')).expanduser()
                info = adopt.inspect(folder)
                for k in ("build_script_windows", "build_script_unix", "output", "gbdk", "target"):
                    if d.get(k) is not None:
                        info[k] = d[k]
                adopt.adopt(folder, info)
                state.open(folder)
                self._json({"ok": True})
                return True
            if path == "/api/create" and m == "POST":
                from .templates import create_project
                d = self._jbody()
                root = Path(d.get("path", "")).expanduser()
                if not str(d.get("path", "")).strip():
                    raise ApiError("保存先のフォルダを入力してください")
                if root.exists() and any(root.iterdir()):
                    raise ApiError(f"{root} は空のフォルダではありません")
                if d.get("type") == "c":
                    from .templates import create_c_project
                    create_c_project(root, d.get("name") or root.name, target=d.get("target") or "both")
                else:
                    create_project(root, d.get("name") or root.name, sample=bool(d.get("sample", True)))
                state.open(root)
                self._json({"ok": True})
                return True
            if path == "/api/close" and m == "POST":
                state.root = None
                self._json({"ok": True})
                return True
            return False

        def project_summary(self, p: Project) -> dict:
            sprites = []
            for n in p.sprite_names():
                try:
                    _, frames = assets.sprite_frames(p.sprites_dir / f"{n}.png")
                    err = None
                except assets.AssetError as e:
                    frames, err = 0, str(e)
                sprites.append({"name": n, "frames": frames, "error": err})
            bgs = []
            for f in p.background_files():
                info = assets.image_info(p.backgrounds_dir / f)
                bgs.append({"file": f, **info})
            return {
                "root": str(p.root), "settings": p.settings, "scenes": p.scene_names(), "sprites": sprites,
                "backgrounds": bgs, "scripts": p.script_names(), "texts": sorted(p.load_texts()),
                "fonts": available_fonts(), "behaviours": BEHAVIOURS, "directions": DIRECTIONS,
                "rom_exists": p.rom_path.exists(), "version": __version__,
                "type": p.settings.get("type", "engine"), "target": p.settings.get("target", "dmg"),
                "sources": p.source_files() if p.is_c else [],
                "res": sorted(f.relative_to(p.root).as_posix() for f in p.res_dir.rglob("*.png")) if p.is_c and p.res_dir.exists() else [],
                "images": sorted(f.relative_to(p.root).as_posix() for f in p.root.rglob("*.png")
                                 if not ({"build", ".vs", "stages", "docs"} & set(f.relative_to(p.root).parts)))[:500] if p.is_c else [],
                "layout": p.settings.get("layout", "standard"), "adopted": bool(p.settings.get("adopted")),
                "build_mode": p.settings.get("build", {}).get("mode", "gbforge"),
                "rom_name": p.rom_path.name,
                "stages": stages.list_stages(p) if p.is_c else [],
                "object_types": stages.object_types(p),
            }

    return Handler


def serve(root: Path | None, port: int = 8765, open_browser: bool = True, host: str = "127.0.0.1"):
    pending = None
    if root is not None:
        root = Path(root).resolve()
        if not (root / "project.gbproj").exists():
            # 既存の GBDK フォルダ → スタート画面で取り込みを確認
            pending = root if any(root.glob("*.c")) else None
            root = None
    if root is not None:
        Project(root)  # 存在チェック
    state = EditorState(root)
    state.pending = pending
    try:
        httpd = ThreadingHTTPServer((host, port), make_handler(state))
    except OSError:
        httpd = ThreadingHTTPServer((host, 0), make_handler(state))   # ポート使用中なら空きポート
    url = f"http://{host}:{httpd.server_address[1]}/"
    print(f"GBForge エディタ: {url}  (Ctrl+C で終了)")
    print(f"プロジェクト: {root or '(スタート画面)'}")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n終了します")
    finally:
        httpd.server_close()
