"""REST API（と MCP）の共通の処理（ドメイン層）。ナレッジグラフの構造と replay の値を 1 つにまとめる。

- 構造: グラフ（Oxigraph）に決まった SPARQL を投げ、工場・ライン・設備・計測項目のモデルを作る（グラフの版ごとに 1 回）
- 値:   グラフに書かれた replay の API（xf:latestEndpoint、xf:readingsTemplate）を呼ぶ。
        グラフの dcat:endpointURL で始まる latest / readings / status 以外は呼ばない
- replay が無い・DB 再開中のときも構造は返し、値の部分を {"unavailable": 理由} にする（全体を失敗にしない）
"""

import re
import statistics
import threading
import time
from urllib.parse import urlencode, urlsplit

XF = "https://xcp-factory.example/def#"
PREFIXES = {
    "xf": XF, "s4inma": "https://saref.etsi.org/saref4inma/", "saref": "https://saref.etsi.org/core/",
    "sosa": "http://www.w3.org/ns/sosa/", "ssn": "http://www.w3.org/ns/ssn/", "qudt": "http://qudt.org/schema/qudt/",
    "unit": "http://qudt.org/vocab/unit/", "dcat": "http://www.w3.org/ns/dcat#", "hydra": "http://www.w3.org/ns/hydra/core#",
    "skos": "http://www.w3.org/2004/02/skos/core#", "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "id": "https://xcp-factory.example/id/",
}
PREFIX_HEADER = "".join(f"PREFIX {p}: <{ns}>\n" for p, ns in PREFIXES.items())

ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
REPLAY_PATHS = ("latest", "readings", "status")
LATEST_CACHE_SECONDS = 15
MAX_LIMIT = 5000
SUMMARY_MAX_PAGES = 10
SUMMARY_POINTS = 60
READING_PARAMS = ("minutes", "from", "to", "source_from", "source_to", "limit", "next")

Q_NODES = """
SELECT ?node ?kind ?label ?labelEn ?code ?parent WHERE {
  { ?node a s4inma:Factory BIND("factory" AS ?kind) }
  UNION { ?node a s4inma:Site BIND("site" AS ?kind) }
  UNION { ?node a s4inma:Area BIND("area" AS ?kind) }
  UNION { ?node a s4inma:WorkCenter ; xf:lineId ?code BIND("line" AS ?kind) }
  UNION { ?node xf:equipmentId ?code BIND("equipment" AS ?kind) }
  OPTIONAL { ?node rdfs:label ?label FILTER (LANG(?label) = "ja") }
  OPTIONAL { ?node rdfs:label ?labelEn FILTER (LANG(?labelEn) = "en") }
  OPTIONAL { ?parent saref:consistsOf ?node }
}"""
Q_EQUIPMENT = """
SELECT ?node ?type ?category ?categoryLabel ?categoryNotation WHERE {
  ?node xf:equipmentId ?id ; a ?type .
  OPTIONAL { ?node s4inma:belongsToCategory ?category .
             ?category skos:prefLabel ?categoryLabel FILTER (LANG(?categoryLabel) = "ja")
             OPTIONAL { ?category skos:notation ?categoryNotation } }
}"""
Q_PROPERTIES = """
SELECT ?prop ?equipment ?metric ?label ?labelEn ?comment ?unit ?unitLabel ?symbol ?latest ?readings ?service ?codes WHERE {
  ?prop a sosa:ObservableProperty ; ssn:isPropertyOf ?equipment ; xf:metricName ?metric ; qudt:hasUnit ?unit ;
        xf:apiService ?service ; xf:latestEndpoint ?latest ; xf:readingsEndpoint ?readings .
  OPTIONAL { ?prop rdfs:label ?label FILTER (LANG(?label) = "ja") }
  OPTIONAL { ?prop rdfs:label ?labelEn FILTER (LANG(?labelEn) = "en") }
  OPTIONAL { ?prop rdfs:comment ?comment }
  OPTIONAL { ?unit rdfs:label ?unitLabel FILTER (LANG(?unitLabel) = "ja") }
  OPTIONAL { ?unit qudt:symbol ?symbol }
  OPTIONAL { ?prop xf:valueCodeList ?codes }
}"""
Q_SERVICES = """
SELECT ?service ?base ?latestTemplate ?readingsTemplate WHERE {
  ?service a dcat:DataService ; dcat:endpointURL ?base ;
           xf:latestTemplate/hydra:template ?latestTemplate ; xf:readingsTemplate/hydra:template ?readingsTemplate .
}"""
Q_CODES = """
SELECT ?scheme ?schemeLabel ?notation ?label ?definition WHERE {
  ?scheme a skos:ConceptScheme .
  OPTIONAL { ?scheme skos:prefLabel ?schemeLabel FILTER (LANG(?schemeLabel) = "ja") }
  OPTIONAL { ?code skos:inScheme ?scheme ; skos:notation ?notation ; skos:prefLabel ?label FILTER (LANG(?label) = "ja")
             OPTIONAL { ?code skos:definition ?definition FILTER (LANG(?definition) = "ja") } }
}"""


