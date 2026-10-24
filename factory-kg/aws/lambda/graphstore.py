"""S3 の kg/current.nq を Oxigraph のメモリ上ストアに読み込み、更新されたら読み直す（sparql・api の両 Lambda で共通）。

ETag を check_interval 秒ごとに確認し、変わっていれば読み直す。読み込みは Lambda のインスタンスごとに 1 回で、
同じインスタンスでの以降の要求は読み込み済みのストアを使う。
"""

import json
import time

import boto3
from botocore.exceptions import ClientError
from pyoxigraph import RdfFormat, Store


class NotPublished(Exception):
    """グラフがまだ公開されていない（S3 に kg/current.nq がない）。"""


class GraphCache:
    def __init__(self, bucket, key, check_interval=30, fetch=None):
        self.bucket, self.key, self.check_interval = bucket, key, check_interval
        self.fetch = fetch or self._fetch_s3
        self.store = None
        self.etag = None
        self.version = None
        self.bytes = 0
        self.load_ms = None
        self.loaded_at = None
        self.checked = 0.0
        self._s3 = None

    def _fetch_s3(self, etag):
        """S3 から N-Quads を取る。etag と同じなら None。(本文, ETag, バージョン) を返す。"""
        self._s3 = self._s3 or boto3.client("s3")
        try:
            head = self._s3.head_object(Bucket=self.bucket, Key=self.key)
        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                raise NotPublished() from None
            raise
        if head["ETag"] == etag:
            return None
        obj = self._s3.get_object(Bucket=self.bucket, Key=self.key, IfMatch=head["ETag"])
        return obj["Body"].read(), head["ETag"], head.get("VersionId")

    def ensure(self, force=False):
        """読み込み済みのストアを返す（必要なら S3 から読み直す）。"""
        now = time.monotonic()
        if not force and self.store is not None and now - self.checked < self.check_interval:
            return self.store
        self.checked = now
        fetched = self.fetch(None if force else self.etag)
        if fetched is None:
            return self.store
        body, etag, version = fetched
        started = time.perf_counter()
        store = Store()
        store.load(body, format=RdfFormat.N_QUADS)
        self.store, self.etag, self.version, self.bytes = store, etag, version, len(body)
        self.load_ms = round((time.perf_counter() - started) * 1000, 1)
        self.loaded_at = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
        print(json.dumps({"msg": "グラフを読み込み", "triples": len(store), "load_ms": self.load_ms,
                          "bytes": self.bytes, "etag": etag}, ensure_ascii=False))
        return store

    def info(self):
        return {"source": f"s3://{self.bucket}/{self.key}", "etag": self.etag, "version_id": self.version,
                "bytes": self.bytes, "load_ms": self.load_ms, "loaded_at": self.loaded_at,
                "check_interval_seconds": self.check_interval}
