"""ナレッジグラフのローカルビューア。build/current.nq を読み込んで画面(viewer/)を配信する。

    python tools/viewer_server.py        # http://127.0.0.1:8765 をブラウザで開く
    python tools/viewer_server.py --port 9000 --no-browser

ほかの Web サイトから悪用されないよう、待ち受けは 127.0.0.1 のみ、Host ヘッダーは localhost だけ、
API には独自ヘッダー(X-Requested-By: kg-viewer)を必須にしている。グラフの更新はできない。
"""

import argparse
import json
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]

from ask import example_params
from kg.common import ID, QUERIES_DIR, ROOT
from kg.store import load_query, open_store, query_params, query_title, run, run_json

VIEWER_DIR = ROOT.parent / "viewer"
REQUIRED_HEADER = ("X-Requested-By", "kg-viewer")
MAX_BODY = 100_000
NEIGHBOR_LIMIT = 6  # 関係と相手の型の組ごとの上限
HIDDEN_PREDICATES = "rdf:type, skos:inScheme"
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}

OVERVIEW = """
SELECT ?parent ?child WHERE {
  ?parent saref:consistsOf ?child .
  { ?parent a s4inma:Factory } UNION { ?parent a s4inma:Area } UNION { ?parent a s4inma:WorkCenter }
}"""
NODE_LITERALS = """
SELECT ?p ?pLabel ?o WHERE {
  <%(iri)s> ?p ?o . FILTER (isLiteral(?o))
  OPTIONAL { ?p rdfs:label ?pLabel }
} ORDER BY ?p"""
NODE_OUT = """
SELECT ?p ?pLabel ?o ?oLabel ?type ?typeLabel WHERE {
  <%(iri)s> ?p ?o . FILTER (isIRI(?o) && ?p NOT IN (%(hidden)s))
  OPTIONAL { ?o a ?type . OPTIONAL { ?type rdfs:label ?typeLabel } }
  OPTIONAL { ?p rdfs:label ?pLabel } OPTIONAL { ?o rdfs:label ?oLabel }
} ORDER BY ?p"""
NODE_IN = """
SELECT ?p ?pLabel ?s ?sLabel ?at ?type ?typeLabel WHERE {
  ?s ?p <%(iri)s> . FILTER (?p NOT IN (%(hidden)s))
  OPTIONAL { ?s a ?type . OPTIONAL { ?type rdfs:label ?typeLabel } }
  OPTIONAL { ?p rdfs:label ?pLabel } OPTIONAL { ?s rdfs:label ?sLabel } OPTIONAL { ?s prov:startedAtTime ?at }
} ORDER BY ?p DESC(?at)"""
NODE_TYPES = "SELECT ?type ?typeLabel WHERE { <%(iri)s> a ?type . OPTIONAL { ?type rdfs:label ?typeLabel } }"
TYPES_OF = "SELECT ?s ?type ?typeLabel WHERE { VALUES ?s { %s } ?s a ?type . OPTIONAL { ?type rdfs:label ?typeLabel } }"
SEARCH = """
SELECT DISTINCT ?s ?label WHERE {
  ?s (xf:equipmentId|xf:plcId|xf:cmmsId|xf:lotNumber|xf:orderNumber|xf:partId|xf:employeeId|rdfs:label) ?v .
  ?s rdfs:label ?label . FILTER (STRSTARTS(STR(?s), "%(id)s") && CONTAINS(LCASE(STR(?v)), LCASE(?q)))
} ORDER BY ?label LIMIT 20"""
SUMMARY = "SELECT ?type ?label (COUNT(DISTINCT ?s) AS ?n) WHERE { ?s a ?type . OPTIONAL { ?type rdfs:label ?label } FILTER (STRSTARTS(STR(?s), \"%s\")) } GROUP BY ?type ?label ORDER BY DESC(?n)"


def local_name(iri):
    return iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]


def short_label(iri, label):
    return label or iri[len(ID):] if iri.startswith(ID) else label or local_name(iri)


def is_local_host(header, port):
    return header in (f"127.0.0.1:{port}", f"localhost:{port}")


def static_path(request_path):
    path = urlsplit(request_path).path
    target = (VIEWER_DIR / ("index.html" if path == "/" else path.lstrip("/"))).resolve()
    if VIEWER_DIR.resolve() not in target.parents or not target.is_file():
        return None
    return target


def is_node_iri(iri):
    return iri.startswith(ID) and not any(c in iri for c in '<>"{}|\\^` ')


def attach_types(store, nodes):
    """ノードの一覧に型(kind / kindLabel)を付ける。"""
    if not nodes:
        return nodes
    rows = run(store, TYPES_OF % " ".join(f"<{n['iri']}>" for n in nodes))
    kinds = {r["s"]: (local_name(r["type"]), r["typeLabel"]) for r in rows}
    for node in nodes:
        node["kind"], node["kindLabel"] = kinds.get(node["iri"], ("Other", None))
    return nodes


