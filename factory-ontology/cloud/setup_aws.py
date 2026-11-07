"""AWS の環境(S3、IAM Roles Anywhere)を作成・更新し、オンプレ側の認証設定を書き出す。

    python cloud/setup_aws.py --owner kojima

何度実行しても同じ結果になる。証明書は 30 日を切っていたら作り直す。
作るファイル: cloud/.secrets(CA の秘密鍵)、onprem/.secrets(抽出プログラムの証明書、秘密鍵、AWS の設定)。どちらも Git に入らない。
"""

import argparse
import hashlib
import io
import json
import subprocess
import sys
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
from botocore.exceptions import ClientError, WaiterError
from cryptography import x509

from certs import create_ca, create_client
from naming import add_common_args, artifacts_bucket_name, stack_name

HERE = Path(__file__).resolve().parent
ONPREM = HERE.parent / "onprem"
GRAPH = HERE.parent / "graph"
WHEEL_DIR = HERE / ".cache" / "wheels"
PYOXIGRAPH = "0.5.13"  # graph/requirements.txt と同じ版
LAMBDA_FILES = {  # zip の中の名前 → 元のファイル
    "updater.py": HERE / "lambda" / "updater.py",
    "kg/__init__.py": GRAPH / "kg" / "__init__.py",
    "kg/common.py": GRAPH / "kg" / "common.py",
    "kg/transform.py": GRAPH / "kg" / "transform.py",
    "kg/update.py": GRAPH / "kg" / "update.py",
    "mapping/mapping.toml": GRAPH / "mapping" / "mapping.toml",
}
TEMPLATE = HERE / "template.yaml"
CA_DIR = HERE / ".secrets"
CLIENT_DIR = ONPREM / ".secrets"
HELPER = CLIENT_DIR / "aws_signing_helper.exe"
HELPER_URL = "https://rolesanywhere.amazonaws.com/releases/1.8.6/X86_64/Windows/Server2022/aws_signing_helper.exe"
HELPER_SHA256 = "1667595b5e5f3e639b9fa250ec83f0965344923729758006af64ecab9968aa66"
RENEW_BEFORE = timedelta(days=30)
PROFILE = "factory-extractor"


def ensure_ca(common_name):
    key, cert = CA_DIR / "ca.key", CA_DIR / "ca.crt"
    if not key.exists():
        CA_DIR.mkdir(exist_ok=True)
        key_pem, cert_pem = create_ca(common_name)
        key.write_bytes(key_pem)
        cert.write_bytes(cert_pem)
        print("CA を作成しました")
    return key.read_bytes(), cert.read_bytes()


def ensure_client(common_name, ca_key, ca_cert):
    key, cert = CLIENT_DIR / "extractor.key", CLIENT_DIR / "extractor.crt"
    CLIENT_DIR.mkdir(exist_ok=True)
    if cert.exists():
        current = x509.load_pem_x509_certificate(cert.read_bytes())
        issued_by_this_ca = current.issuer == x509.load_pem_x509_certificate(ca_cert).subject
        if issued_by_this_ca and current.not_valid_after_utc - datetime.now(timezone.utc) > RENEW_BEFORE:
            return key, cert
    key_pem, cert_pem = create_client(common_name, ca_key, ca_cert)
    key.write_bytes(key_pem)
    cert.write_bytes(cert_pem)
    print("抽出プログラムの証明書を作成しました")
    return key, cert


def ensure_helper():
    if HELPER.exists() and hashlib.sha256(HELPER.read_bytes()).hexdigest() == HELPER_SHA256:
        return
    with urllib.request.urlopen(HELPER_URL, timeout=120) as response:
        body = response.read()
    actual = hashlib.sha256(body).hexdigest()
    if actual != HELPER_SHA256:
        sys.exit(f"aws_signing_helper のハッシュが公式の値と一致しません: {actual}")
    HELPER.write_bytes(body)
    print("aws_signing_helper を取得しました(ハッシュ確認済み)")


def pyoxigraph_wheel():
    """Lambda(Python 3.13、arm64)用の pyoxigraph を取得する。"""
    pattern = f"pyoxigraph-{PYOXIGRAPH}-*aarch64*.whl"
    found = sorted(WHEEL_DIR.glob(pattern))
    if not found:
        WHEEL_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, "-m", "pip", "download", f"pyoxigraph=={PYOXIGRAPH}", "--no-deps",
                        "--platform", "manylinux_2_28_aarch64", "--platform", "manylinux2014_aarch64",
                        "--python-version", "3.13", "--implementation", "cp", "--only-binary=:all:",
                        "-d", str(WHEEL_DIR), "-q"], check=True)
        found = sorted(WHEEL_DIR.glob(pattern))
    if not found:
        sys.exit(f"pyoxigraph {PYOXIGRAPH} の arm64 用の配布物が見つかりません")
    return found[-1]


