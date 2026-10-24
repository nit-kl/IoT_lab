"""REST API（aws/lambda/api.py・domain.py）のテスト。グラフは同梱の例、replay は同梱のサンプル CSV から応答する偽物。

    python -m unittest discover -s tests -v
"""

import csv
import json
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "aws" / "lambda"))

import api  # noqa: E402
import domain as dom  # noqa: E402
import graphstore  # noqa: E402
import publish_ttl  # noqa: E402
from kgcommon import EXAMPLE_INSTANCES  # noqa: E402

SAMPLE_CSV = ROOT.parent / "factory-replay" / "data" / "factory_sample.csv"
REPLAY = "https://replay-api.invalid/"
NOW = "2026-10-01T14:35"  # 偽の replay の「今」（CSV 上の時刻）。CNC-01 は異常停止中


class FakeReplay:
    """replay の HTTP API の偽物（latest / readings / status）。同梱の CSV を NOW までリプレイした状態を返す。"""

    def __init__(self):
        with open(SAMPLE_CSV, encoding="utf-8") as f:
            self.rows = [r for r in csv.DictReader(f) if r["ts"][:16] <= NOW]
        for i, r in enumerate(self.rows, start=1):
            r["seq"], r["value"] = i, float(r["value"])
        self.calls = []
        self.down = False

    def item(self, r):
        return {"seq": r["seq"], "ts": "2026-10-24T13:00:00+09:00", "source_ts": r["ts"], "line_id": r["line_id"],
                "equipment_id": r["equipment_id"], "metric": r["metric"], "value": r["value"], "unit": r["unit"]}

    def __call__(self, url):
        self.calls.append(url)
        if self.down:
            raise ConnectionError("replay の環境がありません")
        parts = urlsplit(url)
        q = {k: v[0] for k, v in parse_qs(parts.query).items()}
        path = parts.path.rsplit("/", 1)[-1]
        if path == "status":
            return 200, {"status": "running", "percent": 60.4, "csv_position": NOW + ":00+09:00"}
        match = [r for r in self.rows if r["equipment_id"] == q.get("equipment") and r["metric"] == q.get("metric")]
        if path == "latest":
            return 200, {"items": [self.item(match[-1])] if match else [], "count": 1 if match else 0}
        if path == "readings":
            if "source_from" in q:
                match = [r for r in match if r["ts"][:16] >= q["source_from"][:16]]
            if "source_to" in q:
                match = [r for r in match if r["ts"][:16] <= q["source_to"][:16]]
            after = int(q.get("next", 0))
            match = [r for r in match if r["seq"] > after]
            limit = int(q.get("limit", 1000))
            page = match[:limit]
            return 200, {"items": [self.item(r) for r in page], "count": len(page),
                         "next": str(page[-1]["seq"]) if len(match) > limit else None, "window": {}}
        return 404, {"error": "not found"}


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make_domain(replay=None, clock=None):
    body, _ = publish_ttl.build_nquads(EXAMPLE_INSTANCES)
    cache = graphstore.GraphCache("b", "k", 30, fetch=lambda etag: None if etag == '"v1"' else (body, '"v1"', "1"))
    return dom.Domain(cache, replay or FakeReplay(), clock=clock or FakeClock())