def node_detail(store, iri):
    """1 つのノードの値と隣のノードを返す。

    隣が多いときは、関係と相手の型の組ごとに新しいものから一定数だけ返す。関係だけで区切ると、
    設備の xf:onEquipment のように件数の多い生産実績が枠を埋めて、アラームや保全が見えなくなる。
    """
    args = {"iri": iri, "hidden": HIDDEN_PREDICATES}
    literals = [{"name": r["pLabel"] or local_name(r["p"]), "value": r["o"]} for r in run(store, NODE_LITERALS % args)]
    types = run(store, NODE_TYPES % args)
    edges, hidden, nodes = [], {}, {}

    def add(direction, row, other_key, label_key):
        predicate = row["pLabel"] or local_name(row["p"])
        kind = row["typeLabel"] or (local_name(row["type"]) if row["type"] else "その他")
        key = (direction, row["p"], row["type"])
        hidden.setdefault(key, {"name": kind, "direction": direction, "total": 0})["total"] += 1
        if hidden[key]["total"] > NEIGHBOR_LIMIT:
            return
        other = row[other_key]
        if not other.startswith(ID):
            return
        nodes[other] = {"iri": other, "label": short_label(other, row[label_key])}
        edges.append({"source": iri if direction == "out" else other, "target": other if direction == "out" else iri, "label": predicate})

    for row in run(store, NODE_OUT % args):
        add("out", row, "o", "oLabel")
    for row in run(store, NODE_IN % args):
        add("in", row, "s", "sLabel")
    return {
        "iri": iri,
        "types": [t["typeLabel"] or local_name(t["type"]) for t in types],
        "literals": literals,
        "nodes": attach_types(store, list(nodes.values())),
        "edges": edges,
        "truncated": [{"name": h["name"], "direction": h["direction"], "total": h["total"], "shown": NEIGHBOR_LIMIT}
                      for h in hidden.values() if h["total"] > NEIGHBOR_LIMIT],
    }


def overview(store):
    rows = run(store, OVERVIEW)
    labels = {}
    for row in rows:
        labels[row["parent"]] = labels[row["child"]] = None
    named = run(store, "SELECT ?s ?l WHERE { VALUES ?s { %s } ?s rdfs:label ?l }" % " ".join(f"<{i}>" for i in labels))
    labels.update({r["s"]: r["l"] for r in named})
    nodes = attach_types(store, [{"iri": i, "label": short_label(i, l)} for i, l in labels.items()])
    return {"nodes": nodes, "edges": [{"source": r["parent"], "target": r["child"], "label": "構成"} for r in rows]}


def list_queries():
    try:
        examples = example_params()
    except FileNotFoundError:
        examples = {}
    out = []
    for path in sorted(QUERIES_DIR.glob("*.rq")):
        params = query_params(path.stem)
        out.append({"name": path.stem, "title": query_title(path.stem), "params": {p: examples.get((path.stem, p), examples.get(p, "")) for p in params}})
    return out


def make_handler(store, port):
    class Handler(BaseHTTPRequestHandler):
        server_version = "kg-viewer"

        def log_message(self, fmt, *args):
            sys.stderr.write(f"{self.command} {self.path[:100]} -> {args[1] if len(args) > 1 else ''}\n")

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
                             "img-src data:; connect-src 'self'")
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
            parts = urlsplit(self.path)
            if not parts.path.startswith("/api/"):
                if not self.guard(api=False):
                    return
                target = static_path(self.path)
                if target is None:
                    return self.send(404, {"error": "見つかりません"})
                return self.send(200, target.read_bytes(), CONTENT_TYPES.get(target.suffix, "application/octet-stream"))
            if not self.guard(api=True):
                return
            params = parse_qs(parts.query)
            try:
                if parts.path == "/api/overview":
                    return self.send(200, overview(store))
                if parts.path == "/api/summary":
                    return self.send(200, [{"name": r["label"] or local_name(r["type"]), "count": int(r["n"])} for r in run(store, SUMMARY % ID)])
                if parts.path == "/api/queries":
                    return self.send(200, list_queries())
                if parts.path == "/api/node":
                    iri = params.get("iri", [""])[0]
                    if not is_node_iri(iri):
                        return self.send(400, {"error": "ノードの指定が正しくありません"})
                    return self.send(200, node_detail(store, iri))
                if parts.path == "/api/search":
                    text = params.get("q", [""])[0][:60]
                    sparql = (SEARCH % {"id": ID}).replace("?q", json.dumps(text, ensure_ascii=False))
                    nodes = [{"iri": r["s"], "label": r["label"]} for r in run(store, sparql)]
                    return self.send(200, attach_types(store, nodes))
                self.send(404, {"error": "見つかりません"})
            except ValueError as e:
                self.send(400, {"error": str(e)})

        def do_POST(self):
            if not self.guard(api=True):
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self.send(413, {"error": "要求が大きすぎます"})
            body = self.rfile.read(length).decode()
            try:
                if self.path == "/api/sparql":
                    return self.send(200, run_json(store, body), "application/sparql-results+json")
                if self.path.startswith("/api/query/"):
                    name = self.path.rsplit("/", 1)[-1]
                    if not (QUERIES_DIR / f"{name}.rq").is_file() or not name.replace("_", "").isalnum():
                        return self.send(404, {"error": "問いが見つかりません"})
                    return self.send(200, run(store, load_query(name, **json.loads(body or "{}"))))
                self.send(404, {"error": "見つかりません"})
            except (ValueError, SyntaxError) as e:
                self.send(400, {"error": str(e)})

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    store = open_store()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(store, args.port))
    url = f"http://127.0.0.1:{args.port}"
    print(f"{url} で表示しています。Ctrl+C で止めます")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
