"""AWS 環境を作成（または更新）し、テーブルを作る。

    python aws/setup_aws.py --owner kojima
    python aws/setup_aws.py --owner kojima --max-acu 2

初回は Aurora の作成に 10〜15 分ほどかかる。何度実行しても同じ状態になる。
"""

import argparse
import hashlib
import io
import sys
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError, WaiterError

from dataapi import DataApi
from naming import PROJECT, add_common_args, artifacts_bucket_name, stack_name

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "template.yaml"
SCHEMA = HERE / "schema.sql"
# Lambda の zip に入れるファイル（すべて zip 直下に置く）
LAMBDA_FILES = sorted((HERE / "lambda").glob("*.py")) + [HERE / "dataapi.py"]


def build_lambda_zip():
    """Lambda コードの zip とキーを返す。内容が同じなら同じキーになる（変わったときだけ Lambda が更新される）。"""
    buf, digest = io.BytesIO(), hashlib.sha256()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path in LAMBDA_FILES:
            data = path.read_bytes()
            digest.update(path.name.encode() + b"\0" + data)
            info = zipfile.ZipInfo(path.name, date_time=(2020, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            z.writestr(info, data, zipfile.ZIP_DEFLATED)
    return buf.getvalue(), f"lambda/{digest.hexdigest()[:16]}.zip"


def upload_lambda_code(s3, bucket, region, owner):
    """スタック外のアーティファクト用バケットに Lambda コードを置く（なければ作る）。"""
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError:
        config = {} if region == "us-east-1" else {"CreateBucketConfiguration": {"LocationConstraint": region}}
        s3.create_bucket(Bucket=bucket, **config)
        s3.put_public_access_block(Bucket=bucket, PublicAccessBlockConfiguration={
            "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
        s3.put_bucket_tagging(Bucket=bucket, Tagging={"TagSet": [
            {"Key": "Project", "Value": PROJECT}, {"Key": "Owner", "Value": owner}]})
        print(f"Lambda コード用バケット {bucket} を作成しました")
    body, key = build_lambda_zip()
    s3.put_object(Bucket=bucket, Key=key, Body=body)
    return key


def deploy_stack(cfn, name, owner, parameters):
    """スタックを作成または更新して完了を待つ。"""
    request = {
        "StackName": name,
        "TemplateBody": TEMPLATE.read_text(encoding="utf-8"),
        "Parameters": [{"ParameterKey": k, "ParameterValue": str(v)} for k, v in parameters.items()],
        "Capabilities": ["CAPABILITY_IAM"],
        "Tags": [{"Key": "Project", "Value": PROJECT}, {"Key": "Owner", "Value": owner}],
    }
    try:
        cfn.describe_stacks(StackName=name)
        exists = True
    except ClientError as e:
        if "does not exist" not in e.response["Error"]["Message"]:
            raise
        exists = False

    if exists:
        try:
            cfn.update_stack(**request)
        except ClientError as e:
            if "No updates are to be performed" in e.response["Error"]["Message"]:
                print("スタックに変更はありません")
                return
            raise
        waiter, action = cfn.get_waiter("stack_update_complete"), "更新"
    else:
        cfn.create_stack(**request, OnFailure="ROLLBACK")
        waiter, action = cfn.get_waiter("stack_create_complete"), "作成"

    print(f"スタック {name} を{action}しています（初回は 10〜15 分ほどかかります）...")
    try:
        waiter.wait(StackName=name, WaiterConfig={"Delay": 20, "MaxAttempts": 90})
    except WaiterError:
        print(f"スタックの{action}に失敗しました。原因はコンソールの CloudFormation > イベント、または次で確認できます:", file=sys.stderr)
        print(f"  aws cloudformation describe-events --stack-name {name}", file=sys.stderr)
        sys.exit(1)
    print(f"スタックの{action}が完了しました")


def stack_outputs(cfn, name):
    stack = cfn.describe_stacks(StackName=name)["Stacks"][0]
    return {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}


def schema_statements():
    """schema.sql を 1 文ずつに分ける。コメント行は除き、$$ 〜 $$（関数本体）の中の ; では区切らない。"""
    lines = [l for l in SCHEMA.read_text(encoding="utf-8").splitlines() if not l.lstrip().startswith("--")]
    statements, current = [], []
    for i, part in enumerate("\n".join(lines).split("$$")):
        if i % 2:  # $$ の内側
            current.append(f"$${part}$$")
            continue
        *done, rest = part.split(";")
        for piece in done:
            statements.append("".join(current) + piece)
            current = []
        current.append(rest)
    statements.append("".join(current))
    return [s.strip() for s in statements if s.strip()]


def apply_schema(db):
    print("DB に接続しています（一時停止中なら再開まで数十秒かかります）...")
    db.wake(resume_wait_sec=300)
    for sql in schema_statements():
        db.execute(sql)
    print("テーブルと関数を作成しました: staging_readings, readings, replay_state, start_replay(), reset_replay()")


def main():
    ap = argparse.ArgumentParser(description="xcp-factory の AWS 環境を作成・更新する")
    add_common_args(ap)
    ap.add_argument("--max-acu", type=int, choices=[1, 2, 4, 8], default=4, help="Aurora の最大 ACU（既定 4）")
    ap.add_argument("--auto-pause", type=int, default=300, help="Aurora が一時停止するまでの無接続秒数（300〜86400、既定 300）")
    ap.add_argument("--vpc-cidr", default="10.80.0.0/16", help="専用 VPC の CIDR（既定 10.80.0.0/16）")
    args = ap.parse_args()

    name = stack_name(args.owner)
    account = boto3.client("sts", region_name=args.region).get_caller_identity()["Account"]
    code_bucket = artifacts_bucket_name(args.owner, account)
    code_key = upload_lambda_code(boto3.client("s3", region_name=args.region), code_bucket, args.region, args.owner)

    cfn = boto3.client("cloudformation", region_name=args.region)
    deploy_stack(cfn, name, args.owner, {
        "MaxAcu": args.max_acu,
        "AutoPauseSeconds": args.auto_pause,
        "VpcCidr": args.vpc_cidr,
        "CodeBucket": code_bucket,
        "CodeKey": code_key,
    })

    out = stack_outputs(cfn, name)
    db = DataApi(out["ClusterArn"], out["SecretArn"], out["DatabaseName"], region=args.region)
    apply_schema(db)

    print()
    print(f"スタック名 : {name}")
    print(f"バケット   : {out['BucketName']}")
    print(f"クラスター : {out['ClusterArn']}")
    print(f"HTTP API   : {out['ApiUrl']}")
    print()
    print("CSV をアップロードすると反映が始まります:")
    print(f"  aws s3 cp data/factory_sample.csv s3://{out['BucketName']}/incoming/")


if __name__ == "__main__":
    main()
