"""SPARQL Lambda（aws/lambda/sparql.py）のテスト。S3 だけを差し替え、それ以外は本物の Oxigraph で動かす。

    python -m unittest discover -s tests -v
"""

import base64
import json
import sys
import unittest
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "aws" / "lambda"))

import graphstore  # noqa: E402
import publish_ttl  # noqa: E402
import sparql  # noqa: E402
from kgcommon import EXAMPLE_INSTANCES  # noqa: E402
from query import PREFIX_HEADER  # noqa: E402

NQ, TRIPLES = publish_ttl.build_nquads(EXAMPLE_INSTANCES)


def event(method, path, query=None, body=None, headers=None, b64=False):
    if b64 and body is not None:
        body = base64.b64encode(body.encode()).decode()
    return {"requestContext": {"http": {"method": method}}, "rawPath": path,
            "queryStringParameters": query, "body": body, "headers": headers or {}, "isBase64Encoded": b64}


def call(ev):
    res = sparql.handler(ev, None)
    body = base64.b64decode(res["body"]).decode()
    return res["statusCode"], res["headers"]["Content-Type"], body


class FakeS3:
    """fetch_graph の代わり。etag を変えると「S3 が更新された」ことになる。"""

    def __init__(self, body=NQ, etag='"v1"'):
        self.body, self.etag, self.calls = body, etag, 0

    def __call__(self, current_etag):
        self.calls += 1
        if self.body is None:
            raise graphstore.NotPublished()
        if current_etag == self.etag:
            return None
        return self.body, self.etag, "version-" + self.etag.strip('"')


class SparqlLambdaTest(unittest.TestCase):
    def setUp(self):
        self.s3 = FakeS3()
        self._orig = sparql.cache
        sparql.cache = graphstore.GraphCache("test-bucket", "kg/current.nq", 30, fetch=self.s3)
        self.addCleanup(setattr, sparql, "cache", self._orig)

    def select(self, q, headers=None):
        return call(event("POST", "/sparql", body=PREFIX_HEADER + q,
                          headers=headers or {"Content-Type": "application/sparql-query"}))

    def test_post_select_json(self):
        code, ctype, body = self.select("SELECT ?e WHERE { ?x xf:equipmentId ?e } ORDER BY ?e")
        self.assertEqual(code, 200)
        self.assertIn("application/sparql-results+json", ctype)
        values = [b["e"]["value"] for b in json.loads(body)["results"]["bindings"]]
        self.assertEqual(values, ["CNC-01", "COMP-01", "CONV-01", "ENV-01", "PRESS-01"])

    def test_get_and_form_post_and_base64(self):
        q = PREFIX_HEADER + "SELECT (COUNT(*) AS ?n) WHERE { ?x xf:equipmentId ?e }"
        for ev in (event("GET", "/sparql", query={"query": q}),
                   event("POST", "/sparql", body=urlencode({"query": q}),
                         headers={"content-type": "application/x-www-form-urlencoded"}),
                   event("POST", "/sparql", body=q, headers={"Content-Type": "application/sparql-query"}, b64=True)):
            code, _, body = call(ev)
            self.assertEqual(code, 200, body)
            self.assertEqual(json.loads(body)["results"]["bindings"][0]["n"]["value"], "5")

    def test_union_of_named_graphs(self):
        # インスタンス（instances）の設備と、オントロジー（ontology）の分類ラベルを 1 つのクエリで結べる
        code, _, body = self.select(
            'SELECT ?l WHERE { ?x xf:equipmentId "CNC-01" ; s4inma:belongsToCategory/skos:prefLabel ?l FILTER(LANG(?l)="ja") }')
        self.assertEqual(json.loads(body)["results"]["bindings"][0]["l"]["value"], "CNC 加工機")

    def test_ask_and_construct_and_csv(self):
        code, _, body = self.select("ASK { ?x xf:equipmentId \"CNC-01\" }")
        self.assertEqual(json.loads(body)["boolean"], True)
        code, ctype, body = self.select("CONSTRUCT { ?x xf:equipmentId ?e } WHERE { ?x xf:equipmentId ?e }")
        self.assertEqual(code, 200)
        self.assertIn("text/turtle", ctype)
        self.assertIn("CNC-01", body)
        code, ctype, body = self.select("SELECT ?e WHERE { ?x xf:equipmentId ?e } ORDER BY ?e",
                                        headers={"Content-Type": "application/sparql-query", "Accept": "text/csv"})
        self.assertIn("text/csv", ctype)
        self.assertEqual(body.splitlines()[:2], ["e", "CNC-01"])

    def test_update_rejected(self):
        code, _, body = call(event("POST", "/sparql", body="update=INSERT+DATA+%7B%7D",
                                   headers={"Content-Type": "application/x-www-form-urlencoded"}))
        self.assertEqual(code, 403)
        code, _, body = call(event("POST", "/sparql", body="INSERT DATA { <a:b> <a:c> <a:d> }",
                                   headers={"Content-Type": "application/sparql-update"}))
        self.assertEqual(code, 403)
        code, _, body = self.select("INSERT DATA { <a:b> <a:c> <a:d> }")
        self.assertEqual(code, 400)
        self.assertIn("更新は受け付けません", json.loads(body)["error"])

    def test_bad_requests(self):
        code, _, body = self.select("SELEC * WHERE {}")
        self.assertEqual(code, 400)
        self.assertIn("構文エラー", json.loads(body)["error"])
        code, _, _ = call(event("GET", "/sparql", query={}))
        self.assertEqual(code, 400)
        code, _, _ = call(event("GET", "/nope"))
        self.assertEqual(code, 404)

    def test_not_published(self):
        self.s3.body = None
        code, _, body = call(event("GET", "/status"))
        self.assertEqual(code, 503)
        self.assertIn("publish_ttl.py", json.loads(body)["error"])

    def test_status(self):
        code, _, body = call(event("GET", "/status"))
        status = json.loads(body)
        self.assertEqual(code, 200)
        self.assertEqual(status["triples"], TRIPLES)
        self.assertEqual(set(status["graphs"]), {"https://xcp-factory.example/graph/ontology",
                                                 "https://xcp-factory.example/graph/imports",
                                                 "https://xcp-factory.example/graph/instances",
                                                 "https://xcp-factory.example/graph/layout"})
        self.assertEqual(sum(status["graphs"].values()), TRIPLES)
        self.assertEqual(status["etag"], '"v1"')

    def test_reload_when_etag_changes(self):
        self.select("ASK {}")
        self.assertEqual(sparql.cache.etag, '"v1"')
        # 確認間隔内は S3 を見に行かない
        calls = self.s3.calls
        self.select("ASK {}")
        self.assertEqual(self.s3.calls, calls)
        # S3 が更新された（ETag が変わった）→ 確認間隔を過ぎると読み直す
        smaller = b"<https://x.example/a> <https://x.example/b> <https://x.example/c> <https://x.example/g> .\n"
        self.s3.body, self.s3.etag = smaller, '"v2"'
        sparql.cache.checked -= sparql.cache.check_interval + 1
        _, _, body = call(event("GET", "/status"))
        self.assertEqual(json.loads(body)["triples"], 1)
        self.assertEqual(json.loads(body)["etag"], '"v2"')

    def test_reload_param_forces_fetch(self):
        call(event("GET", "/status"))
        calls = self.s3.calls
        call(event("GET", "/status", query={"reload": "1"}))
        self.assertEqual(self.s3.calls, calls + 1)


if __name__ == "__main__":
    unittest.main()
