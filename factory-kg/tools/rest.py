"""REST API（/v1/…）を呼ぶ CLI（aws login の認証情報で SigV4 署名）。仕様は aws/openapi.yaml。

    python tools/rest.py --owner kojima /v1/equipment
    python tools/rest.py --owner kojima /v1/equipment/CNC-01
    python tools/rest.py --owner kojima /v1/equipment/CNC-01/properties/status/readings/summary source_from=2026-10-01T06:00
    python tools/rest.py --owner kojima /v1/search q=コンプレッサ
"""

import argparse
import json
import sys

from kgapi import ApiError, KgClient


def main():
    ap = argparse.ArgumentParser(description="工場ナレッジグラフの REST API を呼ぶ")
    ap.add_argument("--owner", required=True)
    ap.add_argument("--region", default="ap-northeast-1")
    ap.add_argument("path", help="/v1/… のパス")
    ap.add_argument("params", nargs="*", help="クエリパラメータ（名前=値）")
    args = ap.parse_args()
    params = {}
    for p in args.params:
        if "=" not in p:
            ap.error(f"パラメータは 名前=値 の形で指定してください: {p}")
        k, v = p.split("=", 1)
        params[k] = v
    try:
        print(json.dumps(KgClient(args.owner, args.region).rest(args.path, params), ensure_ascii=False, indent=2))
    except ApiError as e:
        print(e, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
