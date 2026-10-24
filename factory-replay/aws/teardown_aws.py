"""AWS 環境をすべて削除し、課金されるものが残っていないか確認する。

    python aws/teardown_aws.py --owner kojima            # 削除して残りを確認
    python aws/teardown_aws.py --owner kojima --check    # 削除せず、残っているものを確認するだけ

DB の中身と S3 の CSV は消える（スナップショットも残さない）。
"""

import argparse
import sys

import boto3
from botocore.exceptions import ClientError, WaiterError

from naming import add_common_args, artifacts_bucket_name, stack_name

FUNCTIONS = ("loader", "replayer", "api")


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


def delete_stack(session, name, assume_yes):
    """スタックを削除する。スタックがなければ何もしない。中止したら False。"""
    cfn = session.client("cloudformation")
    try:
        stack = cfn.describe_stacks(StackName=name)["Stacks"][0]
    except ClientError as e:
        if "does not exist" in e.response["Error"]["Message"]:
            print(f"スタック {name} はありません")
            return True
        raise

    if not assume_yes:
        answer = input(f"スタック {name} と、その DB・S3 のデータをすべて削除します。よろしいですか？ [yes/N] ")
        if answer.strip().lower() != "yes":
            print("中止しました")
            return False

    outputs = {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}
    if "BucketName" in outputs:
        try:
            empty_bucket(session.client("s3"), outputs["BucketName"])
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
    print(f"スタック {name} を削除しました")
    return True


def delete_code_bucket(s3, bucket):
    """スタック外で作った Lambda コード用バケットを削除する。"""
    try:
        empty_bucket(s3, bucket)
        s3.delete_bucket(Bucket=bucket)
        print(f"バケット {bucket} を削除しました")
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("NoSuchBucket", "404"):
            raise


def exists(call, *missing_codes):
    """call() が成功すれば True、指定のエラーコード（見つからない）なら False。"""
    try:
        call()
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] in missing_codes or "does not exist" in e.response["Error"]["Message"]:
            return False
        raise


def leftovers(session, owner, account):
    """xcp-factory-<owner> のリソースで残っているものを [(種類, 名前)] で返す。"""
    name = stack_name(owner)
    cluster_arn = f"arn:aws:rds:{session.region_name}:{account}:cluster:{name}"
    cfn, s3, rds = session.client("cloudformation"), session.client("s3"), session.client("rds")
    lam, sch, logs = session.client("lambda"), session.client("scheduler"), session.client("logs")
    ec2, sm = session.client("ec2"), session.client("secretsmanager")
    found = []

    if exists(lambda: cfn.describe_stacks(StackName=name), "ValidationError"):
        found.append(("CloudFormation スタック", name))
    for bucket in (f"{name}-data-{account}", artifacts_bucket_name(owner, account)):
        if exists(lambda: s3.head_bucket(Bucket=bucket), "404", "NoSuchBucket"):
            found.append(("S3 バケット", bucket))
    if exists(lambda: rds.describe_db_clusters(DBClusterIdentifier=name), "DBClusterNotFoundFault"):
        found.append(("Aurora クラスター", name))
    else:
        # クラスター削除後に残ったスナップショット・自動バックアップ（クラスターがある間は本体の一部なので数えない）
        for snap in rds.describe_db_cluster_snapshots(DBClusterIdentifier=name)["DBClusterSnapshots"]:
            found.append(("Aurora スナップショット", snap["DBClusterSnapshotIdentifier"]))
        try:
            backups = rds.describe_db_cluster_automated_backups(DBClusterIdentifier=name)["DBClusterAutomatedBackups"]
        except ClientError as e:
            if e.response["Error"]["Code"] != "DBClusterAutomatedBackupNotFoundFault":
                raise
            backups = []
        for backup in backups:
            found.append(("Aurora 保持された自動バックアップ", backup.get("DbClusterResourceId", name)))
    for fn in FUNCTIONS:
        if exists(lambda: lam.get_function(FunctionName=f"{name}-{fn}"), "ResourceNotFoundException"):
            found.append(("Lambda", f"{name}-{fn}"))
    if exists(lambda: sch.get_schedule(Name=f"{name}-replayer"), "ResourceNotFoundException"):
        found.append(("EventBridge スケジュール", f"{name}-replayer"))
    for group in logs.describe_log_groups(logGroupNamePrefix=f"/aws/lambda/{name}-")["logGroups"]:
        found.append(("CloudWatch Logs", group["logGroupName"]))
    for vpc in ec2.describe_vpcs(Filters=[{"Name": "tag:Name", "Values": [name]}])["Vpcs"]:
        found.append(("VPC", vpc["VpcId"]))
    for secret in sm.list_secrets(Filters=[{"Key": "tag-value", "Values": [cluster_arn]}])["SecretList"]:
        found.append(("Secrets Manager", secret["Name"]))
    return found


def main():
    ap = argparse.ArgumentParser(description="xcp-factory の AWS 環境を削除し、残っているものを確認する")
    add_common_args(ap)
    ap.add_argument("--yes", action="store_true", help="確認せずに削除する")
    ap.add_argument("--check", action="store_true", help="削除せず、残っているリソースを確認するだけ")
    args = ap.parse_args()

    session = boto3.Session(region_name=args.region)
    account = session.client("sts").get_caller_identity()["Account"]
    if not args.check:
        if not delete_stack(session, stack_name(args.owner), args.yes):
            return
        delete_code_bucket(session.client("s3"), artifacts_bucket_name(args.owner, account))

    print()
    print(f"残っているリソースを確認しています（{stack_name(args.owner)}）...")
    found = leftovers(session, args.owner, account)
    if not found:
        print("残っているリソースはありません（課金されるものは残っていません）")
        return
    for kind, name in found:
        print(f"  残っています: {kind:<28} {name}")
    if not args.check:
        print("削除直後は反映待ちのことがあります。数分後に --check で再確認し、それでも残っていれば手動で削除してください")
    sys.exit(1)


if __name__ == "__main__":
    main()
