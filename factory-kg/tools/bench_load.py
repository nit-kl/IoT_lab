"""グラフの大きさごとの読み込み時間を測る（懸念点 G1・5 章「スケールの考え方」の実測）。

    python tools/bench_load.py                                   # ローカル（この PC）で測る
    python tools/bench_load.py --owner kojima                    # AWS の SPARQL Lambda で測る
    python tools/bench_load.py --owner kojima --sizes 10000 100000

本物のインスタンスを、IRI を変えて何倍にも複製した「合成グラフ」で測る（構造は本物と同じ）。
--owner のときは、公開中の kg/current.nq を一時的に合成グラフに差し替え、Lambda の /status?reload=1 で
読み込み時間を取り、最後に必ず元のグラフに戻す（測定中の数十秒は、SPARQL が合成グラフを返す）。
"""

import argparse
import time

from pyoxigraph import RdfFormat, Store

import query
from kgapi import KgClient
from kgnaming import stack_name
from kgcommon import ID, INSTANCES, EXAMPLE_INSTANCES

DEFAULT_SIZES = [10_000, 100_000, 1_000_000]


def synthetic(target):
    """本物のグラフ（オントロジー込み）に、インスタンスを複製して約 target トリプルにした N-Quads を返す。"""
    base, _ = query.open_store(INSTANCES if INSTANCES.exists() else EXAMPLE_INSTANCES)
    nq = base.dump(format=RdfFormat.N_QUADS).decode()
    instance_lines = [l for l in nq.splitlines() if l.endswith("<https://xcp-factory.example/graph/instances> .")]
    copies = max(0, (target - len(base)) // max(1, len(instance_lines)))
    prefix = str(ID)
    parts = [nq]
    for i in range(copies):
        parts.append("\n".join(l.replace(prefix, f"{prefix}bench{i}/") for l in instance_lines) + "\n")
    body = "".join(parts).encode()
    return body, len(base) + copies * len(instance_lines)


def load_locally(body):
    started = time.perf_counter()
    store = Store()
    store.load(body, format=RdfFormat.N_QUADS)
    return (time.perf_counter() - started) * 1000, len(store)


def main():
    ap = argparse.ArgumentParser(description="グラフの大きさごとの読み込み時間を測る")
    ap.add_argument("--owner", help="指定すると AWS の SPARQL Lambda で測る（指定しなければローカル）")
    ap.add_argument("--region", default="ap-northeast-1")
    ap.add_argument("--sizes", type=int, nargs="+", default=DEFAULT_SIZES, help="測るトリプル数（既定 1 万・10 万・100 万）")
    args = ap.parse_args()

    if not args.owner:
        print(f"{'トリプル':>10}  {'サイズ':>8}  {'読み込み':>9}")
        for size in args.sizes:
            body, _ = synthetic(size)
            ms, triples = load_locally(body)
            print(f"{triples:>10,}  {len(body) / 1e6:>6.1f}MB  {ms:>7.0f}ms")
        return

    client = KgClient(args.owner, args.region)
    s3 = client.session.client("s3")
    bucket, key = client.outputs["GraphBucket"], client.outputs["GraphKey"]
    original = s3.get_object(Bucket=bucket, Key=key)
    original_body, original_meta = original["Body"].read(), original.get("Metadata", {})
    function = f"{stack_name(args.owner)}-sparql"
    memory = client.session.client("lambda").get_function_configuration(FunctionName=function)["MemorySize"]
    print(f"Lambda のメモリ: {memory} MB（100 万トリプル規模は 2048 MB 以上が必要。setup_aws.py --memory で変更）")
    print(f"{'トリプル':>10}  {'サイズ':>8}  {'Lambda の読み込み':>16}  {'取得込みの応答':>14}")
    try:
        for size in args.sizes:
            body, _ = synthetic(size)
            put = s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/n-quads",
                                Metadata={"benchmark": "true"})
            started = time.perf_counter()
            status = client.status(reload=True)
            elapsed = (time.perf_counter() - started) * 1000
            if status["etag"] != put["ETag"]:
                print(f"{size:>10,}  （Lambda が別の版を返しました: {status['etag']}）")
                continue
            print(f"{status['triples']:>10,}  {len(body) / 1e6:>6.1f}MB  {status['load_ms']:>14.0f}ms  {elapsed:>12.0f}ms")
    finally:
        put = s3.put_object(Bucket=bucket, Key=key, Body=original_body, ContentType="application/n-quads",
                            Metadata=original_meta)
        status = client.status(reload=True)
        print(f"元のグラフに戻しました（{status['triples']} トリプル、ETag 一致: {status['etag'] == put['ETag']}）")


if __name__ == "__main__":
    main()
