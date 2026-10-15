# 最新値テーブルの変化(DynamoDB Streams)と1分ごとの定期実行で呼ばれ、異常の発生・復旧をメールで知らせる
# setup_aws.py がこのファイルを template.yaml に埋め込んでデプロイします
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.types import TypeDeserializer

ddb = boto3.resource("dynamodb")
latest = ddb.Table(os.environ["LATEST_TABLE"])
state = ddb.Table(os.environ["STATE_TABLE"])  # 通知済みの異常だけを持つ(1異常=1行)
sns = boto3.client("sns")
STALE_SEC = int(os.environ["STALE_SEC"])
TEMP_MAX = Decimal(os.environ["TEMP_MAX_C"])  # ショーケースの庫内温度の上限
JST = timezone(timedelta(hours=9))
CCF = state.meta.client.exceptions.ConditionalCheckFailedException


def jst(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(JST).strftime("%m/%d %H:%M:%S")


def send(subject, body):
    sns.publish(TopicArn=os.environ["TOPIC_ARN"], Subject=subject[:100], Message=body)
    print("NOTIFY", subject)


def raise_alarm(eq, kind, what, ts):
    key = {"pk": f"{eq}#{kind}"}
    try:  # まだ通知していないときだけ書ける(同じ異常で何通も送らない)
        state.put_item(Item={**key, "since": ts, "what": what}, ConditionExpression="attribute_not_exists(pk)")
    except CCF:
        return
    try:
        send(f"[発生] {eq} {what}", f"{eq}: {what}\n発生: {jst(ts)} (JST)")
    except Exception:
        state.delete_item(Key=key)  # 送れなかったら記録を戻し、再試行で送り直せるようにする
        raise


def clear_alarm(eq, kind, ts):
    try:  # 通知済みのときだけ消せる(通知していない一瞬の異常なら復旧通知も出さない)
        old = state.delete_item(Key={"pk": f"{eq}#{kind}"}, ConditionExpression="attribute_exists(pk)",
                                ReturnValues="ALL_OLD")["Attributes"]
    except CCF:
        return
    send(f"[復旧] {eq} {old['what']}", f"{eq}: {old['what']} は復旧しました\n発生: {jst(old['since'])}  復旧: {jst(ts)} (JST)")


def on_change(new, old):
    eq, ts = new["equipment_id"], new["ts"]
    if new["status"] == "comm_error":
        if old.get("status") == "comm_error":  # 2回続いたら通知(1回だけの瞬断は無視)
            raise_alarm(eq, "comm", "通信エラー", ts)
        return
    if old.get("status") == "comm_error":
        clear_alarm(eq, "comm", ts)
    m, pm = new.get("metrics", {}), old.get("metrics", {})
    code, prev = m.get("alarm_code", 0), pm.get("alarm_code", 0)
    if code and prev:  # 2回続けて異常なら通知(オンディレイ)
        raise_alarm(eq, "alarm", f"異常コード {int(code)}", ts)
    elif prev and not code:
        clear_alarm(eq, "alarm", ts)
    if "case_temp_c" in m:  # ショーケース
        if hot(m) and hot(pm):
            raise_alarm(eq, "temp", f"庫内温度 {m['case_temp_c']}℃(上限 {TEMP_MAX}℃)", ts)
        elif pm.get("case_temp_c", 0) > TEMP_MAX >= m["case_temp_c"]:
            clear_alarm(eq, "temp", ts)


def hot(m):
    # 霜取り中は庫内温度が上がるのが正常なので、上限を超えても異常としない
    return m.get("case_temp_c", 0) > TEMP_MAX and not m.get("defrost")


def check_stale():
    # 「データが来ない」は書き込みが起きないのでStreamsでは分からない。時刻を見て判断する
    now = datetime.now(timezone.utc)
    active = {i["pk"] for i in state.scan()["Items"]}
    for it in latest.scan()["Items"]:
        eq = it["equipment_id"]
        age = (now - datetime.fromisoformat(it["ts"].replace("Z", "+00:00"))).total_seconds()
        if age > STALE_SEC and f"{eq}#stale" not in active:
            raise_alarm(eq, "stale", f"データ途絶({STALE_SEC}秒以上更新なし)", it["ts"])
        elif age <= STALE_SEC and f"{eq}#stale" in active:
            clear_alarm(eq, "stale", it["ts"])


def handler(event, context):
    if "Records" not in event:  # EventBridge Schedulerからの定期実行
        return check_stale()
    des = TypeDeserializer()
    for rec in event["Records"]:
        img = {k: {a: des.deserialize(v) for a, v in rec["dynamodb"].get(k, {}).items()}
               for k in ("NewImage", "OldImage")}
        if img["NewImage"]:
            on_change(img["NewImage"], img["OldImage"])
