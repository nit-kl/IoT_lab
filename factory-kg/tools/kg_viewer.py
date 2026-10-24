"""ナレッジグラフのローカルビューア（決定事項 5）。

    python tools/kg_viewer.py --owner kojima          # http://127.0.0.1:8765 をブラウザで開く
    python tools/kg_viewer.py --owner kojima --port 9000 --no-browser

この PC だけで動く小さなサーバー。画面（viewer/）を配信し、ブラウザからの要求を aws login の認証情報で
SigV4 署名して中継する。中継するのは次の 2 つだけ（懸念点 V2）:

  POST /api/sparql            → KG の SPARQL エンドポイント（読み取り専用）
  GET  /api/replay?url=<URL>  → グラフに書かれた replay の API（dcat:endpointURL で始まり、latest / readings / status のもの）

ほかの Web サイトからこのサーバーを悪用されないよう、待ち受けは 127.0.0.1 のみ、Host ヘッダーは localhost だけ、
API には独自ヘッダー（X-Requested-By: kg-viewer）を必須にしている。使い終わったら Ctrl+C で止める
（画面を開いたままだと自動更新が replay の DB を起こし続ける。懸念点 C1）。
"""

import argparse
import json
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from kgapi import KgClient, signed_call
from kgcommon import PREFIXES, ROOT

VIEWER_DIR = ROOT / "viewer"
REPLAY_PATHS = ("latest", "readings", "status")
REQUIRED_HEADER = ("X-Requested-By", "kg-viewer")
ALLOWED_HOSTS_TTL = 30  # 秒。グラフを更新したら、この時間内に中継先の一覧も追従する
MAX_SPARQL_BYTES = 100_000
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".rq": "text/plain; charset=utf-8", ".svg": "image/svg+xml"}

SERVICES_QUERY = (f"PREFIX dcat: <{PREFIXES['dcat']}>\n"
                  "SELECT DISTINCT ?base WHERE { ?s a dcat:DataService ; dcat:endpointURL ?base }")


def is_allowed_replay_url(url, bases):
    """url が、グラフに書かれた API（bases のどれか）の latest / readings / status か。"""
    parts = urlsplit(url)
    if parts.scheme != "https" or ".." in parts.path or parts.fragment:
        return False
    for base in bases:
        b = urlsplit(base)
        if parts.netloc != b.netloc or not parts.path.startswith(b.path):
            continue
        rest = parts.path[len(b.path):]
        if rest in REPLAY_PATHS:
            return True
    return False


def is_local_host(host_header, port):
    return host_header in (f"127.0.0.1:{port}", f"localhost:{port}")


def static_path(request_path):
    """配信してよい viewer/ 配下のファイルを返す（なければ None）。"""
    path = urlsplit(request_path).path
    if path == "/":
        path = "/index.html"
    target = (VIEWER_DIR / path.lstrip("/")).resolve()
    if VIEWER_DIR.resolve() not in target.parents or not target.is_file():
        return None
    return target


class Backend:
    """KG・replay への署名付き呼び出しと、中継してよい replay の URL の管理。"""

    def __init__(self, owner, region, call=signed_call):
        self.client = KgClient(owner, region)
        self.call = call
        self._bases, self._bases_at = [], 0.0
        self._lock = threading.Lock()

    def sparql(self, query, accept):
        return self.call(self.client.session, "POST", f"{self.client.base}/sparql", data=query.encode(),
                         headers={"Content-Type": "application/sparql-query", "Accept": accept})

    def kg_status(self):
        return self.call(self.client.session, "GET", f"{self.client.base}/status")

    def replay_bases(self):
        with self._lock:
            if time.monotonic() - self._bases_at > ALLOWED_HOSTS_TTL:
                status, body, _ = self.sparql(SERVICES_QUERY, "application/sparql-results+json")
                if status == 200:
                    rows = json.loads(body)["results"]["bindings"]
                    self._bases = [r["base"]["value"] for r in rows]
                    self._bases_at = time.monotonic()
            return list(self._bases)

    def replay(self, url):
        if not is_allowed_replay_url(url, self.replay_bases()):
            return 403, json.dumps({"error": "グラフに書かれた replay の API（latest / readings / status）以外は中継しません"},
                                   ensure_ascii=False).encode(), "application/json; charset=utf-8"
        return self.call(self.client.session, "GET", url)


def make_handler(backend, port):
    class Handler(BaseHTTPRequestHandler):
        server_version = "kg-viewer"

        def log_message(self, fmt, *args):  # 1 行で簡潔に
            sys.stderr.write(f"{self.command} {self.path[:120]} -> {args[1] if len(args) > 1 else ''}\n")

        def send(self, status, body, content_type="application/json; charset=utf-8"):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline'; "
                             "img-src 'self' data:; connect-src 'self'")
            self.end_headers()
            self.wfile.write(body)

        def guard(self, api):
            if not is_local_host(self.headers.get("Host", ""), port):
                self.send(403, {"error": "localhost 以外からのアクセスは受け付けません"})
                return False
            if api and self.headers.get(REQUIRED_HEADER[0]) != REQUIRED_HEADER[1]:
                self.send(403, {"error": f"{REQUIRED_HEADER[0]} ヘッダーがありません"})
                return False
            return True

        def do_GET(self):
            path = urlsplit(self.path).path
            if path.startswith("/api/"):
                if not self.guard(api=True):
                    return
                if path == "/api/config":
                    return self.send(200, {"prefixes": {k: str(v) for k, v in PREFIXES.items()},
                                           "kg": backend.client.base, "replayBases": backend.replay_bases()})
                if path == "/api/kg-status":
                    status, body, ctype = backend.kg_status()
                    return self.send(status, body, ctype or "application/json")
                if path == "/api/replay":
                    url = (parse_qs(urlsplit(self.path).query).get("url") or [""])[0]
                    status, body, ctype = backend.replay(url)
                    return self.send(status, body, ctype or "application/json")
                return self.send(404, {"error": f"{path} はありません"})
            if not self.guard(api=False):
                return
            target = static_path(self.path)
            if target is None:
                return self.send(404, b"not found", "text/plain")
            ctype = CONTENT_TYPES.get(target.suffix)
            if ctype is None:
                return self.send(404, b"not found", "text/plain")
            return self.send(200, target.read_bytes(), ctype)

        def do_POST(self):
            path = urlsplit(self.path).path
            if not self.guard(api=True):
                return
            if path != "/api/sparql":
                return self.send(404, {"error": f"{path} はありません"})
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_SPARQL_BYTES:
                return self.send(413, {"error": "SPARQL が長すぎます"})
            query = self.rfile.read(length).decode()
            status, body, ctype = backend.sparql(query, self.headers.get("Accept") or "application/sparql-results+json")
            return self.send(status, body, ctype or "application/json")

    return Handler


def main():
    ap = argparse.ArgumentParser(description="ナレッジグラフのローカルビューア")
    ap.add_argument("--owner", required=True)
    ap.add_argument("--region", default="ap-northeast-1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="ブラウザを自動で開かない")
    args = ap.parse_args()

    backend = Backend(args.owner, args.region)
    bases = backend.replay_bases()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(backend, args.port))
    url = f"http://127.0.0.1:{args.port}/"
    print(f"ナレッジグラフ: {backend.client.base}")
    print(f"replay の API: {', '.join(bases) or '（グラフに API がありません）'}")
    print(f"ビューア: {url}  （止めるときは Ctrl+C。開いたままにすると自動更新が replay の DB を起こし続けます）")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n停止しました")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
