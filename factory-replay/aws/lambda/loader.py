"""S3 の incoming/*.csv が置かれたら起動し、DB に取り込んでリプレイを開始する。

CSV のデータは Lambda を通らない。DB（aws_s3 拡張）が S3 から直接読み込む。
"""

import json
import os
import time
from urllib.parse import unquote_plus

from botocore.exceptions import ClientError

import schedule
from dataapi import DataApi

db = DataApi(os.environ["CLUSTER_ARN"], os.environ["SECRET_ARN"], os.environ["DATABASE"])

# DB の再開直後や S3 取り込み用ロールの更新直後は、DB がロールの認証情報をまだ取得できず
# 「unable to access credentials stored with the database instance」で失敗することがある。
# 数分で解消するので、このエラーのときだけ待って再試行する
CREDENTIALS_ERROR = "unable to access credentials"
CREDENTIALS_RETRIES = 6
CREDENTIALS_WAIT_SEC = 20


def log(**fields):
    print(json.dumps(fields, ensure_ascii=False, default=str))


def handler(event, context):
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])
        load(bucket, key)


def load(bucket, key):
    log(msg="取り込み開始", bucket=bucket, key=key)
    # 取り込み中は replayer を止める（loading の間は replayer も何もしない）
    schedule.set_enabled(False)
    # source_key は取り込みに成功してから start_replay() が書き換える（それまでは staging の中身の CSV のまま）
    db.execute(
        "UPDATE replay_state SET status = 'loading', message = :msg, updated_at = now() WHERE id = 1",
        {"msg": f"{key} を取り込み中"}, resume_wait_sec=150)

    for attempt in range(CREDENTIALS_RETRIES + 1):
        try:
            db.execute("SELECT start_replay(:bucket, :key, :region)",
                       {"bucket": bucket, "key": key, "region": os.environ["AWS_REGION"]},
                       continue_after_timeout=True)
        except ClientError as e:
            # 大きい CSV は Data API の応答待ち（約 45 秒）を超える。DB 側では取り込みが続いているので、
            # replayer を動かしておけば、取り込み完了（status = running）後に自動で反映が始まる
            if e.response["Error"]["Code"] != "StatementTimeoutException":
                raise
            log(msg="取り込みが長いため完了を待たずに終了。DB 側で継続中", key=key)
        state = db.execute("SELECT status, total_rows, message FROM replay_state WHERE id = 1")[0]
        if state["status"] != "error" or CREDENTIALS_ERROR not in (state["message"] or ""):
            break
        if attempt < CREDENTIALS_RETRIES:
            log(msg="S3 取り込み用ロールの認証情報待ち。再試行します", attempt=attempt + 1, message=state["message"])
            time.sleep(CREDENTIALS_WAIT_SEC)

    log(msg="取り込み結果", key=key, **state)
    if state["status"] in ("running", "loading"):
        schedule.set_enabled(True)
