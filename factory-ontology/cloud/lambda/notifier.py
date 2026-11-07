"""通知の Lambda。グラフの更新、定時、未対応の確認、管理者への警告の入口。

同じ出来事を二重に送らないよう、送信の前に DynamoDB で権利を取る。送信に失敗したら権利を返して、再実行で送れるようにする。
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError

from kg.update import load_store
from notify import digest, facts
from notify.mailer import build, sign
from notify.rules import ROLES, detect, route
from notify.writer import ScriptedWriter

JST = timezone(timedelta(hours=9))
CURRENT_KEY = "kg/current.nq.gz"
RETENTION = 7 * 24 * 3600
ACK_LIFETIME = 24 * 3600
ALL_ROLES = len(ROLES)


class State:
    """DynamoDB に置く送信記録と、出来事の状態。"""

    def __init__(self, table):
        self.table = table

    def claim(self, key, now):
        try:
            self.table.put_item(Item={"pk": key, "expires": now + RETENTION}, ConditionExpression="attribute_not_exists(pk)")
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def release(self, key):
        self.table.delete_item(Key={"pk": key})

    def count_sent(self, day, now):
        result = self.table.update_item(Key={"pk": f"count#{day}"}, UpdateExpression="ADD sent :one SET expires = :e",
                                         ExpressionAttributeValues={":one": 1, ":e": now + RETENTION}, ReturnValues="UPDATED_NEW")
        return int(result["Attributes"]["sent"])

    def sent_today(self, day):
        item = self.table.get_item(Key={"pk": f"count#{day}"}).get("Item")
        return int(item["sent"]) if item else 0

    def open_event(self, event, created, now):
        self.table.put_item(Item={"pk": f"event#{event.id}", "status": "OPEN", "level": 0, "created": created, "source": event.source,
                                  "equipment": event.facts.get("equipment", ""), "expires": now + RETENTION})

    def open_events(self):
        scan = self.table.scan(FilterExpression="#s = :open", ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues={":open": "OPEN"})
        return scan["Items"]

    def raise_level(self, key, level):
        """段階を 1 つ上げる。ほかの実行が先に上げていたら False。"""
        try:
            self.table.update_item(Key={"pk": key}, UpdateExpression="SET #l = :new",
                                   ConditionExpression="#l = :old AND #s = :open", ExpressionAttributeNames={"#l": "level", "#s": "status"},
                                   ExpressionAttributeValues={":new": level, ":old": level - 1, ":open": "OPEN"})
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def close(self, key, status="CLOSED"):
        self.table.update_item(Key={"pk": key}, UpdateExpression="SET #s = :s", ExpressionAttributeNames={"#s": "status"},
                               ExpressionAttributeValues={":s": status})


class Notifier:
    def __init__(self, state, send, recipients, ack_url="", secret=b"", daily_limit=50, escalate_minutes=(5, 10), writer=None):
        self.state, self.send_mail, self.recipients = state, send, recipients
        self.ack_url, self.secret, self.daily_limit = ack_url, secret, daily_limit
        self.escalate_minutes = escalate_minutes
        self.writer = writer or ScriptedWriter()

    def address(self, role):
        return self.recipients.get(role) or self.recipients["default"]

    def ack_link(self, event_id, now):
        if not self.ack_url:
            return None
        expires = now + ACK_LIFETIME
        return f"{self.ack_url}?e={event_id}&x={expires}&s={sign(self.secret, event_id, expires)}"

    def deliver(self, claim_key, role, kind, data, moment, target="", ack=None):
        """1 通送る。送信済み、または日次の上限を超えたら送らない。送ったら True。"""
        now = int(moment.timestamp())
        if not self.state.claim(claim_key, now):
            return False
        day = moment.strftime("%Y-%m-%d")
        if self.state.sent_today(day) >= self.daily_limit:
            print(json.dumps({"event": "daily_limit_reached", "key": claim_key}, ensure_ascii=False))
            self.state.release(claim_key)
            return False
        subject, text, page = build(role, self.writer.write(role, kind, data, target), ack)
        try:
            self.send_mail(self.address(role), subject, text, page)
        except Exception:
            self.state.release(claim_key)
            raise
        self.state.count_sent(day, now)
        print(json.dumps({"event": "sent", "role": role, "subject": subject}, ensure_ascii=False))
        return True

    def handle_events(self, store, moment):
        sent = 0
        now = int(moment.timestamp())
        for event in detect(store, moment):
            notices, skipped = route(event, moment)
            print(json.dumps({"event": "detected", "id": event.id, "kind": event.kind, "to": [n.role for n in notices], "skipped": skipped}, ensure_ascii=False))
            for notice in notices:
                ack = self.ack_link(event.id, now) if notice.needs_ack else None
                key = f"sent#{event.id}#{notice.role}#{notice.target}"
                if self.deliver(key, notice.role, notice.kind, event.facts, moment, notice.target, ack):
                    sent += 1
                    if notice.needs_ack:
                        self.state.open_event(event, now, now)
        return sent

    def close_recovered(self, store):
        """復帰したアラームの出来事は、未対応の確認の対象から外す。"""
        for item in self.state.open_events():
            if item.get("source") and facts.alarm_is_closed(store, item["source"]):
                self.state.close(item["pk"])

    def escalate(self, moment):
        sent = 0
        now = int(moment.timestamp())
        chain = list(zip(("ライン責任者", "工場長"), self.escalate_minutes))
        for item in self.state.open_events():
            level = int(item["level"])
            if level >= len(chain):
                continue
            role, minutes = chain[level]
            elapsed = (now - int(item["created"])) / 60
            if elapsed < minutes or not self.state.raise_level(item["pk"], level + 1):
                continue
            data = {"equipment": item.get("equipment", ""), "minutes": int(elapsed)}
            if self.deliver(f"sent#{item['pk']}#escalate{level}", role, "escalation", data, moment):
                sent += 1
        return sent

    def digest(self, store, kind, moment):
        day = moment.strftime("%Y-%m-%d")
        data = digest.gather(store, kind, moment, sent=self.state.sent_today(day), issues=self.issue_text(store))
        sent = 0
        for role in digest.ROLES_BY_KIND[kind]:
            if self.deliver(f"sent#{kind}#{moment:%Y%m%d%H}#{role}", role, kind, data, moment):
                sent += 1
        return sent

    def shift(self, store, moment):
        sent = 0
        for line, data in digest.shift_reports(store, moment):
            if self.deliver(f"sent#shift#{moment:%Y%m%d%H}#{line}", "ライン責任者", "shift", data, moment, target=line):
                sent += 1
        return sent

    @staticmethod
    def issue_text(store):
        from kg.store import load_query, run
        rows = run(store, load_query("cq8_data_issues"))
        return "\n".join(f"・{r['table']}.{r['column']}: {r['type']} {r['value'] or ''} {r['rows']} 件" for r in rows)

    def admin(self, title, detail, moment):
        key = f"sent#admin#{title}#{moment:%Y%m%d%H}"
        return self.deliver(key, "管理者", "admin", {"title": title, "detail": detail}, moment)


def admin_message(event):
    """EventBridge の通知から、管理者向けの件名と本文を作る。なければ None。"""
    source = event.get("source")
    if source == "aws.cloudwatch":
        name = event["detail"]["alarmName"]
        return f"監視アラーム {name}", event["detail"]["state"].get("reason", "")
    if source == "aws.s3" and event["detail"]["object"]["key"].startswith("kg/rejected/"):
        return "読めないファイルを飛ばしました", event["detail"]["object"]["key"]
    return None


def is_active(store, moment):
    """データが長く届いていないとき(手元の PC が止まっているとき)は定時レポートを送らない。"""
    latest = facts.latest_activity(store)
    return bool(latest) and moment - datetime.fromisoformat(latest) < timedelta(days=1)


def build_notifier():
    table = boto3.resource("dynamodb").Table(os.environ["TABLE"])
    ses = boto3.client("sesv2")
    sender = os.environ["SENDER"]

    def send(to, subject, text, page):
        ses.send_email(FromEmailAddress=sender, Destination={"ToAddresses": [to]},
                       Content={"Simple": {"Subject": {"Data": subject, "Charset": "UTF-8"},
                                           "Body": {"Text": {"Data": text, "Charset": "UTF-8"}, "Html": {"Data": page, "Charset": "UTF-8"}}}})
    secret = boto3.client("ssm").get_parameter(Name=os.environ["ACK_SECRET_PARAM"], WithDecryption=True)["Parameter"]["Value"].encode()
    minutes = tuple(int(m) for m in os.environ["ESCALATE_MINUTES"].split(","))
    return Notifier(State(table), send, json.loads(os.environ["RECIPIENTS"]), os.environ["ACK_URL"], secret,
                    int(os.environ["DAILY_LIMIT"]), minutes)


def read_store():
    body = boto3.client("s3").get_object(Bucket=os.environ["BUCKET"], Key=CURRENT_KEY)["Body"].read()
    return load_store(body)


def run_event(notifier, event, moment, get_store):
    message = admin_message(event)
    if message:
        return {"admin": notifier.admin(*message, moment)}
    kind = event.get("kind")
    if kind == "escalate":
        return {"escalated": notifier.escalate(moment)}
    store = get_store()
    if kind in ("daily", "weekly"):
        if not event.get("force") and not is_active(store, moment):
            return {"skipped": "データが届いていません"}
        return {"sent": notifier.digest(store, kind, moment)}
    if kind == "shift":
        if not event.get("force") and not is_active(store, moment):
            return {"skipped": "データが届いていません"}
        return {"sent": notifier.shift(store, moment)}
    notifier.close_recovered(store)
    return {"sent": notifier.handle_events(store, moment)}


_notifier = None


def handler(event, context):
    global _notifier
    _notifier = _notifier or build_notifier()
    started = time.perf_counter()
    result = run_event(_notifier, event, datetime.now(JST), read_store)
    print(json.dumps({"event": "notifier_done", "ms": int((time.perf_counter() - started) * 1000), **result}, ensure_ascii=False))
    return result
