"""
AWS 側の学習環境を削除するスクリプト(課金を止めたいとき・作り直したいとき)

  1. 証明書をモノとポリシーから外し、無効化して削除
  2. S3 バケットの中身を空にする(空でないとスタックを消せないため)
  3. CloudFormation スタックを削除
  4. ローカルの証明書と gateway_config.json を削除

実行: python aws/teardown_aws.py [--region ap-northeast-1] [--profile 名前]
"""
import argparse
import shutil
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
CERT_DIR = ROOT / "gateway" / "certs"
STACK_NAME = "iot-lab"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="ap-northeast-1")
    ap.add_argument("--profile", default=None)
    ap.add_argument("--yes", action="store_true", help="確認なしで削除")
    args = ap.parse_args()

    if not args.yes:
        ans = input(f"スタック {STACK_NAME} と保存データをすべて削除します。よろしいですか? [y/N] ")
        if ans.strip().lower() != "y":
            print("中止しました")
            return

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    cfn = session.client("cloudformation")
    iot = session.client("iot")

    outputs = {}
    try:
        stack = cfn.describe_stacks(StackName=STACK_NAME)["Stacks"][0]
        outputs = {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}
    except cfn.exceptions.ClientError:
        print("スタックは見つかりませんでした")

    cert_id_file = CERT_DIR / "cert_id.txt"
    if cert_id_file.exists():
        cert_id = cert_id_file.read_text().strip()
        arn = iot.describe_certificate(certificateId=cert_id)["certificateDescription"]["certificateArn"]
        print("[1/4] 証明書を削除中…")
        for thing in iot.list_principal_things(principal=arn).get("things", []):
            iot.detach_thing_principal(thingName=thing, principal=arn)
        for pol in iot.list_attached_policies(target=arn).get("policies", []):
            iot.detach_policy(policyName=pol["policyName"], target=arn)
        iot.update_certificate(certificateId=cert_id, newStatus="INACTIVE")
        iot.delete_certificate(certificateId=cert_id, forceDelete=True)

    if "BucketName" in outputs:
        print(f"[2/4] バケット {outputs['BucketName']} を空にしています…")
        session.resource("s3").Bucket(outputs["BucketName"]).objects.all().delete()

    if outputs:
        print("[3/4] スタックを削除中…(1〜3分)")
        cfn.delete_stack(StackName=STACK_NAME)
        cfn.get_waiter("stack_delete_complete").wait(StackName=STACK_NAME)

    print("[4/4] ローカルの証明書と設定を削除")
    shutil.rmtree(CERT_DIR, ignore_errors=True)
    (ROOT / "gateway" / "gateway_config.json").unlink(missing_ok=True)
    print("削除完了")


if __name__ == "__main__":
    main()
