"""デモモードの backend: AWS を使わずに、手元のグラフと同梱のサンプル CSV で KG・REST・replay を再現する（費用 0 円）。

- グラフ: factory-kg の ontology/*.ttl + examples/instances.sample.ttl（API の URL は仮の https://replay-api.invalid/）
- REST:  factory-kg の domain.Domain（本物と同じ処理）を手元で動かす
- replay: サンプル CSV を、起動からの経過時間 × speed で「再生」する偽物（1 日分を流し終えたら最初に戻る）
"""

import bisect
import csv
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "factory-kg" / "tools"), str(ROOT / "factory-kg" / "aws" / "lambda")]

import graphstore  # noqa: E402
import query as kgquery  # noqa: E402
from domain import Domain  # noqa: E402
from kgcommon import EXAMPLE_INSTANCES  # noqa: E402
from pyoxigraph import QueryResultsFormat, RdfFormat  # noqa: E402

SAMPLE_CSV = ROOT / "factory-replay" / "data" / "factory_sample.csv"
JST = timezone(timedelta(hours=9))


class DemoReplay:
    """replay の HTTP API（latest / readings / status）の偽物。CSV 上の時刻は「起動からの経過 × speed」で進む。"""

    def __init__(self, speed=60.0, start_offset_minutes=6 * 60, clock=time.time):
        self.speed, self.clock = speed, clock
        with open(SAMPLE_CSV, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.base = datetime.fromisoformat(rows[0]["ts"])
        self.series = {}
        for seq, r in enumerate(rows, start=1):
            offset = (datetime.fromisoformat(r["ts"]) - self.base).total_seconds()
            self.series.setdefault((r["equipment_id"], r["metric"]), []).append(
                (offset, seq, r["ts"], float(r["value"]), r["unit"], r["line_id"]))
        self.total = len(rows)
        self.day = 86400.0
        # 6:00（稼働開始）から再生を始めると、すぐに動きが見える
        self.started = clock() - start_offset_minutes * 60 / speed

    def position(self):
        """今の CSV 上の経過秒（0〜86400 を繰り返す）と、何周目か。"""
        elapsed = (self.clock() - self.started) * self.speed
        return elapsed % self.day, int(elapsed // self.day)

    def reflected_ts(self, offset, now_offset):
        """CSV 上の時刻 offset の行が反映された（ことにする）時刻。"""
        return datetime.fromtimestamp(self.clock() - (now_offset - offset) / self.speed, JST).isoformat()

    def item(self, entry, key, now_offset):
        offset, seq, ts, value, unit, line = entry
        return {"seq": seq, "ts": self.reflected_ts(offset, now_offset), "source_ts": ts, "line_id": line,
                "equipment_id": key[0], "metric": key[1], "value": value, "unit": unit}

    def __call__(self, url):
        parts = urlsplit(url)
        q = {k: v[0] for k, v in parse_qs(parts.query).items()}
        path = parts.path.rsplit("/", 1)[-1]
        now_offset, loop = self.position()
        if path == "status":
            reflected = sum(bisect.bisect_right([e[0] for e in s], now_offset) for s in self.series.values())
            return 200, {"status": "running", "source_key": "incoming/factory_sample.csv（デモ）", "speed": self.speed,
                         "total_rows": self.total, "reflected_rows": reflected,
                         "percent": round(reflected * 100 / self.total, 1),
                         "csv_position": (self.base + timedelta(seconds=now_offset)).isoformat(),
                         "csv_end": (self.base + timedelta(seconds=self.day - 60)).isoformat(),
                         "started_at": datetime.fromtimestamp(self.started + loop * self.day / self.speed, JST).isoformat()}
        key = (q.get("equipment"), q.get("metric"))
        entries = [e for e in self.series.get(key, []) if e[0] <= now_offset]
        if path == "latest":
            return 200, {"items": [self.item(entries[-1], key, now_offset)] if entries else [], "count": int(bool(entries))}
        if path == "readings":
            if "source_from" in q:
                entries = [e for e in entries if e[2][:16] >= q["source_from"][:16]]
            if "source_to" in q:
                entries = [e for e in entries if e[2][:16] <= q["source_to"][:16]]
            if "minutes" in q:
                entries = [e for e in entries if e[0] >= now_offset - float(q["minutes"]) * 60 * self.speed]
            after = int(q.get("next", 0))
            entries = [e for e in entries if e[1] > after]
            limit = int(q.get("limit", 1000))
            page = entries[:limit]
            return 200, {"items": [self.item(e, key, now_offset) for e in page], "count": len(page),
                         "next": str(page[-1][1]) if len(entries) > limit else None, "window": {}}
        return 404, {"error": f"{path} はありません"}


class DemoBackend:
    """app_server.py の backend（AWS の代わりに手元で動く）。"""

    mode = "demo"

    def __init__(self, speed=60.0):
        store, _ = kgquery.open_store(EXAMPLE_INSTANCES)
        body = store.dump(format=RdfFormat.N_QUADS)
        self.store = store
        cache = graphstore.GraphCache("demo", "kg/current.nq", 3600,
                                      fetch=lambda etag: None if etag == '"demo"' else (body, '"demo"', "demo"))
        self.replay = DemoReplay(speed=speed)
        self.domain = Domain(cache, self.replay)
        import api  # factory-kg の REST のルーティング（dispatch）を、この domain で使う
        api.domain = self.domain
        self.api = api

    def rest(self, path, params):
        status, body = self.api.dispatch(path, params)
        return status, json.dumps(body, ensure_ascii=False).encode(), "application/json; charset=utf-8"

    def sparql(self, query, accept):
        try:
            result = self.store.query(query, use_default_graph_as_union=True)
        except SyntaxError as e:
            return 400, json.dumps({"error": f"SPARQL の構文エラー: {e}"}, ensure_ascii=False).encode(), "application/json"
        return 200, result.serialize(format=QueryResultsFormat.JSON), "application/sparql-results+json"

    def describe(self):
        return {"mode": "demo", "kg": "手元のグラフ（examples/instances.sample.ttl）",
                "replay": f"サンプル CSV のデモ再生（{self.replay.speed:g} 倍速・1 日でループ）"}
