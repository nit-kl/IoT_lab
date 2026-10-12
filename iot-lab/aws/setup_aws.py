"""
AWS 側の環境を作るスクリプト(Windows / Mac / Linux 共通)

  1. CloudFormation で S3・IoTポリシー・ルール・Athena 設定を作成
  2. ゲートウェイ用の X.509 証明書を発行し、ポリシーとモノ(Thing)に紐づけ
  3. AWS IoT Core のエンドポイントを調べ、gateway/gateway_config.json を自動作成
  4. Grafana用の最小権限アクセスキーを発行し、grafana/.env を自動作成

前提: AWS CLI で認証情報を設定済み(aws configure または aws sso login)
実行: python aws/setup_aws.py [--region ap-northeast-1] [--profile 名前]
"""
import argparse
import json
import urllib.request
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
GATEWAY_DIR = ROOT / "gateway"
CERT_DIR = GATEWAY_DIR / "certs"
GRAFANA_DIR = ROOT / "grafana"
STACK_NAME = "iot-lab"
ROOT_CA_URL = "https://www.amazontrust.com/repository/AmazonRootCA1.pem"


def deploy_stack(cfn, gateway_id: str) -> dict:
    body = (ROOT / "aws" / "template.yaml").read_text(encoding="utf-8")
    params = [{"ParameterKey": "GatewayId", "ParameterValue": gateway_id}]
    try:
        cfn.describe_stacks(StackName=STACK_NAME)
        exists = True
    except cfn.exceptions.ClientError:
        exists = False

    if not exists:
        print(f"[1/3] スタック {STACK_NAME} を作成中…(1〜3分)")
        cfn.create_stack(StackName=STACK_NAME, TemplateBody=body, Parameters=params,
                         Capabilities=["CAPABILITY_IAM"])
        cfn.get_waiter("stack_create_complete").wait(StackName=STACK_NAME)
    else:
        print(f"[1/3] スタック {STACK_NAME} は作成済み。変更があれば更新します")
        try:
            cfn.update_stack(StackName=STACK_NAME, TemplateBody=body, Parameters=params,
                             Capabilities=["CAPABILITY_IAM"])
            cfn.get_waiter("stack_update_complete").wait(StackName=STACK_NAME)
        except cfn.exceptions.ClientError as e:
            if "No updates are to be performed" not in str(e):
                raise
    outputs = cfn.describe_stacks(StackName=STACK_NAME)["Stacks"][0]["Outputs"]
    return {o["OutputKey"]: o["OutputValue"] for o in outputs}


def issue_certificate(iot, policy_name: str, thing_name: str) -> None:
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    if (CERT_DIR / "cert_id.txt").exists():
        print("[2/3] 証明書は発行済みのため再利用します")
        return
    print("[2/3] デバイス証明書を発行中…")
    res = iot.create_keys_and_certificate(setAsActive=True)
    (CERT_DIR / "device.pem.crt").write_text(res["certificatePem"])
    (CERT_DIR / "private.pem.key").write_text(res["keyPair"]["PrivateKey"])
    (CERT_DIR / "cert_id.txt").write_text(res["certificateId"])
    iot.attach_policy(policyName=policy_name, target=res["certificateArn"])
    iot.attach_thing_principal(thingName=thing_name, principal=res["certificateArn"])
    with urllib.request.urlopen(ROOT_CA_URL) as r:
        (CERT_DIR / "AmazonRootCA1.pem").write_bytes(r.read())
    print("      秘密鍵は gateway/certs/private.pem.key に保存しました。他人に渡さないでください")


def write_gateway_config(iot, gateway_id: str) -> None:
    endpoint = iot.describe_endpoint(endpointType="iot:Data-ATS")["endpointAddress"]
    cfg = json.loads((GATEWAY_DIR / "gateway_config.example.json").read_text(encoding="utf-8"))
    cfg["gateway_id"] = gateway_id
    cfg["mqtt"]["host"] = endpoint
    cfg["mqtt"]["client_id"] = gateway_id
    (GATEWAY_DIR / "gateway_config.json").write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[3/4] gateway/gateway_config.json を作成しました(エンドポイント: {endpoint})")


def write_grafana_env(iam, region: str, grafana_user: str) -> None:
    env_file = GRAFANA_DIR / ".env"
    if env_file.exists():
        print("[4/4] grafana/.env は作成済みのため再利用します")
        return
    print("[4/4] Grafana用アクセスキーを発行中…")
    res = iam.create_access_key(UserName=grafana_user)["AccessKey"]
    GRAFANA_DIR.mkdir(parents=True, exist_ok=True)
    env_file.write_text(
        "AWS_REGION={}\nAWS_ACCESS_KEY_ID={}\nAWS_SECRET_ACCESS_KEY={}\n".format(
            region, res["AccessKeyId"], res["SecretAccessKey"]
        ),
        encoding="utf-8",
    )
    print("      秘密鍵と同様に扱ってください。grafana/.env を他人に渡さないでください")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="ap-northeast-1")
    ap.add_argument("--profile", default=None)
    ap.add_argument("--gateway-id", default="gw01")
    args = ap.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    account = session.client("sts").get_caller_identity()["Account"]
    print(f"AWSアカウント {account} / リージョン {args.region} に作成します")

    outputs = deploy_stack(session.client("cloudformation"), args.gateway_id)
    iot = session.client("iot")
    issue_certificate(iot, f"{args.gateway_id}-policy", args.gateway_id)
    write_gateway_config(iot, args.gateway_id)
    write_grafana_env(session.client("iam"), args.region, outputs["GrafanaUserName"])

    print("\n完了。次は: python gateway/gateway.py")
    print(f"データ保存先: s3://{outputs.get('BucketName')}/raw/   Athenaワークグループ: iot-lab")
    print("Grafanaで見る場合: cd grafana && docker compose up -d → http://localhost:3000")


if __name__ == "__main__":
    main()
