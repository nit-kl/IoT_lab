"""
DynamoDB に保存された「機器ごとの最新値」を表示するスクリプト

  - ts は UTC で保存されています。表示のときだけ日本時間(JST)に変換します
  - 「何秒前の値か」を出し、一定時間更新が無い機器は STALE(古い)と表示します
    (最新値テーブルを使った死活監視の考え方)

実行: python aws/show_latest.py [--watch 5] [--stale-sec 60] [--region ap-northeast-1] [--profile 名前]
"""
import argparse
import time
from datetime import datetime, timedelta, timezone

import boto3

STACK_NAME = "iot-lab"
JST = timezone(timedelta(hours=9))


def parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def show(table, stale_sec: int) -> None:
    # 機器数ぶんの小さなテーブルなので Scan で全件読む(大きなテーブルでは Query を使う)
    items = sorted(table.scan()["Items"], key=lambda i: i["equipment_id"])
    now = datetime.now(timezone.utc)
    print(f"--- {now.astimezone(JST):%Y-%m-%d %H:%M:%S} JST  {len(items)} 台")
    if not items:
        print("(まだデータがありません。python gateway/gateway.py を動かしてください)")
    for it in items:
        age = (now - parse_ts(it["ts"])).total_seconds()
        flag = "STALE" if age > stale_sec else "fresh"
        metrics = ", ".join(f"{k}={v}" for k, v in sorted(it.get("metrics", {}).items()))
        last_ok = parse_ts(it["last_ok_ts"]).astimezone(JST).strftime("%H:%M:%S") if "last_ok_ts" in it else "-"
        print(f"{it['equipment_id']:<12} {it['status']:<10} {flag:<5} {age:6.0f}秒前  "
              f"ts={parse_ts(it['ts']).astimezone(JST):%H:%M:%S}  last_ok={last_ok}  seq={it['seq']}")
        print(f"{'':12} {metrics}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="ap-northeast-1")
    ap.add_argument("--profile", default=None)
    ap.add_argument("--watch", type=int, default=0, help="指定秒ごとに繰り返し表示(Ctrl+Cで終了)")
    ap.add_argument("--stale-sec", type=int, default=60, help="この秒数より古い値を STALE と表示")
    args = ap.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    outputs = session.client("cloudformation").describe_stacks(StackName=STACK_NAME)["Stacks"][0]["Outputs"]
    table_name = {o["OutputKey"]: o["OutputValue"] for o in outputs}.get("LatestTableName")
    if not table_name:
        raise SystemExit("最新値テーブルがありません。先に python aws/setup_aws.py を実行してください")
    table = session.resource("dynamodb").Table(table_name)

    while True:
        show(table, args.stale_sec)
        if not args.watch:
            break
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
