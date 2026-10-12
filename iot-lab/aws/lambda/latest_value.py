# IoTルールから1メッセージずつ呼ばれ、機器ごとの最新値をDynamoDBに保存する
# setup_aws.py がこのファイルを template.yaml に埋め込んでデプロイします
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3

table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
MAX_FUTURE = timedelta(minutes=5)


def build_update(msg):
    """1メッセージ分の update_item 引数を作る(古い・未来すぎるものは None)"""
    ts = msg["ts"]  # 例 2026-10-10T10:39:24.711Z(UTC・ミリ秒固定なので文字列比較で前後が分かる)
    received = datetime.fromtimestamp(msg["received_at_ms"] / 1000, timezone.utc)
    if datetime.fromisoformat(ts.replace("Z", "+00:00")) > received + MAX_FUTURE:
        return None  # 時計が狂ったゲートウェイの値で最新値を固定されないように捨てる
    names = {"#ts": "ts", "#st": "status"}
    values = {":ts": ts, ":seq": msg["seq"], ":st": msg["status"],
              ":gw": msg["gateway_id"], ":kind": msg["kind"], ":rcv": msg["received_at_ms"]}
    sets = ["#ts = :ts", "seq = :seq", "#st = :st", "gateway_id = :gw", "kind = :kind",
            "received_at_ms = :rcv"]
    if msg["status"] == "ok":
        # 正常時だけ値を更新。comm_error のときは最後に読めた値(last known good)を残す
        names["#met"] = "metrics"  # metrics は DynamoDB の予約語なので別名が必要
        values[":m"] = {k: Decimal(str(v)) for k, v in msg["metrics"].items()}
        sets += ["#met = :m", "last_ok_ts = :ts"]
    return {
        "Key": {"equipment_id": msg["equipment_id"]},
        "UpdateExpression": "SET " + ", ".join(sets),
        # 保存済みより新しいときだけ書く(再送・重複・並列実行で順番が入れ替わっても戻らない)
        "ConditionExpression": "attribute_not_exists(#ts) OR #ts < :ts",
        "ExpressionAttributeNames": names,
        "ExpressionAttributeValues": values,
    }


def handler(event, context):
    who = f'{event.get("equipment_id")} ts={event.get("ts")} seq={event.get("seq")}'
    args = build_update(event)
    if args is None:
        print(f"SKIP future   {who}")
        return
    try:
        table.update_item(**args)
        print(f"UPDATED       {who} status={event['status']}")
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        # 異常ではないので例外にしない(例外にするとLambdaが自動リトライして無駄になる)
        print(f"SKIP stale    {who}")
