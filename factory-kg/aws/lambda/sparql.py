"""ナレッジグラフの SPARQL エンドポイント（Lambda Function URL、IAM 認証）。

    GET  /sparql?query=...                       SPARQL 1.1 Query（SELECT / ASK / CONSTRUCT / DESCRIBE）
    POST /sparql  （本文 application/sparql-query、または application/x-www-form-urlencoded の query=）
    GET  /status                                 読み込み済みのグラフの情報（?reload=1 で S3 から読み直す）

S3 の kg/current.nq（publish_ttl.py が SHACL 検証後に上げる N-Quads。名前付きグラフ入り）を
Oxigraph のメモリ上ストアに読み込む。ETag を CHECK_INTERVAL 秒ごとに確認し、変わっていれば読み直す。
更新（SPARQL Update）は受け付けない（グラフの更新は TTL の差し替えで行う。決定事項 4）。
"""

import base64
import json
import os
import time
from urllib.parse import parse_qs

import boto3
from botocore.exceptions import ClientError
from pyoxigraph import NamedNode, QueryBoolean, QueryResultsFormat, QuerySolutions, RdfFormat, Store

BUCKET = os.environ.get("KG_BUCKET", "")
KEY = os.environ.get("KG_KEY", "kg/current.nq")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "30"))
MAX_RESPONSE_BYTES = 5_500_000  # Function URL の応答上限（6 MB）より少し小さく

SOLUTION_FORMATS = {
    "application/sparql-results+json": QueryResultsFormat.JSON,
    "application/json": QueryResultsFormat.JSON,
    "application/sparql-results+xml": QueryResultsFormat.XML,
    "text/csv": QueryResultsFormat.CSV,
    "text/tab-separated-values": QueryResultsFormat.TSV,
}
GRAPH_FORMATS = {"text/turtle": RdfFormat.TURTLE, "application/n-triples": RdfFormat.N_TRIPLES}

_s3 = None
state = {"store": None, "etag": None, "checked": 0.0, "loaded_at": None, "load_ms": None, "bytes": 0, "version": None}


class NotPublished(Exception):
    pass


def fetch_graph(etag):
    """S3 から N-Quads を取る。変わっていなければ None。(本文, ETag, バージョン) を返す。"""
    global _s3
    _s3 = _s3 or boto3.client("s3")
    try:
        head = _s3.head_object(Bucket=BUCKET, Key=KEY)
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            raise NotPublished() from None
        raise
    if head["ETag"] == etag:
        return None
    obj = _s3.get_object(Bucket=BUCKET, Key=KEY, IfMatch=head["ETag"])
    return obj["Body"].read(), head["ETag"], head.get("VersionId")


def ensure_loaded(force=False):
    now = time.monotonic()
    if not force and state["store"] is not None and now - state["checked"] < CHECK_INTERVAL:
        return
    state["checked"] = now
    fetched = fetch_graph(None if force else state["etag"])
    if fetched is None:
        return
    body, etag, version = fetched
    started = time.perf_counter()
    store = Store()  # メモリ上
    store.load(body, format=RdfFormat.N_QUADS)
    state.update(store=store, etag=etag, version=version, bytes=len(body),
                 load_ms=round((time.perf_counter() - started) * 1000, 1),
                 loaded_at=time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()))
    log(msg="グラフを読み込み", triples=len(store), load_ms=state["load_ms"], bytes=len(body), etag=etag)


def log(**fields):
    print(json.dumps(fields, ensure_ascii=False))


def respond(code, body, content_type="application/json; charset=utf-8", headers=None):
    if isinstance(body, (dict, list)):
        body = json.dumps(body, ensure_ascii=False)
    data = body.encode() if isinstance(body, str) else body
    if len(data) > MAX_RESPONSE_BYTES:
        return respond(413, {"error": f"結果が大きすぎます（{len(data):,} バイト）。LIMIT を付けて絞り込んでください"})
    return {"statusCode": code, "headers": {"Content-Type": content_type, **(headers or {})},
            "body": base64.b64encode(data).decode(), "isBase64Encoded": True}


