"""AWS の環境をすべて削除し、残っているものがないか確認する。

    python cloud/teardown_aws.py --owner kojima
    python cloud/teardown_aws.py --owner kojima --check     # 削除せず確認だけ
"""

import argparse
import sys

import boto3
from botocore.exceptions import ClientError, WaiterError

from naming import add_common_args, artifacts_bucket_name, stack_name


def empty_bucket(s3, bucket):
    deleted = 0
    for page in s3.get_paginator("list_object_versions").paginate(Bucket=bucket):
        objects = [{"Key": o["Key"], "VersionId": o["VersionId"]} for o in page.get("Versions", []) + page.get("DeleteMarkers", [])]
        if objects:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": objects, "Quiet": True})
            deleted += len(objects)
    print(f"バケット {bucket} を空にしました({deleted} 件)")


def bucket_exists(s3, bucket):
    try:
        s3.head_bucket(Bucket=bucket)
        return True
    except ClientError:
        return False


def leftovers(session, name, account, owner):
    cfn, s3, iam, ra, lam, logs, ssm, ddb = (session.client(s) for s in ("cloudformation", "s3", "iam", "rolesanywhere", "lambda", "logs", "ssm", "dynamodb"))
    found = []
    try:
        cfn.describe_stacks(StackName=name)
        found.append(("CloudFormation スタック", name))
    except ClientError:
        pass
    for bucket in (f"{name}-raw-{account}", artifacts_bucket_name(owner, account)):
        if bucket_exists(s3, bucket):
            found.append(("S3 バケット", bucket))
    for role in (f"{name}-extractor", f"{name}-updater", f"{name}-notifier", f"{name}-ack"):
        try:
            iam.get_role(RoleName=role)
            found.append(("IAM ロール", role))
        except ClientError:
            pass
    for function in ("updater", "notifier", "ack"):
        try:
            lam.get_function(FunctionName=f"{name}-{function}")
            found.append(("Lambda", f"{name}-{function}"))
        except ClientError:
            pass
    try:
        ddb.describe_table(TableName=f"{name}-notify")
        found.append(("DynamoDB", f"{name}-notify"))
    except ClientError:
        pass
    try:
        ssm.get_parameter(Name=f"/{name}/ack-secret")
        found.append(("Parameter Store", f"/{name}/ack-secret"))
    except ClientError:
        pass
    found += [("ロググループ", g["logGroupName"]) for g in logs.describe_log_groups(logGroupNamePrefix=f"/aws/lambda/{name}-")["logGroups"]]
    found += [("トラストアンカー", t["name"]) for t in ra.list_trust_anchors()["trustAnchors"] if t["name"] == name]
    found += [("プロファイル", p["name"]) for p in ra.list_profiles()["profiles"] if p["name"] == name]
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(parser)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    session = boto3.Session(region_name=args.region)
    account = session.client("sts").get_caller_identity()["Account"]
    name = stack_name(args.owner)
    if not args.check:
        s3, cfn = session.client("s3"), session.client("cloudformation")
        bucket = f"{name}-raw-{account}"
        if bucket_exists(s3, bucket):
            empty_bucket(s3, bucket)
        cfn.delete_stack(StackName=name)
        try:
            cfn.get_waiter("stack_delete_complete").wait(StackName=name, WaiterConfig={"Delay": 5, "MaxAttempts": 120})
        except WaiterError as e:
            sys.exit(f"スタックの削除に失敗しました: {e}")
        try:
            session.client("ssm").delete_parameter(Name=f"/{name}/ack-secret")
        except ClientError:
            pass
        artifacts = artifacts_bucket_name(args.owner, account)
        if bucket_exists(s3, artifacts):
            empty_bucket(s3, artifacts)
            s3.delete_bucket(Bucket=artifacts)
    found = leftovers(session, name, account, args.owner)
    for kind, item in found:
        print(f"残っています: {kind} {item}")
    print("残存なし" if not found else f"{len(found)} 件残っています")
    sys.exit(1 if found else 0)


if __name__ == "__main__":
    main()
