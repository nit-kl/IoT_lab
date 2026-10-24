"""KG の AWS 環境（S3 + SPARQL Lambda + Function URL）を作成・更新し、グラフを生成して公開する。

    python aws/setup_aws.py --owner kojima
    python aws/setup_aws.py --owner kojima --memory 1024
    python aws/setup_aws.py --owner kojima --skip-publish     # 環境だけ作る（グラフは公開しない）

グラフの生成元: replay のスタック（xcp-factory-<owner>）があれば replay の API から（URL も最新に）、
なければ同梱のサンプル CSV から（URL は仮）。replay を作り直したときも、このコマンドの再実行で URL が更新される（懸念点 T1）。
"""

import argparse
import hashlib
import io
import subprocess
import sys
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError, WaiterError

from kgnaming import PROJECT, add_common_args, artifacts_bucket_name, replay_stack_name, stack_name

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TEMPLATE = HERE / "template.yaml"
# Lambda の zip に入れるコード（sparql・api の両 Lambda で共通の zip）
LAMBDA_FILES = [HERE / "lambda" / n for n in ("sparql.py", "api.py", "domain.py", "graphstore.py")]
WHEEL_DIR = ROOT / ".cache" / "wheels"
SAMPLE_CSV = ROOT.parent / "factory-replay" / "data" / "factory_sample.csv"

sys.path.insert(0, str(ROOT / "tools"))


def pyoxigraph_wheel():
    """Lambda（Python 3.13・arm64）用の pyoxigraph の配布物を取得する。版はローカルと同じにする。

    Lambda の python3.13 は Amazon Linux 2023（glibc 2.34）なので manylinux_2_28 の配布物が使える
    （0.5.2 以降は manylinux2014 の arm64 版がない）。
    """
    import pyoxigraph
    version = pyoxigraph.__version__
    pattern = f"pyoxigraph-{version}-cp313-*manylinux*aarch64*.whl"
    found = sorted(WHEEL_DIR.glob(pattern))
    if found:
        return found[-1]
    WHEEL_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "pip", "download", f"pyoxigraph=={version}", "--no-deps",
                    "--platform", "manylinux_2_28_aarch64", "--platform", "manylinux2014_aarch64",
                    "--python-version", "3.13", "--implementation", "cp",
                    "--only-binary=:all:", "-d", str(WHEEL_DIR), "-q"], check=True)
    found = sorted(WHEEL_DIR.glob(pattern))
    if not found:
        raise SystemExit(f"pyoxigraph {version} の arm64 用配布物が見つかりません")
    return found[-1]


def build_lambda_zip():
    """Lambda のコードと pyoxigraph を 1 つの zip にする。内容が同じなら同じキーになる。"""
    wheel = pyoxigraph_wheel()
    buf, digest = io.BytesIO(), hashlib.sha256()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        def add(name, data, mode=0o644):
            digest.update(name.encode() + b"\0" + data)
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.external_attr = mode << 16
            out.writestr(info, data, zipfile.ZIP_DEFLATED)
        for path in LAMBDA_FILES:
            add(path.name, path.read_bytes())
        with zipfile.ZipFile(wheel) as whl:
            for name in sorted(whl.namelist()):
                if not name.endswith("/"):
                    add(name, whl.read(name), 0o755 if name.endswith(".so") else 0o644)
    return buf.getvalue(), f"lambda/{digest.hexdigest()[:16]}.zip", wheel.name


def upload_lambda_code(s3, bucket, region, owner):
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
    body, key, wheel = build_lambda_zip()
    s3.put_object(Bucket=bucket, Key=key, Body=body)
    print(f"Lambda コードをアップロードしました（{len(body) / 1_000_000:.1f} MB、{wheel}）")
    return key


def deploy_stack(cfn, name, owner, parameters):
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
    print(f"スタック {name} を{action}しています（数分）...")
    try:
        waiter.wait(StackName=name, WaiterConfig={"Delay": 10, "MaxAttempts": 90})
    except WaiterError:
        print(f"スタックの{action}に失敗しました。原因は次で確認できます:", file=sys.stderr)
        print(f"  aws cloudformation describe-events --stack-name {name}", file=sys.stderr)
        sys.exit(1)
    print(f"スタックの{action}が完了しました")


def replay_exists(cfn, owner):
    try:
        cfn.describe_stacks(StackName=replay_stack_name(owner))
        return True
    except ClientError:
        return False


def generate_and_publish(owner, region, use_replay):
    """generate_ttl.py → publish_ttl.py と同じことを行う。"""
    py = sys.executable
    tools = ROOT / "tools"
    if use_replay:
        print(f"replay（{replay_stack_name(owner)}）の API からグラフを生成します")
        cmd = [py, str(tools / "generate_ttl.py"), "--owner", owner, "--region", region]
    else:
        print(f"replay の環境がないため、サンプル CSV からグラフを生成します（API の URL は仮）")
        cmd = [py, str(tools / "generate_ttl.py"), "--csv", str(SAMPLE_CSV)]
    subprocess.run(cmd, check=True)
    subprocess.run([py, str(tools / "publish_ttl.py"), "--owner", owner, "--region", region], check=True)


def main():
    ap = argparse.ArgumentParser(description="KG の AWS 環境を作成・更新し、グラフを公開する")
    add_common_args(ap)
    ap.add_argument("--memory", type=int, choices=[256, 512, 1024, 2048, 4096], default=512,
                    help="SPARQL Lambda のメモリ（MB、既定 512）")
    ap.add_argument("--check-interval", type=int, default=30, help="S3 のグラフの更新を確認する間隔（秒、既定 30）")
    ap.add_argument("--skip-publish", action="store_true", help="グラフの生成・公開をしない")
    args = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)  # 子プロセス（generate / publish）の出力と順番が入れ替わらないように

    session = boto3.Session(region_name=args.region)
    account = session.client("sts").get_caller_identity()["Account"]
    name = stack_name(args.owner)
    code_bucket = artifacts_bucket_name(args.owner, account)
    code_key = upload_lambda_code(session.client("s3"), code_bucket, args.region, args.owner)

    cfn = session.client("cloudformation")
    deploy_stack(cfn, name, args.owner, {
        "CodeBucket": code_bucket, "CodeKey": code_key,
        "MemorySize": args.memory, "CheckIntervalSeconds": args.check_interval,
        "ReplayApiFunctionName": f"{replay_stack_name(args.owner)}-api",
    })
    outputs = {o["OutputKey"]: o["OutputValue"] for o in cfn.describe_stacks(StackName=name)["Stacks"][0]["Outputs"]}

    if not args.skip_publish:
        print()
        generate_and_publish(args.owner, args.region, replay_exists(cfn, args.owner))

    print()
    print(f"スタック名      : {name}")
    print(f"グラフの置き場所: s3://{outputs['GraphBucket']}/{outputs['GraphKey']}")
    print(f"SPARQL          : {outputs['SparqlUrl']}sparql")
    print(f"REST API        : {outputs['ApiUrl']}v1/…（仕様は aws/openapi.yaml）")
    print()
    print("確認: python tools/sparql.py --owner " + args.owner + " --status")


if __name__ == "__main__":
    main()
