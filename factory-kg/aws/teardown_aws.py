"""KG の AWS 環境をすべて削除し、残っているものがないか確認する（replay の環境には触れない）。

    python aws/teardown_aws.py --owner kojima
    python aws/teardown_aws.py --owner kojima --check     # 削除せず確認だけ
"""

import argparse
import sys

import boto3
from botocore.exceptions import ClientError, WaiterError

from kgnaming import add_common_args, artifacts_bucket_name, stack_name


def empty_bucket(s3, bucket):
    """バージョニングしているバケットを、古い版・削除マーカーも含めて空にする。"""
    deleted = 0
    for page in s3.get_paginator("list_object_versions").paginate(Bucket=bucket):
        objects = [{"Key": o["Key"], "VersionId": o["VersionId"]}
                   for o in page.get("Versions", []) + page.get("DeleteMarkers", [])]
        if objects:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": objects, "Quiet": True})
            deleted += len(objects)
    print(f"バケット {bucket} を空にしました（{deleted} 件）")


def missing(call, *codes):
    try:
        call()
        return False
    except ClientError as e:
        if e.response["Error"]["Code"] in codes or "does not exist" in e.response["Error"]["Message"]:
            return True
        raise


def leftovers(session, owner, account):
    name = stack_name(owner)
    cfn, s3, lam, logs = (session.client(s) for s in ("cloudformation", "s3", "lambda", "logs"))
    found = []
    if not missing(lambda: cfn.describe_stacks(StackName=name), "ValidationError"):
        found.append(("CloudFormation スタック", name))
    for bucket in (f"{name}-graph-{account}", artifacts_bucket_name(owner, account)):
        if not missing(lambda: s3.head_bucket(Bucket=bucket), "404", "NoSuchBucket"):
            found.append(("S3 バケット", bucket))
    for fn in ("sparql", "api"):
        if not missing(lambda: lam.get_function(FunctionName=f"{name}-{fn}"), "ResourceNotFoundException"):
            found.append(("Lambda", f"{name}-{fn}"))
    for group in logs.describe_log_groups(logGroupNamePrefix=f"/aws/lambda/{name}-")["logGroups"]:
        found.append(("CloudWatch Logs", group["logGroupName"]))
    return found


def main():
    ap = argparse.ArgumentParser(description="KG の AWS 環境を削除し、残っているものを確認する")
    add_common_args(ap)
    ap.add_argument("--yes", action="store_true", help="確認せずに削除する")
    ap.add_argument("--check", action="store_true", help="削除せず、残っているリソースを確認するだけ")
    args = ap.parse_args()

    session = boto3.Session(region_name=args.region)
    account = session.client("sts").get_caller_identity()["Account"]
    name = stack_name(args.owner)
    s3 = session.client("s3")

    if not args.check:
        if not args.yes:
            answer = input(f"{name} の KG 環境（公開中のグラフを含む）を削除します。replay には触れません。よろしいですか？ [yes/N] ")
            if answer.strip().lower() != "yes":
                print("中止しました")
                return
        cfn = session.client("cloudformation")
        try:
            outputs = {o["OutputKey"]: o["OutputValue"]
                       for o in cfn.describe_stacks(StackName=name)["Stacks"][0].get("Outputs", [])}
        except ClientError:
            outputs = None
            print(f"スタック {name} はありません")
        if outputs is not None:
            if "GraphBucket" in outputs:
                try:
                    empty_bucket(s3, outputs["GraphBucket"])
                except ClientError as e:
                    if e.response["Error"]["Code"] != "NoSuchBucket":
                        raise
            print(f"スタック {name} を削除しています（数分）...")
            cfn.delete_stack(StackName=name)
            try:
                cfn.get_waiter("stack_delete_complete").wait(StackName=name, WaiterConfig={"Delay": 10, "MaxAttempts": 90})
            except WaiterError:
                print("削除に失敗しました。コンソールの CloudFormation > イベントで原因を確認してください", file=sys.stderr)
                sys.exit(1)
            print(f"スタック {name} を削除しました")
        code_bucket = artifacts_bucket_name(args.owner, account)
        try:
            empty_bucket(s3, code_bucket)
            s3.delete_bucket(Bucket=code_bucket)
            print(f"バケット {code_bucket} を削除しました")
        except ClientError as e:
            if e.response["Error"]["Code"] not in ("NoSuchBucket", "404"):
                raise

    print()
    print(f"残っているリソースを確認しています（{name}）...")
    found = leftovers(session, args.owner, account)
    if not found:
        print("残っているリソースはありません（課金されるものは残っていません）")
        return
    for kind, item in found:
        print(f"  残っています: {kind:<24} {item}")
    sys.exit(1)


if __name__ == "__main__":
    main()
