"""
入店カウンタ PC-01 を模擬する HTTP API サーバー(学習用、ポート8081)

  店舗の出入口の天井に付けるセンサーのつもりです。こうした機器はModbusではなく、メーカーのREST API
  (LANの中のHTTP、またはメーカーのクラウド)で集計値を取り出す形が多いので、それを真似ています。

API(メーカーの仕様書のつもりで読んでください)
--------------------------------------------------------------------
  GET /api/v1/counts?since=<UTCのISO8601>&limit=<件数>
      ヘッダ X-API-Key: <APIキー>   (無い・違う → 401)
      since 以降に始まる「締まった」1分ごとの集計を古い順に返す(今まさに数えている途中の1分は返さない)
      limit は1〜60(省略時60)。過去24時間より前は保持していない
      応答:
        {"sensor_id": "ENT-01",
         "bins": [{"start": "2026-10-15T12:00:00Z", "end": "2026-10-15T12:01:00Z", "in": 5, "out": 3}, …],
         "next_since": "2026-10-15T12:01:00Z",   ← 次の問い合わせの since に使う
         "has_more": false}                       ← true なら続きがある(すぐにもう一度問い合わせる)
  GET /api/v1/health   → {"status": "ok"}

  同じ時間帯を何度問い合わせても同じ値を返します(取り直しが効く)。
  --fail-rate を付けると、その割合で 503(一時的なエラー)を返します。

実行:  python simulator/people_counter.py [--port 8081] [--api-key demo-key-store01] [--fail-rate 0.1]
"""
import argparse
import json
import random
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from store_model import arrivals, departures

SENSOR_ID = "ENT-01"
RETENTION_MIN = 24 * 60


def iso(minute: int) -> str:
    return datetime.fromtimestamp(minute * 60, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_minute(text: str) -> int:
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()) // 60


def make_handler(api_key: str, fail_rate: float):
    class Handler(BaseHTTPRequestHandler):
        def reply(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            url = urlparse(self.path)
            if url.path == "/api/v1/health":
                return self.reply(200, {"status": "ok"})
            if url.path != "/api/v1/counts":
                return self.reply(404, {"error": "not found"})
            if self.headers.get("X-API-Key") != api_key:
                return self.reply(401, {"error": "invalid api key"})
            if random.random() < fail_rate:
                return self.reply(503, {"error": "temporarily unavailable"})
            q = parse_qs(url.query)
            now_min = int(time.time()) // 60          # 今数えている途中の1分(まだ返さない)
            try:
                since = parse_minute(q["since"][0]) if "since" in q else now_min - 60
                limit = int(q.get("limit", ["60"])[0])
            except ValueError:
                return self.reply(400, {"error": "bad since/limit"})
            if not 1 <= limit <= 60:
                return self.reply(400, {"error": "limit must be 1-60"})
            start = max(since, now_min - RETENTION_MIN)
            end = min(start + limit, now_min)
            bins = [{"start": iso(m), "end": iso(m + 1), "in": arrivals(m), "out": departures(m)}
                    for m in range(start, end)]
            self.reply(200, {"sensor_id": SENSOR_ID, "bins": bins,
                             "next_since": iso(end) if bins else iso(max(since, start)),
                             "has_more": end < now_min})

        def log_message(self, fmt, *args) -> None:
            print(f"{self.address_string()} {fmt % args}", flush=True)

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description="PC-01 入店カウンタ(HTTP API)シミュレータ")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8081)
    ap.add_argument("--api-key", default="demo-key-store01")
    ap.add_argument("--fail-rate", type=float, default=0.0, help="この割合で 503 を返す(0〜1)")
    args = ap.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(args.api_key, args.fail_rate))
    print(f"PC-01 入店カウンタ(HTTP API)起動: http://{args.host}:{args.port}/api/v1/counts  Ctrl+Cで終了", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
