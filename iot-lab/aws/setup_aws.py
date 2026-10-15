"""
AWS 側の環境を作るスクリプト(Windows / Mac / Linux 共通)

  1. CloudFormation で S3・IoTポリシー・ルール・Athena 設定を作成
  2. ゲートウェイ用の X.509 証明書を発行し、ポリシーとモノ(Thing)に紐づけ
  3. AWS IoT Core のエンドポイントを調べ、gateway/gateway_config.json を自動作成
  4. Grafana用の最小権限アクセスキーを発行し、grafana/.env を自動作成

  (--email を付けると、アラーム通知メールの購読も登録)

リソースの名前はすべて xcp-store-iot-<owner>-… になります(命名のきまりは aws/naming.py)。
同じAWSアカウントをチームで共用するため、初回は --owner で自分の名前(英小文字・数字)を指定します。
2回目以降は gateway/gateway_config.json に保存された owner を使うので、省略できます。

Lambda のコードは aws/lambda/*.py が正本です。template.yaml の「# __CODE:ファイル名__」の行に
埋め込んでデプロイするので、コードを書き換えたらこのスクリプトを再実行すれば反映されます。

前提: AWS CLI で認証情報を設定済み(aws configure または aws sso login)
実行: python aws/setup_aws.py --owner kojima [--email you@example.com] [--region ap-northeast-1] [--profile 名前]
"""
import argparse
import json
import re
import urllib.request
from pathlib import Path

import boto3

import naming

ROOT = Path(__file__).resolve().parent.parent
GATEWAY_DIR = ROOT / "gateway"
CERT_DIR = GATEWAY_DIR / "certs"
GRAFANA_DIR = ROOT / "grafana"
ROOT_CA_URL = "https://www.amazontrust.com/repository/AmazonRootCA1.pem"
CODE_MARKER = re.compile(r"^( *)# __CODE:(\w+)__$", re.MULTILINE)
ZIPFILE_LIMIT = 4096  # CloudFormation のインラインコード(ZipFile)の上限文字数


def embed_code(m: re.Match) -> str:
    indent, name = m.group(1), m.group(2)
    code = (ROOT / "aws" / "lambda" / f"{name}.py").read_text(encoding="utf-8")
    if len(code) > ZIPFILE_LIMIT:
        raise SystemExit(f"aws/lambda/{name}.py が {len(code)} 文字あり、上限 {ZIPFILE_LIMIT} 文字を超えています")
    return "\n".join(indent + line if line else "" for line in code.splitlines())


def build_template() -> str:
    body = (ROOT / "aws" / "template.yaml").read_text(encoding="utf-8")
    return CODE_MARKER.sub(embed_code, body)


def deploy_stack(cfn, owner: str, gateway_id: str) -> dict:
    body = build_template()
    stack = naming.stack_name(owner)
    params = [{"ParameterKey": "Owner", "ParameterValue": owner},
              {"ParameterKey": "GatewayId", "ParameterValue": gateway_id}]
    # IAMロールなどに名前を付けているので CAPABILITY_NAMED_IAM が必要
    args = dict(StackName=stack, TemplateBody=body, Parameters=params, Tags=naming.tags(owner),
                Capabilities=["CAPABILITY_NAMED_IAM"])
    try:
        cfn.describe_stacks(StackName=stack)
        exists = True
    except cfn.exceptions.ClientError:
        exists = False

    if not exists:
        print(f"[1/4] スタック {stack} を作成中…(1〜3分)")
        cfn.create_stack(**args)
        cfn.get_waiter("stack_create_complete").wait(StackName=stack)
    else:
        print(f"[1/4] スタック {stack} は作成済み。変更があれば更新します")
        try:
            cfn.update_stack(**args)
            cfn.get_waiter("stack_update_complete").wait(StackName=stack)
        except cfn.exceptions.ClientError as e:
            if "No updates are to be performed" not in str(e):
                raise
    outputs = cfn.describe_stacks(StackName=stack)["Stacks"][0]["Outputs"]
    return {o["OutputKey"]: o["OutputValue"] for o in outputs}


def issue_certificate(iot, policy_name: str, thing_name: str) -> None:
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    if (CERT_DIR / "cert_id.txt").exists():
        print("[2/4] 証明書は発行済みのため再利用します")
        return
    print("[2/4] デバイス証明書を発行中…")
    res = iot.create_keys_and_certificate(setAsActive=True)
    (CERT_DIR / "device.pem.crt").write_text(res["certificatePem"])
    (CERT_DIR / "private.pem.key").write_text(res["keyPair"]["PrivateKey"])
    (CERT_DIR / "cert_id.txt").write_text(res["certificateId"])
    iot.attach_policy(policyName=policy_name, target=res["certificateArn"])
    iot.attach_thing_principal(thingName=thing_name, principal=res["certificateArn"])
    with urllib.request.urlopen(ROOT_CA_URL) as r:
        (CERT_DIR / "AmazonRootCA1.pem").write_bytes(r.read())
    print("      秘密鍵は gateway/certs/private.pem.key に保存しました。他人に渡さないでください")


