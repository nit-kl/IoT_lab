"""リソース名の決まりごと。xcp-factory-<owner>- で始めない(以前のキットの残存確認と衝突するため)。"""

import argparse
import re

PROJECT = "xcp-factoryont"
OWNER_PATTERN = re.compile(r"^[a-z][a-z0-9]{1,15}$")
DEFAULT_REGION = "ap-northeast-1"


def owner_arg(text):
    if not OWNER_PATTERN.fullmatch(text):
        raise argparse.ArgumentTypeError("--owner は英小文字で始まる英小文字・数字 2〜16 文字にしてください")
    return text


def stack_name(owner):
    return f"{PROJECT}-{owner}"


def artifacts_bucket_name(owner, account):
    """Lambda のコードを置くバケット。スタックより先に必要なのでスタックの外で作る。"""
    return f"{stack_name(owner)}-artifacts-{account}"


def add_common_args(parser):
    parser.add_argument("--owner", required=True, type=owner_arg, help="名前に入れる担当者名(例: kojima)")
    parser.add_argument("--region", default=DEFAULT_REGION)
