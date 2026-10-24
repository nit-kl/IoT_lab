"""ローカルビューア（tools/kg_viewer.py と viewer/）のテスト。AWS は呼ばず、署名付き呼び出しを差し替えて動かす。

    python -m unittest discover -s tests -v
"""

import http.client
import json
import sys
import threading
import unittest
from pathlib import Path
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import kg_viewer  # noqa: E402
import query  # noqa: E402
from kgcommon import EXAMPLE_INSTANCES  # noqa: E402

BASE = "https://abc123.lambda-url.ap-northeast-1.on.aws/"


class AllowListTest(unittest.TestCase):
    def test_allowed(self):
        for path in ("latest?equipment=CNC-01&metric=status", "readings?equipment=CNC-01&metric=x&next=5", "status"):
            self.assertTrue(kg_viewer.is_allowed_replay_url(BASE + path, [BASE]), path)

    def test_rejected(self):
        for url in (
            "https://evil.example/latest",                                   # 別のホスト
            "http://abc123.lambda-url.ap-northeast-1.on.aws/latest",         # https でない
            BASE + "reset",                                                  # 許可していないパス（リセットは中継しない）
            BASE + "../latest",                                              # パスの迷い込み
            BASE + "latest/../reset",
            "https://abc123.lambda-url.ap-northeast-1.on.aws.evil.example/latest",
            BASE + "latest#x",
            "",
        ):
            self.assertFalse(kg_viewer.is_allowed_replay_url(url, [BASE]), url)
        self.assertFalse(kg_viewer.is_allowed_replay_url(BASE + "latest", []))  # グラフに API がない

    def test_static_path(self):
        self.assertEqual(kg_viewer.static_path("/").name, "index.html")
        self.assertEqual(kg_viewer.static_path("/queries/nodes.rq").name, "nodes.rq")
        for bad in ("/../tools/kg_viewer.py", "/..%2F..%2Ftools/kgapi.py", "/nope.js", "/queries/"):
            self.assertIsNone(kg_viewer.static_path(bad), bad)


class FakeSession:
    pass


