"""リソース名の決まりごと。共有アカウントで衝突しないよう、すべてオーナー名を含める。"""

import argparse
import re

PROJECT = "xcp-factory"
OWNER_PATTERN = re.compile(r"^[a-z][a-z0-9]{1,15}$")  # template.yaml の Owner と同じ
DEFAULT_REGION = "ap-northeast-1"


def owner_arg(text):
    if not OWNER_PATTERN.fullmatch(text):
        raise argparse.ArgumentTypeError("--owner は英小文字で始まる英小文字・数字 2〜16 文字にしてください")
    return text


def stack_name(owner):
    """CloudFormation スタック名。Aurora クラスター ID・S3 バケット名もここから作られる。"""
    return f"{PROJECT}-{owner}"


def add_common_args(parser):
    parser.add_argument("--owner", type=owner_arg, required=True, help="利用者名（例: kojima）")
    parser.add_argument("--region", default=DEFAULT_REGION, help=f"リージョン（既定 {DEFAULT_REGION}）")
