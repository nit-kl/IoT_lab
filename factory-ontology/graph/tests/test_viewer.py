"""ビューアのサーバー。実際に 127.0.0.1 で起動して確かめる。"""

import http.client
import json
import sys
import threading
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

import pytest

GRAPH_DIR = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(GRAPH_DIR), str(GRAPH_DIR / "tools"), str(GRAPH_DIR.parent / "onprem" / "simulator")]

from generate import generate  # noqa: E402
from kg.common import ID  # noqa: E402
from kg.store import open_store, run  # noqa: E402
from kg.transform import convert_tables  # noqa: E402
from viewer_server import VIEWER_DIR, make_handler, static_path  # noqa: E402

HEADERS = {"X-Requested-By": "kg-viewer"}


@pytest.fixture(scope="module")
def server():
    store = open_store(quads=convert_tables(generate(datetime(2026, 11, 7, 12))[0]).quads)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store, 0))
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(store, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield port, store
    httpd.shutdown()


def call(port, method, path, body=None, headers=HEADERS, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.putrequest(method, path, skip_host=True)
    conn.putheader("Host", host or f"127.0.0.1:{port}")
    for k, v in headers.items():
        conn.putheader(k, v)
    data = body.encode() if body else b""
    conn.putheader("Content-Length", str(len(data)))
    conn.endheaders(data)
    res = conn.getresponse()
    return res.status, res.read(), dict(res.getheaders())


def get(port, path):
    status, body, _ = call(port, "GET", path)
    assert status == 200, body
    return json.loads(body)


def test_overview_is_the_plant_hierarchy(server):
    data = get(server[0], "/api/overview")
    kinds = {n["kind"] for n in data["nodes"]}
    assert {"Factory", "Area", "WorkCenter", "ProductionEquipment"} <= kinds
    assert all(n["label"] for n in data["nodes"])


def test_node_detail_expands_neighbors_and_truncates_busy_relations(server):
    detail = get(server[0], "/api/node?iri=" + quote(ID + "equipment/PRS-03", safe=""))
    assert "設備" in detail["types"]
    assert any(i["value"] == "PRESS3" for i in detail["literals"])
    assert any(n["iri"].endswith("model/T-PR500") for n in detail["nodes"])
    assert len(detail["nodes"]) < 80
    assert any(t["total"] > t["shown"] for t in detail["truncated"])
    # 件数の多い生産実績に埋もれず、少ない種類の関係も見える
    assert {"Alarm", "MaintenanceWork", "PartInstallation", "Stoppage", "ProductionRun"} <= {n["kind"] for n in detail["nodes"]}


@pytest.mark.parametrize("text", ["PRESS3", "E-0103", "PRS-03"])
def test_search_finds_equipment_by_any_id(server, text):
    results = get(server[0], f"/api/search?q={text}")
    assert any(r["iri"].endswith("equipment/PRS-03") for r in results)


def test_search_text_cannot_escape_the_query(server):
    assert get(server[0], "/api/search?q=" + quote('") } #')) == []


def test_named_query_runs(server):
    status, body, _ = call(server[0], "POST", "/api/query/cq6_find_by_any_id", json.dumps({"ID": "E-0103"}))
    assert status == 200 and json.loads(body)[0]["equipmentId"] == "PRS-03"


def test_summary_counts_nodes(server):
    assert next(i["count"] for i in get(server[0], "/api/summary") if i["name"] == "アラーム") == 36


def test_guards(server):
    port, store = server
    assert call(port, "GET", "/api/overview", headers={})[0] == 403
    assert call(port, "GET", "/api/overview", host="evil.example")[0] == 403
    assert call(port, "GET", "/", headers={}, host="evil.example")[0] == 403
    assert call(port, "GET", "/api/node?iri=http://evil/")[0] == 400
    assert call(port, "POST", "/api/query/..%2Fsecret", "{}")[0] == 404


def test_sparql_cannot_update(server):
    port, store = server
    before = run(store, "SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }")
    status, body, _ = call(port, "POST", "/api/sparql", "DELETE WHERE { ?s ?p ?o }")
    assert status == 400
    assert run(store, "SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }") == before


def test_static_files_stay_inside_viewer():
    assert static_path("/") == (VIEWER_DIR / "index.html").resolve()
    assert static_path("/../graph/kg/common.py") is None
    assert static_path("/%2e%2e/onprem/.env") is None
