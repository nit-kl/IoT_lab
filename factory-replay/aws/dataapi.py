"""RDS Data API の薄いラッパー。setup_aws.py と各 Lambda から共通で使う。

Aurora は一時停止していることがあるため、再開中のエラーは待ってリトライする。
"""

import json
import time
from datetime import datetime

import boto3
from botocore.exceptions import ClientError

RESUMING_ERRORS = {"DatabaseResumingException"}


def _param(name, value):
    if value is None:
        field = {"isNull": True}
    elif isinstance(value, bool):
        field = {"booleanValue": value}
    elif isinstance(value, int):
        field = {"longValue": value}
    elif isinstance(value, float):
        field = {"doubleValue": value}
    elif isinstance(value, datetime):
        field = {"stringValue": value.isoformat()}  # SQL 側で ::timestamptz にキャストする
    else:
        field = {"stringValue": str(value)}
    return {"name": name, "value": field}


class DataApi:
    def __init__(self, resource_arn, secret_arn, database, region=None, client=None):
        self.client = client or boto3.client("rds-data", region_name=region)
        self.base = {"resourceArn": resource_arn, "secretArn": secret_arn, "database": database}

    def execute(self, sql, params=None, *, continue_after_timeout=False, resume_wait_sec=120):
        """1 文を実行し、結果行を dict のリストで返す（結果がない文は空リスト）。"""
        request = dict(self.base, sql=sql, formatRecordsAs="JSON")
        if params:
            request["parameters"] = [_param(k, v) for k, v in params.items()]
        if continue_after_timeout:
            request["continueAfterTimeout"] = True

        deadline = time.monotonic() + resume_wait_sec
        delay = 2
        while True:
            try:
                response = self.client.execute_statement(**request)
                break
            except ClientError as e:
                if e.response["Error"]["Code"] not in RESUMING_ERRORS or time.monotonic() > deadline:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 15)
        records = response.get("formattedRecords")
        return json.loads(records) if records else []

    def wake(self, resume_wait_sec=120):
        """一時停止中の DB を起こす。"""
        self.execute("SELECT 1", resume_wait_sec=resume_wait_sec)
