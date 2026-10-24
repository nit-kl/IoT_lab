"""工場アプリのサーバー（各自の PC で動く）。画面（web/dist）を配信し、KG・REST API への要求を署名して中継する。

    python server/app_server.py --owner kojima          # AWS の KG・replay を使う（http://127.0.0.1:8770/）
    python server/app_server.py --demo                  # AWS を使わないデモモード（手元のグラフ + サンプル CSV）

    GET  /api/config               画面の設定（モード・接頭辞など）
    GET  /api/rest?path=/v1/…&…    REST API（GET の /v1/… だけ）
    POST /api/sparql               KG の SPARQL（読み取りのみ。KG 側でも更新は拒否される）
    GET  /api/agent                ③ エージェントの状態（使えるモデル・上限・今日と今月の費用）
    POST /api/agent/ask            ③ に質問する（{"question", "history", "model"}）。答えは 1 行 1 JSON で逐次返す
    POST /api/agent/cancel         ③ の回答を中止する

ほかの Web サイトから悪用されないよう、待ち受けは 127.0.0.1 のみ、Host ヘッダーは localhost だけ、
API には独自ヘッダー（X-Requested-By: factory-app）を必須にしている（factory-kg のビューアと同じ）。
"""

import argparse
import json
import re
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[2]
APP = Path(__file__).resolve().parents[1]
DIST = APP / "web" / "dist"
sys.path[:0] = [str(ROOT / "factory-kg" / "tools")]

from kgcommon import PREFIXES  # noqa: E402

REQUIRED_HEADER = ("X-Requested-By", "factory-app")
REST_PATH = re.compile(r"^/v1/[A-Za-z0-9_/-]{1,200}$")
MAX_BODY = 100_000
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
                 ".json": "application/json", ".woff2": "font/woff2", ".ico": "image/x-icon"}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "worker-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")


class AwsBackend:
    """AWS の KG（SPARQL・REST API）を aws login の認証情報で署名して呼ぶ。"""

    mode = "aws"

    def __init__(self, owner, region):
        from kgapi import KgClient, signed_call
        self.client, self.call = KgClient(owner, region), signed_call
        self.owner = owner

    def rest(self, path, params):
        from urllib.parse import urlencode
        query = urlencode(params)
        return self.call(self.client.session, "GET", f"{self.client.api_base}{path}" + (f"?{query}" if query else ""))

    def sparql(self, query, accept):
        return self.call(self.client.session, "POST", f"{self.client.base}/sparql", data=query.encode(),
                         headers={"Content-Type": "application/sparql-query", "Accept": accept})

    def describe(self):
        return {"mode": "aws", "owner": self.owner, "kg": self.client.base, "rest": self.client.api_base}


agent_unavailable = "③ エージェントは使えません"


def set_agent_unavailable(message):
    global agent_unavailable
    agent_unavailable = message


def static_file(path):
    """web/dist の中のファイル。拡張子のないパス（画面の中の移動）は index.html を返す。"""
    rel = urlsplit(path).path.lstrip("/") or "index.html"
    target = (DIST / rel).resolve()
    if DIST.resolve() not in target.parents and target != DIST.resolve():
        return None
    if target.is_file():
        return target
    if "." not in Path(rel).name:
        index = DIST / "index.html"
        return index if index.is_file() else None
    return None


