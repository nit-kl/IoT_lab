"""工場アプリのサーバー（server/app_server.py）とデモモード（server/demo.py）のテスト。AWS は呼ばない。

    ../factory-kg/.venv/Scripts/python.exe -m unittest discover -s tests -v
"""

import http.client
import json
import socket
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP / "server"))

import app_server  # noqa: E402
from demo import DemoBackend, DemoReplay  # noqa: E402

HEADER = {"X-Requested-By": "factory-app"}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class DemoReplayTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        # 60 倍速・0:00 から再生（1 秒で CSV 上の 1 分進む）
        self.replay = DemoReplay(speed=60, start_offset_minutes=0, clock=self.clock)

    def test_status_progresses_and_loops(self):
        _, first = self.replay("https://x/status")
        self.clock.t += 12 * 60  # CSV 上の 12 時間
        _, half = self.replay("https://x/status")
        self.assertLess(first["reflected_rows"], half["reflected_rows"])
        self.assertTrue(half["csv_position"].endswith("12:00:00") or "T12:00" in half["csv_position"], half["csv_position"])
        self.clock.t += 12 * 60 + 60  # 1 日を過ぎると最初に戻る
        _, looped = self.replay("https://x/status")
        self.assertLess(looped["reflected_rows"], half["reflected_rows"])

    def test_latest_is_not_from_future(self):
        self.clock.t += 9 * 60
        _, body = self.replay("https://x/latest?equipment=CNC-01&metric=spindle_load")
        self.assertEqual(body["count"], 1)
        self.assertLessEqual(body["items"][0]["source_ts"][11:16], "09:00")

    def test_readings_paging(self):
        self.clock.t += 3 * 60
        _, page1 = self.replay("https://x/readings?equipment=CNC-01&metric=spindle_load&limit=50")
        self.assertEqual(page1["count"], 50)
        self.assertIsNotNone(page1["next"])
        _, page2 = self.replay(f"https://x/readings?equipment=CNC-01&metric=spindle_load&limit=50&next={page1['next']}")
        self.assertGreater(page2["items"][0]["seq"], page1["items"][-1]["seq"])

    def test_start_offset(self):
        # --demo-start 14:25 にあたる。CNC-01 は 14:30 から異常停止（2）
        replay = DemoReplay(speed=60, start_offset_minutes=14 * 60 + 25, clock=self.clock)
        _, body = replay("https://x/latest?equipment=CNC-01&metric=status")
        self.assertEqual(body["items"][0]["source_ts"][11:16], "14:25")
        self.clock.t += 6  # CSV 上の 6 分後
        _, body = replay("https://x/latest?equipment=CNC-01&metric=status")
        self.assertEqual(body["items"][0]["value"], 2)

    def test_unknown_path(self):
        status, _ = self.replay("https://x/reset")
        self.assertEqual(status, 404)


class AppServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        cls.server = ThreadingHTTPServer(("127.0.0.1", cls.port), app_server.make_handler(DemoBackend(), cls.port))
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, method, path, body=None, headers=None, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        conn.putrequest(method, path, skip_host=True)
        conn.putheader("Host", host or f"127.0.0.1:{self.port}")
        for k, v in (headers or {}).items():
            conn.putheader(k, v)
        data = body.encode() if isinstance(body, str) else body
        conn.putheader("Content-Length", str(len(data or b"")))
        conn.endheaders(data)
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        try:
            return res.status, json.loads(raw)
        except ValueError:
            return res.status, raw

    def test_config(self):
        status, body = self.request("GET", "/api/config", headers=HEADER)
        self.assertEqual(status, 200)
        self.assertEqual(body["mode"], "demo")
        self.assertIn("s4inma", body["prefixes"])

    def test_requires_header(self):
        status, _ = self.request("GET", "/api/config")
        self.assertEqual(status, 403)
        status, _ = self.request("POST", "/api/sparql", body="SELECT * WHERE { ?s ?p ?o } LIMIT 1")
        self.assertEqual(status, 403)

    def test_rejects_other_hosts(self):
        # DNS リバインディング対策: Host が localhost でなければ画面も API も返さない
        for path in ("/api/config", "/"):
            status, _ = self.request("GET", path, headers=HEADER, host=f"evil.example:{self.port}")
            self.assertEqual(status, 403, path)

    def test_rest(self):
        status, body = self.request("GET", "/api/rest?path=/v1/factory", headers=HEADER)
        self.assertEqual(status, 200)
        self.assertEqual(body["factories"][0]["kind"], "factory")
        status, body = self.request("GET", "/api/rest?path=/v1/equipment/CNC-01/properties/spindle_load", headers=HEADER)
        self.assertEqual(status, 200)
        self.assertEqual(body["metric"], "spindle_load")

    def test_layout_in_factory(self):
        # 3D 工場の配置（factory-kg の layout.ttl）が /v1/factory に入っている
        status, body = self.request("GET", "/api/rest?path=/v1/factory", headers=HEADER)
        line = body["factories"][0]["children"][0]["children"][0]["children"][0]
        self.assertEqual(line["layout"]["width"], 25.0)
        self.assertTrue(all("layout" in e for e in line["children"]))

    def test_rest_rejects_bad_paths(self):
        for path in ("/v2/factory", "/v1/../admin", "/v1/factory?x", "", "https://evil.example/v1/factory"):
            status, _ = self.request("GET", "/api/rest?path=" + path.replace("?", "%3F"), headers=HEADER)
            self.assertEqual(status, 400, path)

    def test_sparql(self):
        status, body = self.request("POST", "/api/sparql", headers=HEADER,
                                    body="SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }")
        self.assertEqual(status, 200)
        self.assertGreater(int(body["results"]["bindings"][0]["n"]["value"]), 100)

    def test_sparql_update_rejected(self):
        status, _ = self.request("POST", "/api/sparql", headers=HEADER,
                                 body="INSERT DATA { <urn:a> <urn:b> <urn:c> }")
        self.assertEqual(status, 400)

    def test_body_too_large(self):
        status, _ = self.request("POST", "/api/sparql", headers=HEADER, body="#" * (app_server.MAX_BODY + 1))
        self.assertEqual(status, 413)

    def test_static_paths(self):
        self.assertIsNone(app_server.static_file("/../server/app_server.py"))
        self.assertIsNone(app_server.static_file("/..%2Fserver/app_server.py"))
        self.assertIsNone(app_server.static_file("/nope.js"))
        if app_server.DIST.exists():
            self.assertEqual(app_server.static_file("/").name, "index.html")
            self.assertEqual(app_server.static_file("/some/page").name, "index.html")  # 画面の中の移動


if __name__ == "__main__":
    unittest.main()
