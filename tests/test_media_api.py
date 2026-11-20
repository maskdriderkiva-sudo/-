"""GB Media Studio の HTTP API (エディタの画面が使う) のテスト。

素材の取り込み → 保存 → 変換プレビュー → 変換 (ジョブ) → プレビュー用フレーム → C ソースの書き出し → (GBDK があれば) プロジェクトのビルド。
"""
import json
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gbforge import builder, server, templates  # noqa: E402
from gbforge.media import video  # noqa: E402

SAMPLES = ROOT / "gbforge" / "media" / "samples"


@pytest.fixture()
def editor(tmp_path):
    root = tmp_path / "proj"
    templates.create_c_project(root, "Media Test", target="cgb")
    st = server.EditorState(root)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(st))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def call(m, url, body=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        rq = urllib.request.Request(base + url, data=data, method=m,
                                    headers={} if raw is not None else {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(rq) as r:
                ct = r.headers.get("Content-Type", "")
                b = r.read()
                return json.loads(b) if "json" in ct else b
        except urllib.error.HTTPError as e:
            return {"HTTP": e.code, **json.loads(e.read())}

    def job(r):
        for _ in range(1200):
            s = call("GET", f"/api/job/{r['id']}?since=0")
            if s["done"]:
                return s
            time.sleep(0.25)
        raise TimeoutError

    yield root, call, job
    httpd.shutdown()


def test_media_api_flow(editor):
    root, call, job = editor
    info = call("GET", "/api/media/info")
    assert "fade_in_black" in info["fx_types"] and info["bytes_per_update"]["dmg"] > 0
    assert call("POST", "/api/media/new", {"name": "../bad"})["HTTP"] == 400
    mp = call("POST", "/api/media/new", {"name": "opening"})["path"]
    assert mp == "media/opening.gbmedia"
    assert call("GET", "/api/media/list")[0]["target"] == "cgb"      # C プロジェクトがカラーなのでカラー
    a = call("POST", f"/api/media/import?path={mp}&name=astronaut.jpg", raw=(SAMPLES / "astronaut.jpg").read_bytes())
    assert a["kind"] == "image" and (root / "media" / a["path"]).exists()
    assert call("POST", f"/api/media/import?path={mp}&name=evil.exe", raw=b"MZ")["HTTP"] == 400
    proj = call("GET", f"/api/media/project?path={mp}")
    proj["assets"] = [{"id": "a1", "name": "宇宙", "path": a["path"], "kind": "image", "params": {"preset": "old_mono"}}]
    sc = proj["scenes"][0]
    sc.update({"length": 200, "media": [{"id": "c1", "asset": "a1", "start": 40, "duration": 150}],
               "fx": [{"id": "f1", "type": "fade_in_black", "start": 40, "duration": 30}],
               "palette": [{"id": "k", "frame": 0, "level": -31, "interp": "step"}]})
    if video.find_ffmpeg(None):
        v = call("POST", f"/api/media/import?path={mp}&name=zoom.mp4", raw=(SAMPLES / "astronaut_zoom.mp4").read_bytes())
        pr = call("GET", f"/api/media/probe?path={mp}&asset_path={v['path']}")
        assert pr["duration"] == pytest.approx(4.0, abs=0.1)
        proj["assets"].append({"id": "v1", "name": "ズーム", "path": v["path"], "kind": "video", "params": {"dither": "bayer4"},
                               "video": {"fps": 10}})
    assert call("PUT", f"/api/media/project?path={mp}", proj)["ok"]
    call("PUT", f"/api/media/project?path={mp}", proj)
    assert (root / "media" / "opening.gbmedia.bak").exists()          # 1 つ前を残す

    pv = call("POST", f"/api/media/preview?path={mp}", {"asset": "a1"})
    assert pv["tiles"] > 0 and pv["converted"] and pv["source"]
    th = call("GET", f"/api/media/thumb?path={mp}&asset=a1&asset_path={a['path']}&adjusted=1")
    assert th[:4] == b"\x89PNG"

    b = job(call("POST", f"/api/media/build?path={mp}", {}))
    assert b["ok"], b["log"]
    assert b["result"]["report"]["assets"][0]["tiles"] > 0
    fr = call("GET", f"/api/media/frames?path={mp}&scene=0")
    assert fr["length"] == 200 and len(fr["frames"]) == 201 and fr["screens"]
    assert fr["levels"][0] == -31 and fr["levels"][100] == 0

    ex = job(call("POST", f"/api/media/export?path={mp}", {}))
    assert ex["ok"], ex["log"]
    assert "src/gbmedia/gbmedia.c" in ex["result"]["files"]
    assert (root / "src" / "gbmedia" / "gbmedia_data.h").read_text(encoding="utf-8").count("GBM_SCENE_OPENING") == 1

    if builder.find_gbdk(None):
        main = root / "src" / "main.c"
        main.write_text('#include <gb/gb.h>\n#include "gbmedia/gbmedia.h"\n#include "gbmedia/gbmedia_data.h"\n'
                        "void main(void) { SHOW_BKG; DISPLAY_ON; gbmedia_init(); "
                        "gbmedia_load_scene(GBM_SCENE_OPENING); gbmedia_play(); while (1) vsync(); }\n", encoding="utf-8")
        r = job(call("POST", "/api/build", {}))
        assert r["ok"], "\n".join(r["log"][-15:])