class NotFound(Exception):
    pass


class BadRequest(Exception):
    pass


def shorten(iri):
    for prefix, ns in PREFIXES.items():
        if iri.startswith(ns):
            return f"{prefix}:{iri[len(ns):]}"
    return iri


def last_segment(iri):
    return iri.rstrip("/").rsplit("/", 1)[-1]


def rows(store, query):
    result = store.query(PREFIX_HEADER + query, use_default_graph_as_union=True)
    names = [v.value for v in result.variables]
    out = []
    for solution in result:
        out.append({n: (solution[n].value if solution[n] is not None else None) for n in names})
    return out


def as_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    return int(number) if number.is_integer() else number


class Model:
    """グラフから作る、工場の構造のモデル（グラフの版ごとに作り直す）。"""

    def __init__(self, store):
        self.nodes, self.equipment, self.lines, self.properties = {}, {}, {}, {}
        for r in rows(store, Q_NODES):
            node = self.nodes.setdefault(r["node"], {"iri": r["node"], "kind": r["kind"]})
            node.update({k: v for k, v in (("label", r["label"]), ("label_en", r["labelEn"]), ("id", r["code"]),
                                          ("parent", r["parent"])) if v})
        for node in self.nodes.values():
            if node["kind"] == "equipment":
                node.update(types=set(), category=None, properties={})
                self.equipment[node["id"]] = node
            elif node["kind"] == "line":
                self.lines[node["id"]] = node
        by_iri = {e["iri"]: e for e in self.equipment.values()}
        for r in rows(store, Q_EQUIPMENT):
            e = by_iri[r["node"]]
            e["types"].add(shorten(r["type"]))
            if r["category"]:
                e["category"] = {"key": last_segment(r["category"]), "label": r["categoryLabel"],
                                 "notation": r["categoryNotation"]}
        self.code_lists = {}
        for r in rows(store, Q_CODES):
            scheme = self.code_lists.setdefault(r["scheme"], {"key": last_segment(r["scheme"]), "@id": r["scheme"],
                                                              "label": r["schemeLabel"], "codes": {}})
            if r["notation"] is not None:
                scheme["codes"][r["notation"]] = {"value": as_number(r["notation"]), "label": r["label"],
                                                  **({"definition": r["definition"]} if r["definition"] else {})}
        self.services = {r["service"]: r for r in rows(store, Q_SERVICES)}
        for r in rows(store, Q_PROPERTIES):
            e = by_iri.get(r["equipment"])
            if e is None or r["metric"] in e["properties"]:
                continue
            prop = {"iri": r["prop"], "metric": r["metric"], "label": r["label"], "label_en": r["labelEn"],
                    "description": r["comment"], "equipment": e["id"],
                    # 単位なし（unit:UNITLESS）は QUDT の記号「一」を返さず null にする（JSON では紛らわしいため）
                    "unit": {"@id": r["unit"], "id": shorten(r["unit"]), "label": r["unitLabel"],
                             "symbol": None if r["unit"].endswith("/UNITLESS") else r["symbol"]},
                    "latest_url": r["latest"], "readings_url": r["readings"], "service": r["service"],
                    "code_list": r["codes"]}
            e["properties"][r["metric"]] = prop
            self.properties[prop["iri"]] = prop
        for e in self.equipment.values():
            line = self.nodes.get(e.get("parent"))
            e["line"] = line["id"] if line and line["kind"] == "line" else None

    def get_equipment(self, equipment_id):
        if not ID_PATTERN.fullmatch(equipment_id or "") or equipment_id not in self.equipment:
            raise NotFound(f"設備 {equipment_id} はありません")
        return self.equipment[equipment_id]

    def get_property(self, equipment_id, metric):
        e = self.get_equipment(equipment_id)
        if metric not in e["properties"]:
            raise NotFound(f"設備 {equipment_id} に計測項目 {metric} はありません（{', '.join(sorted(e['properties']))}）")
        return e, e["properties"][metric]

    def state_property(self, equipment):
        """稼働状態を表す計測項目（コード表を持つ status）。なければ None。"""
        prop = equipment["properties"].get("status")
        return prop if prop and prop["code_list"] else None

    def decode(self, prop, value):
        """コード値なら {"value", "label"}、そうでなければ数値のまま。"""
        if prop["code_list"] and value is not None:
            codes = self.code_lists.get(prop["code_list"], {}).get("codes", {})
            key = str(int(value)) if isinstance(value, (int, float)) and float(value).is_integer() else str(value)
            if key in codes:
                return {"value": codes[key]["value"], "label": codes[key]["label"]}
        return value

    def replay_bases(self):
        return [s["base"] for s in self.services.values()]


