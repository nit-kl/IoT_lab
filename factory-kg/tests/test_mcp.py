"""MCP サーバー（tools/kgmcp_tools.py）のテスト。本物の MCP クライアントをプロセス内でつなぐ。

REST の呼び出しは api.dispatch()（将来のリモート MCP と同じ経路）、replay は同梱のサンプル CSV から応答する偽物。

    python -m unittest discover -s tests -v
"""

import asyncio
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "aws" / "lambda"), str(Path(__file__).resolve().parent)]

import api  # noqa: E402
import query  # noqa: E402
from kgcommon import EXAMPLE_INSTANCES  # noqa: E402
from kgmcp_tools import BackendError, build_server  # noqa: E402
from mcp import Client  # noqa: E402
from pyoxigraph import QueryResultsFormat  # noqa: E402
from test_rest_api import make_domain  # noqa: E402


class DispatchBackend:
    """HTTP を介さず api.dispatch() を呼ぶ backend（リモート MCP を api Lambda で動かすときと同じ形）。"""

    def __init__(self):
        self.store, _ = query.open_store(EXAMPLE_INSTANCES)
        self.sparql_calls = []

    def rest(self, path, params=None):
        status, body = api.dispatch(path, params)
        if status != 200:
            raise BackendError(f"HTTP {status}: {body.get('error')}")
        return body

    def sparql(self, q):
        self.sparql_calls.append(q)
        return json.loads(self.store.query(q, use_default_graph_as_union=True).serialize(format=QueryResultsFormat.JSON))


def run(coro):
    return asyncio.run(coro)


