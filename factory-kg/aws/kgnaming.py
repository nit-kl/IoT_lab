"""リソース名の決まりごと（決定事項 8）。

replay（xcp-factory-<owner>）と区別するため xcp-factorykg-<owner> にする。
xcp-factory-<owner>- で始めると、replay の teardown の残存確認が自分のものとして数えてしまう。
"""

import argparse
import re

PROJECT = "xcp-factorykg"
OWNER_PATTERN = re.compile(r"^[a-z][a-z0-9]{1,15}$")  # replay と同じルール
DEFAULT_REGION = "ap-northeast-1"


def owner_arg(text):
    if not OWNER_PATTERN.fullmatch(text):
        raise argparse.ArgumentTypeError("--owner は英小文字で始まる英小文字・数字 2〜16 文字にしてください")
    return text


def stack_name(owner):
    return f"{PROJECT}-{owner}"


def artifacts_bucket_name(owner, account):
    """Lambda コードの zip を置くバケット。スタックより先に必要なのでスタック外で作る。"""
    return f"{stack_name(owner)}-artifacts-{account}"


def replay_stack_name(owner):
    return f"xcp-factory-{owner}"


def add_common_args(parser):
    parser.add_argument("--owner", type=owner_arg, required=True, help="利用者名（例: kojima）")
    parser.add_argument("--region", default=DEFAULT_REGION, help=f"リージョン（既定 {DEFAULT_REGION}）")
