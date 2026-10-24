"""AWS 環境をすべて削除する（バケットを空にしてからスタックを削除）。

    python aws/teardown_aws.py --owner kojima

DB の中身と S3 の CSV は消える（スナップショットも残さない）。
"""

import argparse
import sys

import boto3
from botocore.exceptions import ClientError, WaiterError

from naming import add_common_args, stack_name


def empty_bucket(s3, bucket):
    deleted = 0
    paginator = s3.get_paginator("list_object_versions")
    for page in paginator.paginate(Bucket=bucket):
        objects = [{"Key": o["Key"], "VersionId": o["VersionId"]}
                   for o in page.get("Versions", []) + page.get("DeleteMarkers", [])]
        if objects:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": objects, "Quiet": True})
            deleted += len(objects)
    print(f"バケット {bucket} を空にしました（{deleted} 件）")


def main():
    ap = argparse.ArgumentParser(description="xcp-factory の AWS 環境を削除する")
    add_common_args(ap)
    ap.add_argument("--yes", action="store_true", help="確認せずに削除する")
    args = ap.parse_args()

    name = stack_name(args.owner)
    cfn = boto3.client("cloudformation", region_name=args.region)
    try:
        stack = cfn.describe_stacks(StackName=name)["Stacks"][0]
    except ClientError as e:
        if "does not exist" in e.response["Error"]["Message"]:
            print(f"スタック {name} はありません")
            return
        raise

    if not args.yes:
        answer = input(f"スタック {name} と、その DB・S3 のデータをすべて削除します。よろしいですか？ [yes/N] ")
        if answer.strip().lower() != "yes":
            print("中止しました")
            return

    outputs = {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}
    if "BucketName" in outputs:
        s3 = boto3.client("s3", region_name=args.region)
        try:
            empty_bucket(s3, outputs["BucketName"])
        except ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchBucket":
                raise

    print(f"スタック {name} を削除しています（10 分ほどかかります）...")
    cfn.delete_stack(StackName=name)
    try:
        cfn.get_waiter("stack_delete_complete").wait(StackName=name, WaiterConfig={"Delay": 20, "MaxAttempts": 90})
    except WaiterError:
        print("削除に失敗しました。コンソールの CloudFormation > イベントで原因を確認してください", file=sys.stderr)
        sys.exit(1)
    print("削除が完了しました")


if __name__ == "__main__":
    main()
