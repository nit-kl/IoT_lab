"""S3 に届いた差分でナレッジグラフを更新する。

SQS のメッセージは「新着あり」の合図として扱い、未処理のファイルをキーの順に全部処理する。
処理位置はグラフのファイルのメタデータに持つので、グラフと処理位置は必ず同時に更新される。
読めないファイルは kg/rejected/ に記録して飛ばす。処理位置は順に進むので、飛ばさないと以後の更新がすべて止まる。
処理位置は読めたファイルでだけ進める。読めないファイルの名前で進めると、それより前に並ぶ正常なファイルを読み飛ばす。
S3 の障害などの失敗は例外で SQS に戻り、数回失敗すると DLQ へ移る。
"""

import gzip
import json
import os

import boto3
from botocore.exceptions import ClientError

from kg.update import apply_tables, dump_store, load_store, parse_batch, prune_runs

BUCKET = os.environ.get("BUCKET", "")
CURRENT_KEY = "kg/current.nq.gz"
RAW_PREFIX = "raw/"
REJECTED_PREFIX = "kg/rejected/"
LAST_KEY = "last-key"
UNREADABLE = (OSError, EOFError, gzip.BadGzipFile, UnicodeDecodeError, json.JSONDecodeError, IndexError, KeyError, AttributeError)
s3 = boto3.client("s3")


def pending_keys(client, bucket, after):
    keys = []
    args = {"Bucket": bucket, "Prefix": RAW_PREFIX}
    if after:
        args["StartAfter"] = after
    for page in client.get_paginator("list_objects_v2").paginate(**args):
        keys += [o["Key"] for o in page.get("Contents", []) if o["Key"].endswith(".jsonl.gz")]
    return sorted(keys)


def read_current(client, bucket):
    """(グラフの本文, 処理済みのキー, ETag) を返す。まだなければ (None, None, None)。"""
    try:
        response = client.get_object(Bucket=bucket, Key=CURRENT_KEY)
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return None, None, None
        raise
    return response["Body"].read(), response["Metadata"].get(LAST_KEY), response["ETag"]


def write_current(client, bucket, body, last_key, etag):
    args = {"IfMatch": etag} if etag else {"IfNoneMatch": "*"}  # ほかの実行と競合したら失敗させる
    client.put_object(Bucket=bucket, Key=CURRENT_KEY, Body=body, ContentType="application/gzip",
                      Metadata={LAST_KEY: last_key}, **args)


def reject(client, bucket, key, error):
    note = json.dumps({"key": key, "error": repr(error)[:300]}, ensure_ascii=False).encode()
    client.put_object(Bucket=bucket, Key=REJECTED_PREFIX + key.rsplit("/", 1)[-1] + ".json", Body=note,
                      ContentType="application/json", Metadata={})
    print(json.dumps({"event": "batch_rejected", "key": key, "error": repr(error)[:300]}, ensure_ascii=False))


def process(client, bucket):
    body, last_key, etag = read_current(client, bucket)
    keys = pending_keys(client, bucket, last_key)
    if not keys:
        return {"batches": 0}
    store = load_store(body)
    totals = {"batches": 0, "converted": 0, "deleted": 0, "quarantined": 0, "issues": 0, "failed": 0, "rejected": 0}
    newest = None
    for key in keys:
        data = client.get_object(Bucket=bucket, Key=key)["Body"].read()
        try:
            header, tables = parse_batch(data)
        except UNREADABLE as e:
            reject(client, bucket, key, e)
            totals["rejected"] += 1
            continue
        if header.get("full"):
            store = load_store()  # 全件の送り直しなので作り直す。消えた行を残さないため
        stats = apply_tables(store, tables)
        newest = key
        totals["batches"] += 1
        for name, count in stats.items():
            totals[name] += count
    if newest is None:
        return totals
    totals["pruned"] = prune_runs(store)
    totals["quads"] = len(store)
    write_current(client, bucket, dump_store(store), newest, etag)
    totals["last_key"] = newest
    return totals


def handler(event, context):
    result = process(s3, BUCKET)
    print(json.dumps({"event": "graph_updated", **result}, ensure_ascii=False))
    return result