class Domain:
    """REST の各エンドポイント（MCP のツール）の中身。call(url) は replay を署名して呼び、(status, dict) を返す関数。"""

    def __init__(self, cache, call, clock=time.monotonic):
        self.cache, self.call, self.clock = cache, call, clock
        self._model, self._model_etag = None, None
        self._latest = {}
        self._lock = threading.Lock()

    # ---------- 共通 ----------

    def model(self):
        store = self.cache.ensure()
        with self._lock:
            if self._model is None or self._model_etag != self.cache.etag:
                self._model, self._model_etag = Model(store), self.cache.etag
                self._latest.clear()
            return self._model

    def allowed(self, url):
        parts = urlsplit(url)
        for base in self.model().replay_bases():
            b = urlsplit(base)
            if parts.scheme == "https" and parts.netloc == b.netloc and parts.path.startswith(b.path) \
                    and parts.path[len(b.path):] in REPLAY_PATHS and ".." not in parts.path:
                return True
        return False

    def replay(self, url):
        """replay を呼ぶ。失敗しても例外にせず、(結果 or None, 理由 or None) を返す。"""
        if not self.allowed(url):
            return None, "グラフに書かれた replay の API ではありません"
        try:
            status, body = self.call(url)
        except Exception as e:  # 接続できない・replay の環境がない など
            return None, f"replay に接続できません: {e}"
        if status != 200:
            reason = body.get("error") if isinstance(body, dict) else body
            return None, f"replay が {status} を返しました: {reason}"
        return body, None

    def latest(self, prop):
        """最新値（短時間キャッシュ）。{"value", "ts", "source_ts"} か {"unavailable": 理由}。"""
        now = self.clock()
        hit = self._latest.get(prop["latest_url"])
        if hit and now - hit[0] < LATEST_CACHE_SECONDS:
            return hit[1]
        body, error = self.replay(prop["latest_url"])
        if error:
            return {"unavailable": error}
        if not body.get("items"):
            return {"unavailable": "replay にまだ値がありません"}
        item = body["items"][0]
        value = {"value": self.model().decode(prop, item["value"]), "ts": item["ts"], "source_ts": item["source_ts"]}
        self._latest[prop["latest_url"]] = (now, value)
        return value

    # ---------- 表現 ----------

    def equipment_summary(self, e, with_status=True):
        model = self.model()
        out = {"id": e["id"], "@id": e["iri"], "label": e.get("label"), "label_en": e.get("label_en"),
               "line": e["line"], "category": e["category"], "types": sorted(e["types"]),
               "metrics": sorted(e["properties"])}
        state = model.state_property(e)
        if with_status and state:
            out["status"] = self.latest(state)
        return out

    def property_view(self, e, prop, with_latest=True):
        model = self.model()
        out = {"metric": prop["metric"], "@id": prop["iri"], "label": prop["label"], "label_en": prop["label_en"],
               "description": prop["description"], "equipment": e["id"], "unit": prop["unit"],
               "endpoints": {"latest": prop["latest_url"], "readings": prop["readings_url"]}}
        if prop["code_list"]:
            codes = model.code_lists.get(prop["code_list"], {})
            out["codes"] = {"key": codes.get("key"), "label": codes.get("label"), "values": list(codes.get("codes", {}).values())}
        if with_latest:
            out["latest"] = self.latest(prop)
        return out

    # ---------- エンドポイント ----------

    def factory(self):
        model = self.model()

        def build(node):
            out = {"kind": node["kind"], "id": node.get("id"), "@id": node["iri"], "label": node.get("label")}
            if node["kind"] == "equipment":
                e = model.equipment[node["id"]]
                out.update(category=e["category"]["label"] if e["category"] else None, metrics=sorted(e["properties"]))
                return out
            children = [n for n in model.nodes.values() if n.get("parent") == node["iri"]]
            out["children"] = [build(c) for c in sorted(children, key=lambda n: (n.get("id") or "", n.get("label") or ""))]
            return out

        roots = [n for n in model.nodes.values() if n["kind"] == "factory"]
        return {"factories": [build(r) for r in roots]}

    def list_equipment(self, line=None, category=None):
        model = self.model()
        items = []
        for e in sorted(model.equipment.values(), key=lambda e: e["id"]):
            if line and e["line"] != line:
                continue
            if category and not (e["category"] and category in (e["category"]["key"], e["category"]["label"],
                                                                e["category"]["notation"])):
                continue
            items.append(self.equipment_summary(e))
        return {"items": items, "count": len(items)}

    def equipment_detail(self, equipment_id):
        model = self.model()
        e = model.get_equipment(equipment_id)
        out = self.equipment_summary(e, with_status=False)
        out["properties"] = [self.property_view(e, p) for _, p in sorted(e["properties"].items())]
        state = model.state_property(e)
        if state:
            out["status"] = next(p["latest"] for p in out["properties"] if p["metric"] == state["metric"])
        return out

    def property_detail(self, equipment_id, metric):
        e, prop = self.model().get_property(equipment_id, metric)
        return self.property_view(e, prop)

    def readings_url(self, prop, params):
        service = self.model().services.get(prop["service"])
        template = service["readingsTemplate"]
        names = re.search(r"\{\?([^}]+)\}", template).group(1).split(",")
        values = {"equipment": prop["equipment"], "metric": prop["metric"]}
        for k, v in params.items():
            if k not in READING_PARAMS or k not in names:
                raise BadRequest(f"{k} は指定できません（使えるもの: {', '.join(p for p in READING_PARAMS if p in names)}）")
            values[k] = v
        query = urlencode({n: values[n] for n in names if values.get(n) not in (None, "")})
        return service["base"] + template[:template.index("{")] + ("?" + query if query else "")

    def readings(self, equipment_id, metric, params):
        model = self.model()
        e, prop = model.get_property(equipment_id, metric)
        params = {k: v for k, v in params.items() if k != "summary"}
        if "limit" in params:
            try:
                if not 1 <= int(params["limit"]) <= MAX_LIMIT:
                    raise ValueError
            except ValueError:
                raise BadRequest(f"limit は 1〜{MAX_LIMIT} で指定してください") from None
        body, error = self.replay(self.readings_url(prop, params))
        base = {"equipment": e["id"], "metric": metric, "label": prop["label"], "unit": prop["unit"]}
        if error:
            return {**base, "unavailable": error}
        items = [{"ts": i["ts"], "source_ts": i["source_ts"], "value": model.decode(prop, i["value"])} for i in body["items"]]
        return {**base, "items": items, "count": len(items), "next": body.get("next"), "window": body.get("window")}

    def readings_summary(self, equipment_id, metric, params):
        """時系列の要約（件数・最小・最大・平均、コード値なら状態ごとの時間、間引いた点）。MCP 向け。"""
        model = self.model()
        e, prop = model.get_property(equipment_id, metric)
        params = {k: v for k, v in params.items() if k not in ("summary", "next", "limit")}
        items, next_cursor, pages = [], None, 0
        while pages < SUMMARY_MAX_PAGES:
            page_params = {**params, "limit": str(MAX_LIMIT), **({"next": next_cursor} if next_cursor else {})}
            body, error = self.replay(self.readings_url(prop, page_params))
            if error:
                return {"equipment": e["id"], "metric": metric, "unavailable": error}
            items.extend(body["items"])
            next_cursor, pages = body.get("next"), pages + 1
            if not next_cursor:
                break
        out = {"equipment": e["id"], "metric": metric, "label": prop["label"], "unit": prop["unit"],
               "count": len(items), "truncated": bool(next_cursor)}
        if not items:
            return out
        values = [i["value"] for i in items]
        out.update(first={"source_ts": items[0]["source_ts"], "value": model.decode(prop, values[0])},
                   last={"source_ts": items[-1]["source_ts"], "value": model.decode(prop, values[-1])})
        if prop["code_list"]:
            step = self._step_minutes(items)
            states = {}
            for v in values:
                key = model.decode(prop, v)
                label = key["label"] if isinstance(key, dict) else str(key)
                entry = states.setdefault(label, {"value": key["value"] if isinstance(key, dict) else key, "label": label, "count": 0})
                entry["count"] += 1
            for entry in states.values():
                entry["minutes"] = round(entry["count"] * step)
            out["states"] = sorted(states.values(), key=lambda s: str(s["value"]))
            out["changes"] = self._changes(items, prop)
        else:
            out.update(min=min(values), max=max(values), avg=round(statistics.fmean(values), 3))
            peak = max(items, key=lambda i: i["value"])
            out["max_at"] = peak["source_ts"]
        stride = max(1, -(-len(items) // SUMMARY_POINTS))  # 切り上げ（SUMMARY_POINTS 点以下にする）
        out["points"] = [{"source_ts": i["source_ts"], "value": i["value"]} for i in items[::stride]]
        return out

    @staticmethod
    def _step_minutes(items):
        if len(items) < 2:
            return 0
        from datetime import datetime
        first, last = (datetime.fromisoformat(items[k]["source_ts"]) for k in (0, -1))
        return (last - first).total_seconds() / 60 / (len(items) - 1)

    def _changes(self, items, prop, limit=50):
        """コード値が変わった時刻（例: 稼働 → 異常停止）。"""
        model, out, previous = self.model(), [], None
        for i in items:
            if i["value"] != previous:
                out.append({"source_ts": i["source_ts"], "value": model.decode(prop, i["value"])})
                previous = i["value"]
        return out[:limit]

    def codes(self, key):
        for scheme in self.model().code_lists.values():
            if scheme["key"] == key:
                return {"key": key, "@id": scheme["@id"], "label": scheme["label"], "values": list(scheme["codes"].values())}
        raise NotFound(f"コード表 {key} はありません（{', '.join(s['key'] for s in self.model().code_lists.values())}）")

    def search(self, q):
        q = (q or "").strip().lower()
        if not q:
            raise BadRequest("q を指定してください")
        model, equipment, properties = self.model(), [], []
        for e in sorted(model.equipment.values(), key=lambda e: e["id"]):
            text = " ".join(filter(None, [e["id"], e.get("label"), e.get("label_en"),
                                          e["category"]["label"] if e["category"] else None])).lower()
            if q in text:
                equipment.append(self.equipment_summary(e, with_status=False))
            for metric, p in sorted(e["properties"].items()):
                if q in " ".join(filter(None, [metric, p["label"], p["label_en"], p["description"]])).lower():
                    properties.append({"equipment": e["id"], "metric": metric, "label": p["label"], "unit": p["unit"]["symbol"]})
        return {"q": q, "equipment": equipment, "properties": properties}

    def status(self):
        model = self.model()
        store = self.cache.ensure()
        out = {"graph": {"triples": len(store), **self.cache.info()},
               "counts": {"lines": len(model.lines), "equipment": len(model.equipment), "properties": len(model.properties)}}
        bases = model.replay_bases()
        if bases:
            body, error = self.replay(bases[0] + "status")
            out["replay"] = {"unavailable": error} if error else body
        return out
