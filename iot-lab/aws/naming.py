"""
リソースの命名(XCPチームの検証用。setup / teardown / show_latest / send_command で共通に使う)

  同じAWSアカウントをチームで共用するので、名前には必ず owner(自分を表す英小文字・数字)を入れます。
    スタック名        xcp-store-iot-<owner>
    各リソースの名前  xcp-store-iot-<owner>-…(個々の名前は template.yaml で決める)
    MQTTトピック      store-<owner>/<店舗ゲートウェイ>/<機器>/…

  owner は setup_aws.py --owner で1回指定すると gateway/gateway_config.json に保存され、
  以後のスクリプトはそこから読みます。
"""
import json
import re
from pathlib import Path

TEAM = "xcp"
PROJECT = "store-iot"
CONFIG = Path(__file__).resolve().parent.parent / "gateway" / "gateway_config.json"
OWNER_PATTERN = re.compile(r"[a-z0-9]{1,16}")


def saved_owner() -> str | None:
    if not CONFIG.exists():
        return None
    return json.loads(CONFIG.read_text(encoding="utf-8")).get("owner")


def resolve_owner(arg: str | None) -> str:
    saved = saved_owner()
    if arg and saved and arg != saved:
        raise SystemExit(f"gateway/gateway_config.json は owner={saved} で作られています。"
                         f"別の owner で作り直すときは、先に python aws/teardown_aws.py を実行してください")
    owner = arg or saved
    if not owner:
        raise SystemExit("--owner を指定してください(例: --owner kojima)。"
                         "同じAWSアカウントを使うチームの中で、自分のリソースを見分けるための名前です")
    if not OWNER_PATTERN.fullmatch(owner):
        raise SystemExit(f"--owner は英小文字と数字だけ、16文字以内にしてください: {owner}")
    return owner


def stack_name(owner: str) -> str:
    return f"{TEAM}-{PROJECT}-{owner}"


def tags(owner: str) -> list[dict]:
    """スタックに付けるタグ(対応しているリソースにはすべて引き継がれる)。コスト配分や棚卸しに使う"""
    return [{"Key": "Team", "Value": TEAM}, {"Key": "Project", "Value": PROJECT},
            {"Key": "Env", "Value": "poc"}, {"Key": "Owner", "Value": owner}]


def stack_outputs(session, owner: str) -> dict:
    cfn = session.client("cloudformation")
    try:
        stack = cfn.describe_stacks(StackName=stack_name(owner))["Stacks"][0]
    except cfn.exceptions.ClientError:
        raise SystemExit(f"スタック {stack_name(owner)} がありません。先に python aws/setup_aws.py を実行してください")
    return {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}
