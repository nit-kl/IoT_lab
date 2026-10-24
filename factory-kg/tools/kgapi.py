"""KG の SPARQL エンドポイント（とスタックの出力）を、aws login の認証情報で SigV4 署名して呼ぶ。"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "aws"))
from kgnaming import stack_name  # noqa: E402


class ApiError(Exception):
    def __init__(self, status, body):
        try:
            detail = json.loads(body).get("error", body)
        except (ValueError, AttributeError):
            detail = body.decode(errors="replace") if isinstance(body, bytes) else body
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status


def stack_outputs(session, name):
    cfn = session.client("cloudformation")
    stack = cfn.describe_stacks(StackName=name)["Stacks"][0]
    return {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}


def signed_call(session, method, url, data=None, headers=None):
    """SigV4（サービス名 lambda）で署名して呼び、(ステータス, 本文 bytes, Content-Type) を返す（エラーでも例外にしない）。"""
    headers = dict(headers or {})
    req = AWSRequest(method=method, url=url, data=data, headers=headers)
    SigV4Auth(session.get_credentials().get_frozen_credentials(), "lambda", session.region_name).add_auth(req)
    http = urllib.request.Request(url, data=data, method=method, headers=dict(req.headers))
    try:
        with urllib.request.urlopen(http, timeout=60) as res:
            return res.status, res.read(), res.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers.get("Content-Type", "")


def signed_request(session, method, url, data=None, headers=None):
    """signed_call と同じだが、成功時は (本文 bytes, Content-Type) を返し、エラーは ApiError にする。"""
    status, body, content_type = signed_call(session, method, url, data, headers)
    if status >= 400:
        raise ApiError(status, body)
    return body, content_type


class KgClient:
    def __init__(self, owner, region):
        self.session = boto3.Session(region_name=region)
        self.outputs = stack_outputs(self.session, stack_name(owner))
        self.base = self.outputs["SparqlUrl"].rstrip("/")

    def query(self, sparql, accept="application/sparql-results+json"):
        body, content_type = signed_request(self.session, "POST", f"{self.base}/sparql", data=sparql.encode(),
                                            headers={"Content-Type": "application/sparql-query", "Accept": accept})
        return json.loads(body) if "json" in content_type else body.decode()

    def status(self, reload=False):
        body, _ = signed_request(self.session, "GET", f"{self.base}/status" + ("?reload=1" if reload else ""))
        return json.loads(body)
