"""工場ナレッジグラフの REST API（Lambda Function URL、IAM 認証）。仕様は aws/openapi.yaml。

    GET /v1/factory                                              工場 → サイト → エリア → ライン → 設備 の木
    GET /v1/equipment?line=&category=                            設備の一覧（稼働状態つき）
    GET /v1/equipment/{id}                                       設備の詳細（計測項目と最新値）
    GET /v1/equipment/{id}/properties/{metric}                   計測項目の詳細と最新値
    GET /v1/equipment/{id}/properties/{metric}/readings          時系列（?minutes= / ?source_from=&source_to= / ?limit=&next=）
    GET /v1/equipment/{id}/properties/{metric}/readings/summary  時系列の要約（件数・最小・最大・平均・状態ごとの時間）
    GET /v1/codes/{key}                                          コード表
    GET /v1/search?q=                                            設備・計測項目の検索
    GET /v1/status                                               グラフと replay の状態

構造はナレッジグラフ（S3 の kg/current.nq）から、値はグラフに書かれた replay の API から取る（domain.py）。
"""

import json
import os
import re
import urllib.error
import urllib.request

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

from domain import BadRequest, Domain, NotFound
from graphstore import GraphCache, NotPublished

BUCKET = os.environ.get("KG_BUCKET", "")
KEY = os.environ.get("KG_KEY", "kg/current.nq")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "30"))
VERSION = "v1"

_session = None


def call_replay(url):
    """replay の API を、この Lambda の実行ロールで SigV4 署名して呼ぶ。"""
    global _session
    _session = _session or boto3.Session()
    req = AWSRequest(method="GET", url=url)
    SigV4Auth(_session.get_credentials().get_frozen_credentials(), "lambda", _session.region_name).add_auth(req)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=dict(req.headers)), timeout=20) as res:
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except ValueError:
            return e.code, {"error": e.reason}


cache = GraphCache(BUCKET, KEY, CHECK_INTERVAL)
domain = Domain(cache, call_replay)

ID = r"([A-Za-z0-9_-]{1,32})"
ROUTES = [
    (re.compile(r"^/v1/factory$"), lambda q: domain.factory()),
    (re.compile(r"^/v1/equipment$"), lambda q: domain.list_equipment(q.get("line"), q.get("category"))),
    (re.compile(rf"^/v1/equipment/{ID}$"), lambda q, e: domain.equipment_detail(e)),
    (re.compile(rf"^/v1/equipment/{ID}/properties/{ID}$"), lambda q, e, m: domain.property_detail(e, m)),
    (re.compile(rf"^/v1/equipment/{ID}/properties/{ID}/readings$"), lambda q, e, m: domain.readings(e, m, q)),
    (re.compile(rf"^/v1/equipment/{ID}/properties/{ID}/readings/summary$"),
     lambda q, e, m: domain.readings_summary(e, m, q)),
    (re.compile(rf"^/v1/codes/{ID}$"), lambda q, k: domain.codes(k)),
    (re.compile(r"^/v1/search$"), lambda q: domain.search(q.get("q"))),
    (re.compile(r"^/v1/status$"), lambda q: domain.status()),
]


def respond(code, body):
    return {"statusCode": code, "headers": {"Content-Type": "application/json; charset=utf-8"},
            "body": json.dumps(body, ensure_ascii=False)}


def handler(event, context):
    method = event["requestContext"]["http"]["method"]
    path = event.get("rawPath", "/").rstrip("/") or "/"
    if method != "GET":
        return respond(405, {"error": "GET だけを受け付けます（読み取り専用）"})
    query = event.get("queryStringParameters") or {}
    for pattern, route in ROUTES:
        match = pattern.match(path)
        if match:
            try:
                return respond(200, route(query, *match.groups()))
            except NotFound as e:
                return respond(404, {"error": str(e)})
            except BadRequest as e:
                return respond(400, {"error": str(e)})
            except NotPublished:
                return respond(503, {"error": "グラフが未公開です。publish_ttl.py を実行してください"})
    return respond(404, {"error": f"{path} はありません", "openapi": "aws/openapi.yaml を参照"})
