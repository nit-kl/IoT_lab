"""AWS の環境(S3、IAM Roles Anywhere)を作成・更新し、オンプレ側の認証設定を書き出す。

    python cloud/setup_aws.py --owner kojima

何度実行しても同じ結果になる。証明書は 30 日を切っていたら作り直す。
作るファイル: cloud/.secrets(CA の秘密鍵)、onprem/.secrets(抽出プログラムの証明書、秘密鍵、AWS の設定)。どちらも Git に入らない。
"""

import argparse
import hashlib
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
from botocore.exceptions import ClientError, WaiterError
from cryptography import x509

from certs import create_ca, create_client
from naming import add_common_args, stack_name

HERE = Path(__file__).resolve().parent
ONPREM = HERE.parent / "onprem"
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
    cfn = boto3.Session(region_name=args.region).client("cloudformation")
    deploy(cfn, name, {"Owner": args.owner, "CaCertificate": ca_cert.decode(), "ClientCommonName": client_name, "CaCommonName": ca_name})
    stack = cfn.describe_stacks(StackName=name)["Stacks"][0]
    outputs = {o["OutputKey"]: o["OutputValue"] for o in stack["Outputs"]}
    write_client_config(outputs, key, cert, args.region)
    print(f"完了: s3://{outputs['RawBucketName']}/raw/ に送れます")


if __name__ == "__main__":
    main()
