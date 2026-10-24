"""③ AI エージェント（server/agent.py）のテスト。Bedrock は偽物（決まった応答を返す）にし、MCP のツールはデモの backend で本物を動かす。

費用の歯止め（モデルの限定・1 回の上限・日月の予算・中止時の記録）が確実に効くことを確かめる。
"""

import http.client
import json
import socket
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP / "server"))

import app_server  # noqa: E402
from agent import CONFIG_PATH, Agent, AgentError, Ledger, clean_history, cost_yen, load_config  # noqa: E402
from demo import DemoBackend  # noqa: E402

BACKEND = DemoBackend(start_offset_minutes=14 * 60 + 40)  # CSV 上 14:40 から（CNC-01 は 14:30〜14:39 に異常停止）


class FakeClock:
    def __init__(self, t=1_792_800_000.0):  # 2026-10-24（日本時間）
        self.t = t

    def __call__(self):
        return self.t


def usage(inp=1000, out=100, read=0, write=0):
    return {"inputTokens": inp, "outputTokens": out, "totalTokens": inp + out,
            "cacheReadInputTokens": read, "cacheWriteInputTokens": write}


def text_reply(text, u=None):
    events = [{"messageStart": {"role": "assistant"}}]
    for i in range(0, len(text), 5):
        events.append({"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": text[i:i + 5]}}})
    events += [{"contentBlockStop": {"contentBlockIndex": 0}}, {"messageStop": {"stopReason": "end_turn"}},
               {"metadata": {"usage": u or usage()}}]
    return events