class DomainTest(unittest.TestCase):
    def setUp(self):
        self.replay = FakeReplay()
        self.clock = FakeClock()
        self.d = make_domain(self.replay, self.clock)

    def test_factory_tree(self):
        tree = self.d.factory()["factories"]
        self.assertEqual(len(tree), 1)
        site = tree[0]["children"][0]
        area = site["children"][0]
        line = area["children"][0]
        self.assertEqual((tree[0]["label"], site["kind"], area["label"], line["id"]), ("サンプル工場", "site", "製造エリア", "LINE-A"))
        self.assertEqual([c["id"] for c in line["children"]], ["CNC-01", "COMP-01", "CONV-01", "ENV-01", "PRESS-01"])
        # 配置（layout.ttl）: エリア・ライン・設備に付き、工場・サイトには無い
        self.assertNotIn("layout", tree[0])
        self.assertEqual(area["layout"]["width"], 30.0)
        self.assertEqual(line["children"][0]["layout"],
                         {"x": 22.5, "y": 10.0, "width": 3.2, "depth": 2.4, "height": 2.5, "rotation": 0.0})
        self.assertEqual(self.replay.calls, [])  # 構造だけなので replay は呼ばない

    def test_list_equipment_with_status(self):
        items = {e["id"]: e for e in self.d.list_equipment()["items"]}
        self.assertEqual(len(items), 5)
        self.assertEqual(items["CNC-01"]["status"]["value"], {"value": 2, "label": "異常停止"})
        self.assertEqual(items["PRESS-01"]["status"]["value"], {"value": 1, "label": "稼働"})
        self.assertNotIn("status", items["ENV-01"])  # 稼働状態を持たない
        self.assertEqual(items["CNC-01"]["category"]["label"], "CNC 加工機")
        self.assertIn("s4inma:ProductionEquipment", items["CNC-01"]["types"])

    def test_filters(self):
        self.assertEqual(self.d.list_equipment(line="LINE-A")["count"], 5)
        self.assertEqual(self.d.list_equipment(line="LINE-Z")["count"], 0)
        for category in ("cnc", "CNC 加工機", "CNC"):
            self.assertEqual([e["id"] for e in self.d.list_equipment(category=category)["items"]], ["CNC-01"], category)

    def test_equipment_detail(self):
        e = self.d.equipment_detail("CNC-01")
        self.assertEqual([p["metric"] for p in e["properties"]], ["spindle_load", "spindle_speed", "status", "work_count"])
        load = next(p for p in e["properties"] if p["metric"] == "spindle_load")
        self.assertEqual(load["unit"]["symbol"], "%")
        self.assertEqual(load["latest"]["source_ts"], "2026-10-01T14:35:00+09:00")
        self.assertTrue(load["endpoints"]["latest"].startswith(REPLAY))
        self.assertEqual(e["status"]["value"]["label"], "異常停止")
        status = next(p for p in e["properties"] if p["metric"] == "status")
        self.assertEqual((status["unit"]["id"], status["unit"]["symbol"]), ("unit:UNITLESS", None))

    def test_not_found(self):
        for args in (("ROBOT-01",), ("../etc",)):
            with self.assertRaises(dom.NotFound):
                self.d.equipment_detail(*args)
        with self.assertRaises(dom.NotFound) as ctx:
            self.d.property_detail("CNC-01", "temperature")
        self.assertIn("spindle_load", str(ctx.exception))  # 使える計測項目を案内する

    def test_readings_and_paging(self):
        r = self.d.readings("CNC-01", "spindle_load", {"source_from": "2026-10-01T14:00", "source_to": "2026-10-01T14:10",
                                                       "limit": "5"})
        self.assertEqual(r["count"], 5)
        self.assertIsNotNone(r["next"])
        r2 = self.d.readings("CNC-01", "spindle_load", {"source_from": "2026-10-01T14:00", "source_to": "2026-10-01T14:10",
                                                        "limit": "5", "next": r["next"]})
        self.assertEqual([i["source_ts"][11:16] for i in r2["items"]], ["14:05", "14:06", "14:07", "14:08", "14:09"])
        status = self.d.readings("CNC-01", "status", {"source_from": "2026-10-01T14:30", "limit": "1"})
        self.assertEqual(status["items"][0]["value"], {"value": 2, "label": "異常停止"})

    def test_readings_bad_params(self):
        with self.assertRaises(dom.BadRequest):
            self.d.readings("CNC-01", "spindle_load", {"limit": "99999"})
        with self.assertRaises(dom.BadRequest):
            self.d.readings("CNC-01", "spindle_load", {"equipment": "PRESS-01"})  # パラメータの差し替えは不可

    def test_summary_of_status(self):
        s = self.d.readings_summary("CNC-01", "status", {"source_from": "2026-10-01T06:00"})
        states = {x["label"]: x["minutes"] for x in s["states"]}
        self.assertEqual(states["異常停止"], 6)  # 14:30〜14:35（NOW まで）
        self.assertIn("稼働", states)
        changes = [(c["source_ts"][11:16], c["value"]["label"]) for c in s["changes"]]
        self.assertIn(("14:30", "異常停止"), changes)
        self.assertLessEqual(len(s["points"]), dom.SUMMARY_POINTS)

    def test_summary_of_numbers(self):
        s = self.d.readings_summary("CNC-01", "spindle_load", {"source_from": "2026-10-01T13:00", "source_to": "2026-10-01T14:35"})
        self.assertEqual((s["max"], s["max_at"][11:16]), (92.9, "14:26"))  # CSV 上の実際の最大（チョコ停の直前）
        self.assertEqual(s["min"], 0)  # 異常停止中は 0
        self.assertFalse(s["truncated"])

    def test_codes_and_search(self):
        codes = self.d.codes("equipment-status")
        self.assertEqual([c["label"] for c in codes["values"]], ["停止", "稼働", "異常停止"])
        with self.assertRaises(dom.NotFound):
            self.d.codes("nope")
        self.assertEqual([e["id"] for e in self.d.search("コンプレッサ")["equipment"]], ["COMP-01"])
        self.assertEqual({p["metric"] for p in self.d.search("主軸")["properties"]}, {"spindle_load", "spindle_speed"})
        with self.assertRaises(dom.BadRequest):
            self.d.search(" ")

    def test_status(self):
        s = self.d.status()
        self.assertEqual(s["counts"], {"lines": 1, "equipment": 5, "properties": 15})
        self.assertEqual(s["replay"]["status"], "running")
        self.assertEqual(s["graph"]["etag"], '"v1"')

    def test_replay_unavailable_keeps_structure(self):
        self.replay.down = True
        e = self.d.equipment_detail("CNC-01")
        self.assertEqual(len(e["properties"]), 4)
        self.assertIn("unavailable", e["properties"][0]["latest"])
        self.assertIn("unavailable", self.d.readings("CNC-01", "status", {}))
        self.assertIn("unavailable", self.d.status()["replay"])

    def test_latest_cache(self):
        self.d.property_detail("ENV-01", "temperature")
        calls = len(self.replay.calls)
        self.d.property_detail("ENV-01", "temperature")
        self.assertEqual(len(self.replay.calls), calls)  # 15 秒以内はキャッシュ
        self.clock.now += dom.LATEST_CACHE_SECONDS + 1
        self.d.property_detail("ENV-01", "temperature")
        self.assertEqual(len(self.replay.calls), calls + 1)

    def test_only_graph_urls_are_called(self):
        self.assertFalse(self.d.allowed("https://evil.example/latest"))
        self.assertFalse(self.d.allowed(REPLAY + "reset"))
        self.assertTrue(self.d.allowed(REPLAY + "latest?equipment=CNC-01&metric=status"))
        body, error = self.d.replay(REPLAY + "reset")
        self.assertIsNone(body)
        self.assertNotIn(REPLAY + "reset", self.replay.calls)