def build_lambda_zip():
    """コードと pyoxigraph を zip にする。内容が同じなら同じハッシュになる。"""
    buffer, digest = io.BytesIO(), hashlib.sha256()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as out:
        def add(name, data):
            digest.update(name.encode() + b"\0" + data)
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            out.writestr(info, data, zipfile.ZIP_DEFLATED)
        for name, path in LAMBDA_FILES.items():
            add(name, path.read_bytes())
        with zipfile.ZipFile(pyoxigraph_wheel()) as wheel:
            for item in sorted(wheel.namelist()):
                if not item.endswith("/"):
                    add(item, wheel.read(item))
    return buffer.getvalue(), digest.hexdigest()[:16]


def upload_code(session, owner, account, region):
    """Lambda のコードを置く。バケットは Lambda の参照用で、公開はしない。"""
    s3 = session.client("s3")
    bucket = artifacts_bucket_name(owner, account)
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError:
        s3.create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": region})
        s3.put_public_access_block(Bucket=bucket, PublicAccessBlockConfiguration={
            "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
        s3.put_bucket_tagging(Bucket=bucket, Tagging={"TagSet": [{"Key": "Project", "Value": "xcp-factoryont"}, {"Key": "Owner", "Value": owner}]})
    body, code_hash = build_lambda_zip()
    key = f"lambda/updater-{code_hash}.zip"
    s3.put_object(Bucket=bucket, Key=key, Body=body)
    return bucket, key


def deploy(cfn, name, parameters):
    args = {"StackName": name, "TemplateBody": TEMPLATE.read_text(encoding="utf-8"), "Capabilities": ["CAPABILITY_NAMED_IAM"],
            "Parameters": [{"ParameterKey": k, "ParameterValue": v} for k, v in parameters.items()],
            "Tags": [{"Key": "Project", "Value": "xcp-factoryont"}, {"Key": "Owner", "Value": parameters["Owner"]}]}
    try:
        cfn.describe_stacks(StackName=name)
        exists = True
    except ClientError:
        exists = False
    try:
        if exists:
            cfn.update_stack(**args)
            waiter = "stack_update_complete"
        else:
            cfn.create_stack(OnFailure="DELETE", **args)
            waiter = "stack_create_complete"
    except ClientError as e:
        if "No updates are to be performed" not in str(e):
            raise
        return
    print(f"スタック {name} を{'更新' if exists else '作成'}しています")
    try:
        cfn.get_waiter(waiter).wait(StackName=name, WaiterConfig={"Delay": 5, "MaxAttempts": 120})
    except WaiterError:
        events = cfn.describe_stack_events(StackName=name)["StackEvents"][:10]
        for event in events:
            if "FAILED" in event["ResourceStatus"]:
                print(f"  {event['LogicalResourceId']}: {event.get('ResourceStatusReason')}")
        sys.exit("スタックの作成に失敗しました")


def write_client_config(outputs, key, cert, region):
    command = " ".join([f'"{HELPER}"', "credential-process", f'--certificate "{cert}"', f'--private-key "{key}"',
                        f"--trust-anchor-arn {outputs['TrustAnchorArn']}", f"--profile-arn {outputs['ProfileArn']}",
                        f"--role-arn {outputs['ExtractorRoleArn']}", f"--region {region}"])
    (CLIENT_DIR / "aws_config").write_text(f"[profile {PROFILE}]\ncredential_process = {command}\nregion = {region}\n", encoding="utf-8")
    (CLIENT_DIR / "extractor.json").write_text(json.dumps({"bucket": outputs["RawBucketName"], "region": region}), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(parser)
    args = parser.parse_args()
    name = stack_name(args.owner)
    ca_name, client_name = f"{name}-ca", f"{name}-extractor"
    ca_key, ca_cert = ensure_ca(ca_name)
    key, cert = ensure_client(client_name, ca_key, ca_cert)
    ensure_helper()
    session = boto3.Session(region_name=args.region)
    account = session.client("sts").get_caller_identity()["Account"]
    code_bucket, code_key = upload_code(session, args.owner, account, args.region)
    cfn = session.client("cloudformation")
    deploy(cfn, name, {"Owner": args.owner, "CaCertificate": ca_cert.decode(), "ClientCommonName": client_name, "CaCommonName": ca_name,
                       "CodeBucket": code_bucket, "CodeKey": code_key})
    stack = cfn.describe_stacks(StackName=name)["Stacks"][0]
    outputs = {o["OutputKey"]: o["OutputValue"] for o in stack["Outputs"]}
    write_client_config(outputs, key, cert, args.region)
    print(f"完了: s3://{outputs['RawBucketName']}/raw/ に送れます")


if __name__ == "__main__":
    main()
