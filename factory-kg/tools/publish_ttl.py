"""ナレッジグラフを SHACL で検証し、通ったら S3 に公開する（グラフの更新はこれだけで行う。決定事項 4）。

    python tools/publish_ttl.py --owner kojima               # build/instances.ttl + ontology/ を検証して公開
    python tools/publish_ttl.py --owner kojima --dry-run     # 検証と N-Quads の作成だけ（上げない）

ontology/*.ttl とインスタンスを名前付きグラフごとに 1 つの N-Quads（kg/current.nq）にまとめて上げる。
1 ファイルなので、SPARQL Lambda から見て「一部だけ新しい」状態にならない。上げた後、Lambda に読み直しを依頼して
新しいグラフが使われていることを確かめる。S3 はバージョニングしているので、1 つ前に戻すこともできる（--rollback）。
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from pyoxigraph import RdfFormat

import query
import validate
from kgapi import ApiError, KgClient
from kgcommon import INSTANCES


def build_nquads(instances):
    store, _ = query.open_store(instances)
    return store.dump(format=RdfFormat.N_QUADS), len(store)


def confirm_loaded(client, etag):
    status = client.status(reload=True)
    if status["etag"] != etag:
        raise SystemExit(f"Lambda が新しいグラフを読み込んでいません（Lambda: {status['etag']}、S3: {etag}）")
    print(f"SPARQL エンドポイントが新しいグラフを使っています: {status['triples']} トリプル"
          f"（読み込み {status['load_ms']} ms）")


def rollback(client):
    s3 = client.session.client("s3")
    bucket, key = client.outputs["GraphBucket"], client.outputs["GraphKey"]
    versions = [v for v in s3.list_object_versions(Bucket=bucket, Prefix=key).get("Versions", []) if v["Key"] == key]
    versions.sort(key=lambda v: v["LastModified"], reverse=True)
    if len(versions) < 2:
        raise SystemExit("戻せる 1 つ前の版がありません")
    previous = versions[1]
    s3.copy_object(Bucket=bucket, Key=key, CopySource={"Bucket": bucket, "Key": key, "VersionId": previous["VersionId"]})
    etag = s3.head_object(Bucket=bucket, Key=key)["ETag"]
    print(f"1 つ前の版（{previous['LastModified'].astimezone().isoformat(timespec='seconds')}）に戻しました")
    confirm_loaded(client, etag)


def main():
    ap = argparse.ArgumentParser(description="ナレッジグラフを検証して S3 に公開する")
    ap.add_argument("--owner", required=True)
    ap.add_argument("--region", default="ap-northeast-1")
    ap.add_argument("--instances", type=Path, default=INSTANCES, help="インスタンスの TTL（既定 build/instances.ttl）")
    ap.add_argument("--dry-run", action="store_true", help="検証と N-Quads の作成だけ行い、S3 には上げない")
    ap.add_argument("--rollback", action="store_true", help="公開中のグラフを 1 つ前の版に戻す")
    args = ap.parse_args()

    if args.rollback:
        rollback(KgClient(args.owner, args.region))
        return
    if not args.instances.exists():
        raise SystemExit(f"{args.instances} がありません。先に python tools/generate_ttl.py を実行してください")

    conforms, problems, *_ = validate.run(args.instances)
    if not conforms:
        print(f"SHACL 検証で違反が {len(problems)} 件あるため公開しません:", file=sys.stderr)
        for focus, message in problems:
            print(f"  {focus}: {message}", file=sys.stderr)
        sys.exit(1)
    body, triples = build_nquads(args.instances)
    print(f"SHACL 検証: 適合。N-Quads: {triples} トリプル / {len(body):,} バイト")
    if args.dry_run:
        return

    client = KgClient(args.owner, args.region)
    bucket, key = client.outputs["GraphBucket"], client.outputs["GraphKey"]
    put = client.session.client("s3").put_object(
        Bucket=bucket, Key=key, Body=body, ContentType="application/n-quads",
        Metadata={"triples": str(triples), "instances": args.instances.name,
                  "published-at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    print(f"公開しました: s3://{bucket}/{key}（版 {put.get('VersionId')}）")
    try:
        confirm_loaded(client, put["ETag"])
    except ApiError as e:
        raise SystemExit(f"公開はしましたが、SPARQL エンドポイントでの確認に失敗しました: {e}")


if __name__ == "__main__":
    main()