class FakeBackend(kg_viewer.Backend):
    """AWS を呼ばない Backend。SPARQL はローカルの Oxigraph、replay は固定の応答。"""

    def __init__(self):
        self.client = type("C", (), {"base": "https://kg.example", "session": FakeSession()})()
        self.store, _ = query.open_store(EXAMPLE_INSTANCES)
        self.replay_calls = []
        self._bases, self._bases_at = [], 0.0
        self._lock = threading.Lock()

    def call(self, session, method, url, data=None, headers=None):
        if url.endswith("/sparql"):
            result = self.store.query(data.decode(), use_default_graph_as_union=True)
            from pyoxigraph import QueryResultsFormat
            return 200, result.serialize(format=QueryResultsFormat.JSON), "application/sparql-results+json"
        if url.endswith("/status") and url.startswith("https://kg.example"):
            return 200, json.dumps({"triples": len(self.store)}).encode(), "application/json"
        self.replay_calls.append(url)
        return 200, json.dumps({"items": [{"value": 1.0}]}).encode(), "application/json"


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = FakeBackend()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), kg_viewer.make_handler(cls.backend, 0))
        cls.port = cls.server.server_address[1]
        cls.server.RequestHandlerClass = kg_viewer.make_handler(cls.backend, cls.port)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, method, path, body=None, headers=None, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": host or f"127.0.0.1:{self.port}", **(headers or {})}
        conn.request(method, path, body=body, headers=h)
        res = conn.getresponse()
        data = res.read()
        conn.close()
        return res.status, res.getheader("Content-Type"), data

    API = {"X-Requested-By": "kg-viewer"}

    def test_static_files(self):
        status, ctype, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn("工場ナレッジグラフ", body.decode())
        status, ctype, _ = self.request("GET", "/app.js")
        self.assertEqual((status, ctype.split(";")[0]), (200, "text/javascript"))
        self.assertEqual(self.request("GET", "/../tools/kgapi.py")[0], 404)

    def test_api_requires_header_and_local_host(self):
        self.assertEqual(self.request("GET", "/api/config")[0], 403)                                  # ヘッダーなし
        self.assertEqual(self.request("GET", "/api/config", headers=self.API, host="evil.example")[0], 403)  # DNS リバインディング対策
        self.assertEqual(self.request("GET", "/", host="evil.example")[0], 403)
        status, _, body = self.request("GET", "/api/config", headers=self.API)
        self.assertEqual(status, 200)
        self.assertIn("https://replay-api.invalid/", json.loads(body)["replayBases"])

    def test_sparql_proxy(self):
        q = "SELECT (COUNT(*) AS ?n) WHERE { ?s <https://xcp-factory.example/def#equipmentId> ?id }"
        status, _, body = self.request("POST", "/api/sparql", body=q.encode(),
                                       headers={**self.API, "Content-Type": "application/sparql-query"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["results"]["bindings"][0]["n"]["value"], "5")

    def test_replay_proxy_allow_list(self):
        from urllib.parse import quote
        ok = "https://replay-api.invalid/latest?equipment=CNC-01&metric=status"
        status, _, _ = self.request("GET", "/api/replay?url=" + quote(ok, safe=""), headers=self.API)
        self.assertEqual(status, 200)
        self.assertIn(ok, self.backend.replay_calls)
        for bad in ("https://replay-api.invalid/reset", "https://evil.example/latest"):
            status, _, body = self.request("GET", "/api/replay?url=" + quote(bad, safe=""), headers=self.API)
            self.assertEqual(status, 403, bad)
            self.assertNotIn(bad, self.backend.replay_calls)

    def test_post_only_sparql(self):
        self.assertEqual(self.request("POST", "/api/replay", body=b"x", headers=self.API)[0], 404)


class ViewerQueriesTest(unittest.TestCase):
    """viewer/queries/*.rq（画面が使う SPARQL）がサンプルのグラフで期待どおりの行を返すこと。"""

    @classmethod
    def setUpClass(cls):
        cls.store, _ = query.open_store(EXAMPLE_INSTANCES)

    def rows(self, name, node=None):
        sparql = (ROOT / "viewer" / "queries" / f"{name}.rq").read_text(encoding="utf-8")
        if node:
            sparql = sparql.replace("$NODE", f"<{node}>")
        _, rows = query.run(self.store, sparql)
        return rows

    def test_describe_placeholder_only_in_pattern(self):
        # app.js は $NODE を置き換える。コメントなど別の場所に $NODE があると、置き換え漏れで全ノードに一致してしまう
        sparql = (ROOT / "viewer" / "queries" / "describe.rq").read_text(encoding="utf-8")
        self.assertEqual(sparql.count("$NODE"), 1)
        self.assertNotIn("$NODE", "".join(l for l in sparql.splitlines() if l.lstrip().startswith("#")))

    def test_nodes(self):
        rows = self.rows("nodes")
        kinds = sorted(r[1] for r in rows)
        self.assertEqual(kinds, ["area", "equipment", "equipment", "equipment", "equipment", "equipment",
                                 "factory", "line", "site"])
        cnc = next(r for r in rows if r[3] == "CNC-01")
        self.assertEqual((cnc[2], cnc[5]), ("CNC 加工機 01", "CNC 加工機"))

    def test_properties(self):
        rows = self.rows("properties")
        self.assertEqual(len(rows), 15)
        status = [r for r in rows if r[3] == "status"]
        self.assertEqual(len(status), 2)
        self.assertTrue(all(r[12] for r in status))  # コード表あり

    def test_services_and_codes(self):
        services = self.rows("services")
        self.assertEqual(len(services), 1)
        self.assertTrue(services[0][4].startswith("readings{?equipment,metric"))
        codes = self.rows("codes")
        self.assertIn(["id:scheme/equipment-status", "2", "異常停止"], codes)

    def test_describe(self):
        rows = self.rows("describe", "https://xcp-factory.example/id/equipment/CNC-01")
        predicates = {r[0] for r in rows}
        self.assertIn("s4inma:belongsToCategory", predicates)
        self.assertIn("xf:equipmentId", predicates)
        graphs = {r[4] for r in rows}
        self.assertIn("xf:placement", predicates)  # 配置は layout.ttl（別の名前付きグラフ）
        self.assertEqual(graphs, {"https://xcp-factory.example/graph/instances", "https://xcp-factory.example/graph/layout"})


if __name__ == "__main__":
    unittest.main()
