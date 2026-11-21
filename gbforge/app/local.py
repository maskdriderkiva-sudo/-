"""エディタサーバー (gbforge/server.py) の処理を、HTTP を使わずにプロセス内で呼び出す。

ブラウザ版と同じ処理を通るので、ビルド・保存・変換などの結果は 2 つの画面で必ず同じになる。
ソケットは開かない (外部からは一切アクセスできない)。
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from urllib.parse import quote, urlencode

from .. import server


class ApiFail(Exception):
    """API がエラーを返した (メッセージは日本語でそのまま表示できる)。"""

    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


class Response:
    def __init__(self):
        self.status = 200
        self.headers: dict[str, str] = {}
        self.body = b""
        self.json = None


class LocalApi:
    def __init__(self, root: Path | None = None, pending: Path | None = None):
        self.state = server.EditorState(root)
        self.state.pending = pending
        base = server.make_handler(self.state)

        class Local(base):  # type: ignore[misc, valid-type]
            def __init__(self, method: str, path: str, data: bytes):   # noqa: D401  (ソケットなし)
                self.command = method
                self.path = path
                self.headers = {"Content-Length": str(len(data))}
                self.rfile = io.BytesIO(data)
                self.wfile = io.BytesIO()
                self.res = Response()

            def _json(self, obj, status=200):
                self.res.status = status
                self.res.json = obj
                self.res.headers["Content-Type"] = "application/json"

            def _file(self, path, download=None):
                if not Path(path).is_file():
                    raise server.ApiError("ファイルがありません", 404)
                self.res.body = Path(path).read_bytes()

            def send_response(self, code, message=None):
                self.res.status = code

            def send_header(self, key, value):
                self.res.headers[key] = str(value)

            def end_headers(self):
                pass

        self._cls = Local

    # ------------------------------------------------------------------
    def request(self, method: str, url: str, body=None, raw: bytes | None = None) -> Response:
        data = raw if raw is not None else (json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else b"")
        h = self._cls(method, url, data)
        h._dispatch(method)
        res = h.res
        if not res.body:
            res.body = h.wfile.getvalue()
        if res.json is not None and res.status >= 400:
            raise ApiFail(res.json.get("error", "エラー"), res.status)
        return res

    def call(self, method: str, url: str, body=None, raw: bytes | None = None):
        """JSON の API。エラーは ApiFail。"""
        return self.request(method, url, body, raw).json

    def get(self, url: str, **q):
        return self.call("GET", url + (("?" + urlencode({k: v for k, v in q.items() if v is not None})) if q else ""))

    def post(self, url: str, body=None, **q):
        return self.call("POST", url + (("?" + urlencode(q)) if q else ""), body if body is not None else {})

    def put(self, url: str, body, **q):
        return self.call("PUT", url + (("?" + urlencode(q)) if q else ""), body)

    def delete(self, url: str, **q):
        return self.call("DELETE", url + (("?" + urlencode(q)) if q else ""))

    def binary(self, method: str, url: str, raw: bytes | None = None) -> Response:
        return self.request(method, url, raw=raw)

    # ------------------------------------------------------------------
    @property
    def root(self) -> Path | None:
        return self.state.root

    @property
    def project(self):
        return self.state.project


def q(s: str) -> str:
    return quote(s, safe="")