def tool_reply(name, tool_input, tool_id="t1", u=None, text="調べます。"):
    raw = json.dumps(tool_input, ensure_ascii=False)
    return [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": text}}},
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"contentBlockStart": {"contentBlockIndex": 1, "start": {"toolUse": {"toolUseId": tool_id, "name": name}}}},
        {"contentBlockDelta": {"contentBlockIndex": 1, "delta": {"toolUse": {"input": raw[: len(raw) // 2]}}}},
        {"contentBlockDelta": {"contentBlockIndex": 1, "delta": {"toolUse": {"input": raw[len(raw) // 2:]}}}},
        {"contentBlockStop": {"contentBlockIndex": 1}},
        {"messageStop": {"stopReason": "tool_use"}},
        {"metadata": {"usage": u or usage()}},
    ]


class FakeBedrock:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def converse_stream(self, **request):
        self.requests.append(json.loads(json.dumps(request)))
        reply = self.replies.pop(0) if self.replies else tool_reply("get_status", {}, tool_id=f"t{len(self.requests)}")
        return {"stream": iter(reply)}


class AgentTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = FakeClock()
        self.ledger = Ledger(Path(self.tmp.name) / "usage.jsonl", clock=self.clock)
        self.events = []

    def config(self, **replace):
        """config/agent.toml の一部を書き換えた設定ファイルを作る。"""
        text = CONFIG_PATH.read_text(encoding="utf-8")
        for old, new in replace.items():
            self.assertIn(old, text)
            text = text.replace(old, new)
        path = Path(self.tmp.name) / "agent.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def agent(self, replies, config_path=CONFIG_PATH):
        self.bedrock = FakeBedrock(replies)
        return Agent(BACKEND, bedrock=self.bedrock, config_path=config_path, ledger=self.ledger)

    def ask(self, agent, question="CNC-01 の状態は？", history=None, model=None):
        agent.ask(question, history or [], model, self.events.append)
        return self.events[-1]

    def of(self, kind):
        return [e for e in self.events if e["type"] == kind]


class AnswerTest(AgentTestCase):
    def test_tools_then_answer(self):
        agent = self.agent([
            tool_reply("get_equipment", {"equipment_id": "CNC-01"}, "t1", usage(3000, 80, write=2500)),
            tool_reply("highlight_equipment", {"ids": ["CNC-01", "NOPE-01"], "reason": "異常停止"}, "t2", usage(500, 60, read=2500)),
            text_reply("CNC-01 は 14:30 から異常停止していました（CSV 上の時刻）。", usage(600, 40, read=2500)),
        ])
        done = self.ask(agent)
        self.assertEqual(done["type"], "done")
        self.assertEqual(done["stop"], "end_turn")
        self.assertEqual((done["usage"]["calls"], done["usage"]["tool_calls"]), (3, 2))

        # 使ったツール（MCP 経由で本物の REST の処理を通る）と、画面の強調
        starts = self.of("tool_start")
        self.assertEqual([s["name"] for s in starts], ["get_equipment", "highlight_equipment"])
        self.assertIn("CNC-01", self.of("tool_end")[0]["preview"])
        self.assertEqual(self.of("highlight"), [{"type": "highlight", "ids": ["CNC-01"], "reason": "異常停止"}])
        self.assertIn("異常停止していました", "".join(e["delta"] for e in self.of("text")))

        # Bedrock への要求: 日本国内の Haiku、出力の上限、キャッシュの目印、ツールの結果を返している
        first, second = self.bedrock.requests[0], self.bedrock.requests[1]
        self.assertEqual(first["modelId"], "jp.anthropic.claude-haiku-5-5")
        self.assertEqual(first["inferenceConfig"]["maxTokens"], 2000)
        self.assertIn({"cachePoint": {"type": "default"}}, first["system"])
        self.assertIn({"cachePoint": {"type": "default"}}, first["toolConfig"]["tools"])
        names = {t["toolSpec"]["name"] for t in first["toolConfig"]["tools"] if "toolSpec" in t}
        self.assertTrue({"get_equipment", "get_readings", "highlight_equipment"} <= names)
        result = second["messages"][-1]["content"][0]["toolResult"]
        self.assertEqual(result["toolUseId"], "t1")
        self.assertIn('"id": "CNC-01"', result["content"][0]["text"])

        # 台帳: 呼び出しごとに実際のトークン数で記録し、円は単価から計算
        lines = [json.loads(l) for l in self.ledger.path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(lines), 3)
        self.assertFalse(any(l["estimated"] for l in lines))
        model = load_config()["models"]["haiku"]
        expected = sum(cost_yen(model, u, 150) for u in (usage(3000, 80, write=2500), usage(500, 60, read=2500),
                                                         usage(600, 40, read=2500)))
        self.assertAlmostEqual(done["usage"]["yen"], round(expected, 4), places=4)
        self.assertAlmostEqual(self.ledger.totals()["today"], expected, places=5)
        self.assertLess(expected, 0.2)  # 普通の質問なら 1 円未満

    def test_tool_error_is_passed_to_model(self):
        agent = self.agent([tool_reply("get_equipment", {"equipment_id": "NOPE-01"}), text_reply("見つかりません。")])
        self.ask(agent)
        end = self.of("tool_end")[0]
        self.assertTrue(end["error"])
        self.assertIn("NOPE-01", end["preview"])
        result = self.bedrock.requests[1]["messages"][-1]["content"][0]["toolResult"]
        self.assertEqual(result["status"], "error")

    def test_unknown_tool(self):
        agent = self.agent([tool_reply("delete_everything", {}), text_reply("できません。")])
        self.ask(agent)
        self.assertTrue(self.of("tool_end")[0]["error"])

    def test_history_is_sent(self):
        agent = self.agent([text_reply("はい。")])
        history = [{"role": "user", "text": "CNC-01 は？"}, {"role": "assistant", "text": "稼働中です。"},
                   {"role": "user", "text": "答えのない質問"}, {"role": "bogus", "text": "x"}]
        self.ask(agent, "では PRESS-01 は？", history)
        messages = self.bedrock.requests[0]["messages"]
        self.assertEqual([m["role"] for m in messages], ["user", "assistant", "user"])
        self.assertEqual(messages[-1]["content"][0]["text"], "では PRESS-01 は？")


class SafeguardTest(AgentTestCase):
    def test_only_allowed_models(self):
        agent = self.agent([text_reply("x")])
        with self.assertRaises(AgentError):
            self.ask(agent, model="opus")  # 設定で許可していない（Haiku の約 40 倍）
        with self.assertRaises(AgentError):
            self.ask(agent, model="sonnet")
        self.assertEqual(self.bedrock.requests, [])
        self.assertEqual([m["key"] for m in agent.status()["models"] if m["allowed"]], ["haiku"])

    def test_config_rejects_global_profile_and_bad_default(self):
        with self.assertRaises(ValueError):
            load_config(self.config(**{'model_id = "jp.anthropic.claude-opus-5-5"': 'model_id = "global.anthropic.claude-opus-5-5"'}))
        with self.assertRaises(ValueError):
            load_config(self.config(**{'default = "haiku"': 'default = "opus"'}))
        with self.assertRaises(ValueError):
            load_config(self.config(**{"daily_yen = 100 ": "daily_yen = 0 "}))

    def test_daily_budget_reached(self):
        self.ledger.add(model="haiku", yen=100.0)
        agent = self.agent([text_reply("x")])
        self.assertFalse(agent.status()["enabled"])
        with self.assertRaises(AgentError) as ctx:
            self.ask(agent)
        self.assertIn("今日の予算", str(ctx.exception))
        self.assertEqual(self.bedrock.requests, [])

    def test_monthly_budget_reached(self):
        self.ledger.add(model="haiku", yen=1000.0)
        self.clock.t += 86400  # 翌日（同じ月）
        agent = self.agent([text_reply("x")])
        status = agent.status()
        self.assertFalse(status["enabled"])
        self.assertIn("今月の予算", status["reason"])
        self.assertEqual(status["spent"]["today"], 0)

    def test_stops_before_exceeding_daily_budget(self):
        self.ledger.add(model="haiku", yen=99.9)  # 残り 0.1 円。1 回の呼び出しの最悪の見積もりはそれより大きい
        agent = self.agent([text_reply("x")])
        done = self.ask(agent)
        self.assertEqual(done["stop"], "daily_budget")
        self.assertEqual(self.bedrock.requests, [])

    def test_per_question_budget(self):
        agent = self.agent([], self.config(**{"per_question_yen = 5 ": "per_question_yen = 0.4 "}))
        done = self.ask(agent)  # 偽の Bedrock はツールを求め続ける
        self.assertEqual(done["stop"], "question_budget")
        self.assertLessEqual(done["usage"]["yen"], 0.4)
        self.assertGreater(len(self.bedrock.requests), 1)

    def test_tool_call_limit(self):
        agent = self.agent([], self.config(**{"max_tool_calls = 8 ": "max_tool_calls = 2 "}))
        done = self.ask(agent)
        self.assertEqual(done["stop"], "tool_limit")
        self.assertEqual(done["usage"]["tool_calls"], 2)
        self.assertEqual(len(self.bedrock.requests), 4)  # 上限の後に 1 回だけ「答えて」と返し、それでもツールを求めたら止める

    def test_input_limit_drops_old_turns(self):
        agent = self.agent([text_reply("x")], self.config(**{"max_input_tokens = 30000 ": "max_input_tokens = 9000 "}))
        history = []
        for i in range(5):
            history += [{"role": "user", "text": f"質問{i}" + "あ" * 1500}, {"role": "assistant", "text": "答え" + "い" * 1500}]
        done = self.ask(agent, history=history)
        self.assertIn("古いやりとりを省いて", done["notice"])
        self.assertLess(len(self.bedrock.requests[0]["messages"]), 11)

    def test_disconnect_records_estimate(self):
        # 画面が閉じた（書き込みに失敗した）ら止める。途中の呼び出しも、送った分の見積もりを記録する（少なく数えない）
        agent = self.agent([text_reply("長い答え" * 20)])

        def emit(event):
            if event["type"] == "text":
                raise BrokenPipeError()

        with self.assertRaises(BrokenPipeError):
            agent.ask("CNC-01 は？", [], None, emit)
        lines = [json.loads(l) for l in self.ledger.path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0]["estimated"])
        self.assertGreater(lines[0]["inputTokens"], 1000)
        self.assertFalse(agent.busy.locked())  # 次の質問を受け付けられる

    def test_one_question_at_a_time(self):
        agent = self.agent([text_reply("x")])
        agent.busy.acquire()
        with self.assertRaises(AgentError):
            self.ask(agent)
        agent.busy.release()

    def test_clean_history(self):
        self.assertEqual(clean_history("bad"), [])
        turns = clean_history([{"role": "user", "text": "q"}, {"role": "assistant", "text": "a" * 10000}] * 15)
        self.assertEqual(len(turns), 10)
        self.assertEqual(len(turns[0][1]["content"][0]["text"]), 4000)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class AgentEndpointTest(AgentTestCase):
    def setUp(self):
        super().setUp()
        self.port = free_port()
        self.agent_obj = self.agent([
            tool_reply("highlight_equipment", {"ids": ["PRESS-01"]}),
            text_reply("PRESS-01 です。"),
        ])
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), app_server.make_handler(BACKEND, self.port, self.agent_obj))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        headers = {"Host": f"127.0.0.1:{self.port}", "X-Requested-By": "factory-app", "Content-Type": "application/json"}
        conn.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=headers)
        res = conn.getresponse()
        raw = res.read().decode()
        conn.close()
        return res.status, res.getheader("Content-Type"), raw

    def test_status(self):
        status, _, raw = self.request("GET", "/api/agent")
        body = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertTrue(body["enabled"])
        self.assertEqual(body["limits"]["daily_yen"], 100)

    def test_ask_streams_ndjson(self):
        status, ctype, raw = self.request("POST", "/api/agent/ask", {"question": "プレス機は？", "history": []})
        self.assertEqual(status, 200)
        self.assertIn("ndjson", ctype)
        events = [json.loads(l) for l in raw.splitlines()]
        self.assertEqual(events[-1]["type"], "done")
        self.assertIn({"type": "highlight", "ids": ["PRESS-01"], "reason": ""}, events)

    def test_rejections(self):
        status, _, raw = self.request("POST", "/api/agent/ask", {"question": "x", "model": "opus"})
        self.assertEqual(status, 409)
        self.assertIn("使えません", json.loads(raw)["error"])
        status, _, _ = self.request("POST", "/api/agent/ask", {"question": ""})
        self.assertEqual(status, 409)


if __name__ == "__main__":
    unittest.main()
