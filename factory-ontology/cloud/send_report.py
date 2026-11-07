"""定時レポートを今すぐ送る(デモ用)。データが古くても送る。

    python cloud/send_report.py --owner kojima daily     # daily / weekly / shift
"""

import argparse
import json

import boto3

from naming import add_common_args, stack_name


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(parser)
    parser.add_argument("kind", choices=["daily", "weekly", "shift"])
    args = parser.parse_args()
    client = boto3.Session(region_name=args.region).client("lambda")
    response = client.invoke(FunctionName=f"{stack_name(args.owner)}-notifier", Payload=json.dumps({"kind": args.kind, "force": True}))
    print(json.dumps(json.loads(response["Payload"].read()), ensure_ascii=False))


if __name__ == "__main__":
    main()