class McpTest(unittest.TestCase):
    def setUp(self):
        self._orig = api.domain
        api.domain = make_domain()  # 偽の replay（CSV 上 14:35 まで。CNC-01 は異常停止中）
        self.addCleanup(setattr, api, "domain", self._orig)
        self.backend = DispatchBackend()

    async def call(self, server, name, args=None):
        async with Client(server) as c:
            result = await c.call_tool(name, args or {})
            text = result.content[0].text
            return result.is_error, (text if result.is_error else json.loads(text))

    def tool(self, name, args=None, enable_sparql=True):
        return run(self.call(build_server(self.backend, enable_sparql), name, args))

    def test_tools_are_listed_with_descriptions(self):
        async def main():
            async with Client(build_server(self.backend)) as c:
                return (await c.list_tools()).tools
        tools = {t.name: t for t in run(main())}
        self.assertEqual(set(tools), {"list_lines", "list_equipment", "get_equipment", "get_property", "get_readings",
                                      "search", "get_status", "sparql_query"})
        for t in tools.values():
            self.assertTrue(t.annotations.read_only_hint, t.name)  # すべて読み取り専用
            self.assertGreater(len(t.description), 30, t.name)
        props = tools["get_readings"].input_schema["properties"]
        self.assertIn("CSV 上の時刻", props["source_from"]["description"])  # 引数にも説明が付く
        self.assertEqual(tools["get_readings"].input_schema["required"], ["equipment_id", "metric"])

    def test_sparql_tool_can_be_disabled(self):
        async def main():
            async with Client(build_server(self.backend, enable_sparql=False)) as c:
                return [t.name for t in (await c.list_tools()).tools]
        self.assertNotIn("sparql_query", run(main()))  # リモート版の既定

    def test_list_lines_and_equipment(self):
        error, body = self.tool("list_lines")
        self.assertFalse(error)
        self.assertEqual([(l["id"], len(l["equipment"])) for l in body["lines"]], [("LINE-A", 5)])
        error, body = self.tool("list_equipment", {"category": "プレス機"})
        self.assertEqual([e["id"] for e in body["equipment"]], ["PRESS-01"])

    def test_get_equipment_answers_current_state(self):
        error, body = self.tool("get_equipment", {"equipment_id": "CNC-01"})
        self.assertFalse(error)
        self.assertEqual(body["status"]["value"], {"value": 2, "label": "異常停止"})
        load = next(p for p in body["properties"] if p["metric"] == "spindle_load")
        self.assertEqual((load["unit"], load["latest"]["source_ts"]), ("%", "2026-10-01T14:35:00+09:00"))

    def test_errors_reach_the_agent(self):
        error, text = self.tool("get_equipment", {"equipment_id": "NOPE-01"})
        self.assertTrue(error)
        self.assertIn("設備 NOPE-01 はありません", text)  # 理由がエージェントに届く
        error, text = self.tool("get_property", {"equipment_id": "CNC-01", "metric": "temperature"})
        self.assertIn("spindle_load", text)  # 使える計測項目を案内する

    def test_get_readings_summary_by_default(self):
        error, body = self.tool("get_readings", {"equipment_id": "CNC-01", "metric": "status",
                                                 "source_from": "2026-10-01T14:00", "source_to": "2026-10-01T15:00"})
        self.assertFalse(error)
        self.assertIn(("14:30", "異常停止"), [(c["source_ts"][11:16], c["value"]["label"]) for c in body["changes"]])
        self.assertIn("states", body)
        self.assertNotIn("items", body)  # 既定は要約（値そのものは返さない）

    def test_get_readings_detail(self):
        error, body = self.tool("get_readings", {"equipment_id": "CNC-01", "metric": "spindle_load", "detail": True,
                                                 "source_from": "2026-10-01T14:00", "limit": 3})
        self.assertEqual([i["source_ts"][11:16] for i in body["items"]], ["14:00", "14:01", "14:02"])
        self.assertIsNotNone(body["next"])
        error, body2 = self.tool("get_readings", {"equipment_id": "CNC-01", "metric": "spindle_load", "detail": True,
                                                  "source_from": "2026-10-01T14:00", "limit": 3, "next": body["next"]})
        self.assertEqual(body2["items"][0]["source_ts"][11:16], "14:03")
        error, text = self.tool("get_readings", {"equipment_id": "CNC-01", "metric": "spindle_load", "detail": True,
                                                 "limit": 5000})
        self.assertTrue(error)  # 上限 1000 を超える指定は弾く

    def test_search_and_status(self):
        error, body = self.tool("search", {"query": "温度"})
        self.assertEqual({(p["equipment"], p["metric"]) for p in body["properties"]},
                         {("ENV-01", "temperature"), ("CONV-01", "motor_temp")})
        error, body = self.tool("get_status")
        self.assertEqual(body["counts"]["equipment"], 5)

    def test_sparql_query(self):
        error, body = self.tool("sparql_query", {"query": "PREFIX xf: <https://xcp-factory.example/def#> "
                                                          "SELECT ?id WHERE { ?e xf:equipmentId ?id } ORDER BY ?id"})
        self.assertFalse(error)
        self.assertEqual([r["id"] for r in body["rows"]], ["CNC-01", "COMP-01", "CONV-01", "ENV-01", "PRESS-01"])
        self.assertIn("LIMIT 201", self.backend.sparql_calls[-1])  # LIMIT を自動で付ける
        error, text = self.tool("sparql_query", {"query": "INSERT DATA { <urn:a> <urn:b> <urn:c> }"})
        self.assertTrue(error)
        self.assertIn("更新はできません", text)

    def test_resources(self):
        async def main():
            async with Client(build_server(self.backend)) as c:
                names = [str(r.uri) for r in (await c.list_resources()).resources]
                ontology = (await c.read_resource("factory-kg://ontology")).contents[0].text
                codes = json.loads((await c.read_resource("factory-kg://codes")).contents[0].text)
                instructions = c.instructions
                return names, ontology, codes, instructions
        names, ontology, codes, instructions = run(main())
        self.assertEqual(set(names), {"factory-kg://ontology", "factory-kg://codes"})
        self.assertIn("s4inma:WorkCenter", ontology)
        self.assertIn("- CNC-01 CNC 加工機 01（CNC 加工機）: spindle_load, spindle_speed, status, work_count", ontology)
        self.assertEqual([v["label"] for v in codes["values"]], ["停止", "稼働", "異常停止"])
        self.assertIn("source_ts は CSV 上の時刻", instructions)


if __name__ == "__main__":
    unittest.main()
