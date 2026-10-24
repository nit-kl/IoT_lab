"""HTTP API を SigV4 署名付きで呼ぶ。cli.py と postman_env.py から使う。"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "aws"))
from naming import stack_name  # noqa: E402


class ApiError(Exception):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}: {body.get('error', body)}")
        self.status = status
        self.body = body


def api_url(session, owner):
    """スタックの出力から HTTP API の URL を取る（末尾の / は除く）。"""
    cfn = session.client("cloudformation")
    outputs = cfn.describe_stacks(StackName=stack_name(owner))["Stacks"][0].get("Outputs", [])
    for o in outputs:
        if o["OutputKey"] == "ApiUrl":
            return o["OutputValue"].rstrip("/")
    raise SystemExit("スタックに ApiUrl がありません。python aws/setup_aws.py を実行して環境を更新してください")


class Client:
    def __init__(self, owner, region):
        self.session = boto3.Session(region_name=region)
        self.region = region
        self.base = api_url(self.session, owner)

    def request(self, method, path, params=None, body=None):
        query = urlencode({k: v for k, v in (params or {}).items() if v is not None})
        url = f"{self.base}{path}" + (f"?{query}" if query else "")
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        req = AWSRequest(method=method, url=url, data=data, headers=headers)
        SigV4Auth(self.session.get_credentials().get_frozen_credentials(), "lambda", self.region).add_auth(req)
        http = urllib.request.Request(url, data=data, method=method, headers=dict(req.headers))
        try:
            with urllib.request.urlopen(http, timeout=60) as res:
                return json.loads(res.read())
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = {"error": raw.decode(errors="replace")}
            raise ApiError(e.code, payload) from None

    def get(self, path, **params):
        return self.request("GET", path, params)