class HandlerTest(unittest.TestCase):
    def setUp(self):
        self._orig = api.domain
        api.domain = make_domain()
        self.addCleanup(setattr, api, "domain", self._orig)

    def call(self, path, query=None, method="GET"):
        res = api.handler({"requestContext": {"http": {"method": method}}, "rawPath": path,
                           "queryStringParameters": query}, None)
        return res["statusCode"], json.loads(res["body"])

    def test_routes(self):
        for path, key in (("/v1/factory", "factories"), ("/v1/equipment", "items"), ("/v1/equipment/CNC-01", "properties"),
                          ("/v1/equipment/CNC-01/properties/status", "codes"),
                          ("/v1/equipment/CNC-01/properties/status/readings", "items"),
                          ("/v1/equipment/CNC-01/properties/status/readings/summary", "states"),
                          ("/v1/codes/equipment-status", "values"), ("/v1/status", "counts")):
            code, body = self.call(path)
            self.assertEqual(code, 200, (path, body))
            self.assertIn(key, body, path)
        self.assertEqual(self.call("/v1/search", {"q": "CNC"})[0], 200)

    def test_errors(self):
        self.assertEqual(self.call("/v1/equipment/NOPE-01")[0], 404)
        self.assertEqual(self.call("/v1/equipment/CNC-01/properties/status/readings", {"limit": "0"})[0], 400)
        self.assertEqual(self.call("/v1/nope")[0], 404)
        self.assertEqual(self.call("/v1/equipment", method="POST")[0], 405)
        self.assertEqual(self.call("/v1/equipment/../../etc")[0], 404)

    def test_openapi_covers_routes(self):
        spec = (ROOT / "aws" / "openapi.yaml").read_text(encoding="utf-8")
        for path in ("/v1/factory:", "/v1/equipment:", "/v1/equipment/{id}:", "/v1/equipment/{id}/properties/{metric}:",
                     "/v1/equipment/{id}/properties/{metric}/readings:", "/v1/equipment/{id}/properties/{metric}/readings/summary:",
                     "/v1/codes/{key}:", "/v1/search:", "/v1/status:"):
            self.assertIn(path, spec)
        self.assertEqual(len(api.ROUTES), 9)


if __name__ == "__main__":
    unittest.main()
