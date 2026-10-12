"""
クラウドからゲートウェイへ命令を送り、結果を待つスクリプト(ハンズオン5)

  1. 命令(command_id・宛先・値・期限・送った人)を作り、命令テーブルに「sent」として記録
  2. MQTT トピック lab/<ゲートウェイ>/<機器>/cmd に QoS1 で送信
  3. ゲートウェイが返した結果(…/cmd_result)が IoTルール経由で命令テーブルに書かれるのを待って表示

実行例:
  python aws/send_command.py --value 24.5                 # AHU-01 の設定温度を 24.5℃ に
  python aws/send_command.py --value 35                   # 範囲外 → ゲートウェイが拒否
  python aws/send_command.py --value 24.0 --ttl -10       # 最初から期限切れ → 拒否
  python aws/send_command.py --value 24.0 --command-id test-1   # 同じIDで2回送ると、2回目は再実行されない
"""
import argparse
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3

STACK_NAME = "iot-lab"


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def main() -> None:
    ap = argparse.ArgumentParser(description="ゲートウェイに命令を送る")
    ap.add_argument("--equipment", default="AHU-01")
    ap.add_argument("--point", default="setpoint_c")
    ap.add_argument("--value", type=float, required=True)
    ap.add_argument("--ttl", type=int, default=60, help="命令の有効期限(秒)。これより遅れて届いたら実行されない")
    ap.add_argument("--command-id", default=None, help="省略時は自動採番。同じIDで送ると重複扱い")
    ap.add_argument("--gateway-id", default="gw01")
    ap.add_argument("--topic-prefix", default="lab")
    ap.add_argument("--wait", type=int, default=30, help="結果を待つ秒数")
    ap.add_argument("--region", default="ap-northeast-1")
    ap.add_argument("--profile", default=None)
    args = ap.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    outputs = session.client("cloudformation").describe_stacks(StackName=STACK_NAME)["Stacks"][0]["Outputs"]
    table_name = {o["OutputKey"]: o["OutputValue"] for o in outputs}.get("CommandTableName")
    if not table_name:
        raise SystemExit("命令テーブルがありません。先に python aws/setup_aws.py を実行してください")
    table = session.resource("dynamodb").Table(table_name)
    endpoint = session.client("iot").describe_endpoint(endpointType="iot:Data-ATS")["endpointAddress"]
    iot_data = session.client("iot-data", endpoint_url=f"https://{endpoint}")

    now = datetime.now(timezone.utc)
    cmd = {
        "command_id": args.command_id or str(uuid.uuid4()),
        "point": args.point,
        "value": args.value,
        "issued_at": iso(now),
        "expires_at": iso(now + timedelta(seconds=args.ttl)),
        # 誰が操作したかを必ず残す(監査ログ)
        "issued_by": session.client("sts").get_caller_identity()["Arn"],
    }
    topic = f"{args.topic_prefix}/{args.gateway_id}/{args.equipment}/cmd"

    table.put_item(Item={**cmd, "equipment_id": args.equipment, "value": Decimal(str(args.value)), "status": "sent"})
    iot_data.publish(topic=topic, qos=1, payload=json.dumps(cmd))
    print(f"送信: {topic}  {args.point}={args.value}  command_id={cmd['command_id']}  期限={args.ttl}秒")

    deadline = time.time() + args.wait
    while time.time() < deadline:
        item = table.get_item(Key={"command_id": cmd["command_id"]}, ConsistentRead=True).get("Item", {})
        if item.get("status") != "sent":
            print(f"結果: {item.get('status')}" + (f"(理由: {item['reason']})" if item.get("reason") else ""))
            if "before" in item:
                print(f"      変更前 {item['before']} → 変更後 {item.get('after')}")
            if item.get("duplicate"):
                print("      同じ command_id は実行済みのため、ゲートウェイは再実行せず前回の結果を返しました")
            return
        time.sleep(1)
    print(f"{args.wait}秒待っても結果が返りませんでした。ゲートウェイは動いていますか?(MQTT接続中でないと命令は届きません)")


if __name__ == "__main__":
    main()