def write_gateway_config(iot, owner: str, gateway_id: str, outputs: dict, region: str) -> None:
    endpoint = iot.describe_endpoint(endpointType="iot:Data-ATS")["endpointAddress"]
    cfg = json.loads((GATEWAY_DIR / "gateway_config.example.json").read_text(encoding="utf-8"))
    cfg["owner"] = owner
    cfg["gateway_id"] = gateway_id
    cfg["mqtt"]["host"] = endpoint
    cfg["mqtt"]["client_id"] = outputs["ThingName"]   # IoTポリシーで、モノの名前と同じclient IDだけ接続を許可している
    cfg["mqtt"]["topic_prefix"] = outputs["TopicPrefix"]
    # ファイル連携(ハンズオン7):証明書で一時的な認証情報をもらう先と、アップロード先
    cfg["aws"] = {
        "region": region,
        "bucket": outputs["BucketName"],
        "credentials_endpoint": iot.describe_endpoint(endpointType="iot:CredentialProvider")["endpointAddress"],
        "role_alias": outputs["FilesRoleAliasName"],
    }
    (GATEWAY_DIR / "gateway_config.json").write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[3/4] gateway/gateway_config.json を作成しました(エンドポイント: {endpoint})")


def write_grafana_env(iam, region: str, outputs: dict) -> None:
    env_file = GRAFANA_DIR / ".env"
    if env_file.exists():
        print("[4/4] grafana/.env は作成済みのため再利用します")
        return
    print("[4/4] Grafana用アクセスキーを発行中…")
    res = iam.create_access_key(UserName=outputs["GrafanaUserName"])["AccessKey"]
    GRAFANA_DIR.mkdir(parents=True, exist_ok=True)
    env_file.write_text(
        f"AWS_REGION={region}\n"
        f"AWS_ACCESS_KEY_ID={res['AccessKeyId']}\n"
        f"AWS_SECRET_ACCESS_KEY={res['SecretAccessKey']}\n"
        # データソースの接続先(名前に owner が入るので、provisioning の YAML に直接書かずここで渡す)
        f"ATHENA_WORKGROUP={outputs['AthenaWorkGroup']}\n"
        f"ATHENA_DATABASE={outputs['GlueDatabaseName']}\n",
        encoding="utf-8",
    )
    print("      秘密鍵と同様に扱ってください。grafana/.env を他人に渡さないでください")


def subscribe_email(sns, topic_arn: str, email: str) -> None:
    subs = sns.list_subscriptions_by_topic(TopicArn=topic_arn)["Subscriptions"]
    if any(s["Endpoint"] == email for s in subs):
        print(f"      通知先 {email} は登録済みです")
        return
    sns.subscribe(TopicArn=topic_arn, Protocol="email", Endpoint=email)
    print(f"      通知先 {email} を登録しました。届いた確認メールの「Confirm subscription」を押してください")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="ap-northeast-1")
    ap.add_argument("--profile", default=None)
    ap.add_argument("--owner", default=None, help="自分を表す名前(英小文字・数字、16文字以内)。リソース名に入る")
    ap.add_argument("--gateway-id", default="store01", help="店舗ゲートウェイのID")
    ap.add_argument("--email", default=None, help="アラーム通知を受け取るメールアドレス(ハンズオン4)")
    args = ap.parse_args()
    owner = naming.resolve_owner(args.owner)

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    account = session.client("sts").get_caller_identity()["Account"]
    print(f"AWSアカウント {account} / リージョン {args.region} に {naming.stack_name(owner)} を作成します")

    outputs = deploy_stack(session.client("cloudformation"), owner, args.gateway_id)
    iot = session.client("iot")
    issue_certificate(iot, outputs["PolicyName"], outputs["ThingName"])
    write_gateway_config(iot, owner, args.gateway_id, outputs, args.region)
    write_grafana_env(session.client("iam"), args.region, outputs)
    if args.email:
        subscribe_email(session.client("sns"), outputs["AlarmTopicArn"], args.email)

    print("\n完了。次は: python gateway/gateway.py")
    print(f"MQTTトピック: {outputs.get('TopicPrefix')}/{args.gateway_id}/<機器>/telemetry")
    print(f"データ保存先: s3://{outputs.get('BucketName')}/raw/")
    print(f"Athena: ワークグループ {outputs.get('AthenaWorkGroup')} / データベース {outputs.get('GlueDatabaseName')}")
    print(f"最新値テーブル: {outputs.get('LatestTableName')}   確認: python aws/show_latest.py")
    print(f"Lambdaのログ: aws logs tail {outputs.get('LatestValueLogGroup')} --follow")
    print(f"通知Lambdaのログ: aws logs tail {outputs.get('AlarmNotifierLogGroup')} --follow")
    print("Grafanaで見る場合: cd grafana && docker compose up -d → http://localhost:3000")


if __name__ == "__main__":
    main()
