"""メールのリンクから「対応を始めた」を記録する。

リンクは公開の URL なので、署名と有効期限を確かめる。開いただけでは記録しない。メールの安全確認ソフトが
リンクを自動で開くと、誰も見ていないのに記録されてしまうため、確認画面のボタン(POST)で記録する。
"""

import base64
import html
import os
import time
from urllib.parse import parse_qs

import boto3
from botocore.exceptions import ClientError

from notify.mailer import verify

_secret = None
HEADERS = {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
           "Referrer-Policy": "no-referrer", "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'"}


def secret():
    global _secret
    if _secret is None:
        _secret = boto3.client("ssm").get_parameter(Name=os.environ["ACK_SECRET_PARAM"], WithDecryption=True)["Parameter"]["Value"].encode()
    return _secret


def page(status, title, inner=""):
    body = f'<!doctype html><html lang="ja"><meta charset="utf-8"><title>{html.escape(title)}</title><body style="font-family:sans-serif;margin:2em"><h1 style="font-size:20px">{html.escape(title)}</h1>{inner}</body></html>'
    return {"statusCode": status, "headers": HEADERS, "body": body}


def params_of(event):
    if event["requestContext"]["http"]["method"] == "POST":
        text = event.get("body") or ""
        if event.get("isBase64Encoded"):
            text = base64.b64decode(text).decode()
        return {k: v[0] for k, v in parse_qs(text).items()}
    return event.get("queryStringParameters") or {}


def record(table, event_id):
    """対応中にする。出来事がなければ False。"""
    try:
        table.update_item(Key={"pk": f"event#{event_id}"}, UpdateExpression="SET #s = :s", ConditionExpression="attribute_exists(pk)",
                          ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues={":s": "ACK"})
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def respond(event, key, table, now):
    params = params_of(event)
    event_id, expires, signature = params.get("e", ""), params.get("x", ""), params.get("s", "")
    if not (event_id and expires.isdigit() and signature and verify(key, event_id, expires, signature, now)):
        return page(403, "リンクが無効か、期限切れです")
    if event["requestContext"]["http"]["method"] == "GET":
        fields = "".join(f'<input type="hidden" name="{n}" value="{html.escape(v, quote=True)}">' for n, v in (("e", event_id), ("x", expires), ("s", signature)))
        return page(200, "対応を始めたことを記録します",
                    f'<form method="post">{fields}<button style="font-size:16px;padding:8px 16px">記録する</button></form>')
    if not record(table, event_id):
        return page(404, "対象の出来事が見つかりません")
    return page(200, "記録しました", "<p>上位への通知を止めました。</p>")


def handler(event, context):
    table = boto3.resource("dynamodb").Table(os.environ["TABLE"])
    return respond(event, secret(), table, int(time.time()))