def make_handler(backend, port, agent=None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "factory-app"

        def log_message(self, fmt, *args):
            sys.stderr.write(f"{self.command} {self.path[:120]} -> {args[1] if len(args) > 1 else ''}\n")

        def send(self, status, body, content_type="application/json; charset=utf-8", cache=False):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=31536000, immutable" if cache else "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", CSP)
            self.end_headers()
            self.wfile.write(body)

        def guard(self, api):
            host = self.headers.get("Host", "")
            if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
                self.send(403, {"error": "localhost 以外からのアクセスは受け付けません"})
                return False
            if api and self.headers.get(REQUIRED_HEADER[0]) != REQUIRED_HEADER[1]:
                self.send(403, {"error": f"{REQUIRED_HEADER[0]} ヘッダーがありません"})
                return False
            return True

        def do_GET(self):
            parts = urlsplit(self.path)
            if parts.path.startswith("/api/"):
                if not self.guard(api=True):
                    return
                query = {k: v[0] for k, v in parse_qs(parts.query).items()}
                if parts.path == "/api/config":
                    return self.send(200, {**backend.describe(), "prefixes": {k: str(v) for k, v in PREFIXES.items()}})
                if parts.path == "/api/agent":
                    if agent is None:
                        return self.send(200, {"enabled": False, "reason": agent_unavailable, "models": [], "spent": {}})
                    return self.send(200, agent.status())
                if parts.path == "/api/rest":
                    path = query.pop("path", "")
                    if not REST_PATH.fullmatch(path) or ".." in path:
                        return self.send(400, {"error": "path は /v1/… で指定してください"})
                    status, body, ctype = backend.rest(path, query)
                    return self.send(status, body, ctype or "application/json")
                return self.send(404, {"error": f"{parts.path} はありません"})
            if not self.guard(api=False):
                return
            target = static_file(self.path)
            if target is None:
                if not DIST.exists():
                    return self.send(503, "画面がビルドされていません。web フォルダで npm run build を実行してください".encode(),
                                     "text/plain; charset=utf-8")
                return self.send(404, b"not found", "text/plain")
            ctype = CONTENT_TYPES.get(target.suffix)
            if ctype is None:
                return self.send(404, b"not found", "text/plain")
            return self.send(200, target.read_bytes(), ctype, cache="/assets/" in self.path)

        def do_POST(self):
            path = urlsplit(self.path).path
            if not self.guard(api=True):
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self.send(413, {"error": "要求が大きすぎます"})
            body = self.rfile.read(length)
            if path == "/api/sparql":
                status, data, ctype = backend.sparql(body.decode(), self.headers.get("Accept") or "application/sparql-results+json")
                return self.send(status, data, ctype or "application/json")
            if path == "/api/agent/ask":
                return self.ask(body)
            if path == "/api/agent/cancel":
                if agent:
                    agent.cancel()
                return self.send(200, {"cancelled": True})
            return self.send(404, {"error": f"{path} はありません"})

        def ask(self, body):
            """③ に質問する。答えは 1 行 1 JSON（NDJSON）で、届いた順に書き出す。画面が閉じたら（書き込みに失敗したら）止める。"""
            from agent import AgentError, Cancelled, explain_error
            if agent is None:
                return self.send(503, {"error": agent_unavailable})
            try:
                request = json.loads(body)
                assert isinstance(request, dict)
            except (ValueError, AssertionError):
                return self.send(400, {"error": "JSON で送ってください"})
            started = False

            def emit(event):
                nonlocal started
                if not started:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Content-Security-Policy", CSP)
                    self.end_headers()
                    started = True
                self.wfile.write((json.dumps(event, ensure_ascii=False) + "\n").encode())
                self.wfile.flush()

            try:
                agent.ask(request.get("question"), request.get("history"), request.get("model"), emit)
            except AgentError as e:
                if not started:
                    return self.send(409, {"error": str(e)})
                emit({"type": "error", "message": str(e)})
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                agent.cancel()  # 画面が閉じた・中止した
            except Cancelled:
                try:
                    emit({"type": "cancelled"})
                except OSError:
                    pass
            except Exception as e:  # noqa: BLE001  画面に理由を出す
                sys.stderr.write(f"agent: {type(e).__name__}: {e}\n")
                try:
                    emit({"type": "error", "message": explain_error(e)})
                except OSError:
                    pass

    return Handler


def main():
    ap = argparse.ArgumentParser(description="工場アプリのサーバー")
    ap.add_argument("--owner", help="AWS の KG・replay の利用者名（--demo でないときは必須）")
    ap.add_argument("--region", default="ap-northeast-1")
    ap.add_argument("--demo", action="store_true", help="AWS を使わないデモモード")
    ap.add_argument("--demo-speed", type=float, default=60.0, help="デモの再生速度（既定 60 倍）")
    ap.add_argument("--demo-start", default="06:00", metavar="HH:MM",
                    help="デモを CSV 上のどの時刻から再生するか（既定 06:00。異常停止を見るなら 14:25 など）")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    if not args.demo and not args.owner:
        ap.error("--owner を指定するか、--demo で起動してください")
    sys.stdout.reconfigure(line_buffering=True)

    if args.demo:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from demo import DemoBackend
        m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", args.demo_start)
        if not m:
            ap.error("--demo-start は HH:MM で指定してください（例: 14:25）")
        backend = DemoBackend(speed=args.demo_speed, start_offset_minutes=int(m[1]) * 60 + int(m[2]))
    else:
        backend = AwsBackend(args.owner, args.region)
    agent = None
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from agent import Agent
        agent = Agent(backend, region=args.region)
    except Exception as e:  # noqa: BLE001  設定の誤りなどでも ①・② は使えるようにする
        set_agent_unavailable(f"③ エージェントの設定を読めません: {e}")
        print(f"注意: {agent_unavailable}", file=sys.stderr)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(backend, args.port, agent))
    url = f"http://127.0.0.1:{args.port}/"
    for k, v in backend.describe().items():
        print(f"{k:7}: {v}")
    print(f"アプリ : {url}  （止めるときは Ctrl+C）")
    if not DIST.exists():
        print("注意: 画面がビルドされていません（web フォルダで npm install && npm run build）", file=sys.stderr)
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