def read_query(event):
    method = event["requestContext"]["http"]["method"]
    params = event.get("queryStringParameters") or {}
    if method == "GET":
        return params.get("query")
    raw = event.get("body") or ""
    raw = base64.b64decode(raw).decode() if event.get("isBase64Encoded") else raw
    content_type = {k.lower(): v for k, v in (event.get("headers") or {}).items()}.get("content-type", "")
    if content_type.startswith("application/sparql-update"):
        raise PermissionError()
    if content_type.startswith("application/sparql-query"):
        return raw
    if content_type.startswith("application/x-www-form-urlencoded"):
        form = parse_qs(raw)
        if "update" in form:  # SPARQL 1.1 Protocol の更新の形式
            raise PermissionError()
        values = form.get("query")
        return values[0] if values else None
    return raw or params.get("query")


def choose(accept, formats, default):
    for part in (accept or "").split(","):
        media = part.split(";")[0].strip().lower()
        if media in formats:
            return media, formats[media]
    return default, formats[default]


def run_query(event):
    try:
        sparql = read_query(event)
    except PermissionError:
        return respond(403, {"error": "更新（SPARQL Update）は受け付けません。グラフは TTL を publish して更新します"})
    if not sparql or not sparql.strip():
        return respond(400, {"error": "query がありません（GET は ?query=、POST は本文に SPARQL）"})
    ensure_loaded()
    accept = {k.lower(): v for k, v in (event.get("headers") or {}).items()}.get("accept", "")
    started = time.perf_counter()
    try:
        result = state["store"].query(sparql, use_default_graph_as_union=True)
        if isinstance(result, (QuerySolutions, QueryBoolean)):
            media, fmt = choose(accept, SOLUTION_FORMATS, "application/sparql-results+json")
            body = result.serialize(format=fmt)
        else:
            media, fmt = choose(accept, GRAPH_FORMATS, "text/turtle")
            body = result.serialize(format=fmt)
    except SyntaxError as e:
        message = str(e)
        if any(word in sparql.upper() for word in ("INSERT", "DELETE", "LOAD", "CLEAR", "DROP", "CREATE")):
            message += "（更新は受け付けません）"
        return respond(400, {"error": f"SPARQL の構文エラー: {message}"})
    log(msg="query", ms=round((time.perf_counter() - started) * 1000, 1), bytes=len(body))
    return respond(200, body, f"{media}; charset=utf-8")


def get_status(event):
    params = event.get("queryStringParameters") or {}
    ensure_loaded(force=params.get("reload") == "1")
    store = state["store"]
    graphs = sorted(str(g.value) for g in store.named_graphs())
    return respond(200, {
        "triples": len(store),
        "graphs": {g: sum(1 for _ in store.quads_for_pattern(None, None, None, NamedNode(g))) for g in graphs},
        "source": f"s3://{BUCKET}/{KEY}",
        "etag": state["etag"],
        "version_id": state["version"],
        "bytes": state["bytes"],
        "load_ms": state["load_ms"],
        "loaded_at": state["loaded_at"],
        "check_interval_seconds": CHECK_INTERVAL,
    })


ROUTES = {("GET", "/sparql"): run_query, ("POST", "/sparql"): run_query, ("GET", "/status"): get_status}


def handler(event, context):
    method = event["requestContext"]["http"]["method"]
    path = event.get("rawPath", "/").rstrip("/") or "/"
    route = ROUTES.get((method, path))
    if route is None:
        return respond(404, {"error": f"{method} {path} はありません", "endpoints": [f"{m} {p}" for m, p in ROUTES]})
    try:
        return route(event)
    except NotPublished:
        return respond(503, {"error": f"グラフが未公開です（s3://{BUCKET}/{KEY} がありません）。publish_ttl.py を実行してください"})
