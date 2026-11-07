"""リソース名の決まりごと。xcp-factory-<owner>- で始めない(以前のキットの残存確認と衝突するため)。"""

import argparse
import re

PROJECT = "xcp-factoryont"
OWNER_PATTERN = re.compile(r"^[a-z][a-z0-9]{1,15}$")
DEFAULT_REGION = "ap-northeast-1"
ROLES = ("工場長", "KPI責任者", "ライン責任者", "保全当番", "品質保証", "生産管理", "管理者")


def owner_arg(text):
    if not OWNER_PATTERN.fullmatch(text):
        raise argparse.ArgumentTypeError("--owner は英小文字で始まる英小文字・数字 2〜16 文字にしてください")
    return text


def stack_name(owner):
    return f"{PROJECT}-{owner}"


def artifacts_bucket_name(owner, account):
    """Lambda のコードを置くバケット。スタックより先に必要なのでスタックの外で作る。"""
    return f"{stack_name(owner)}-artifacts-{account}"


def recipients_map(default, overrides):
    """既定の宛先と「役割=アドレス」の一覧から、役割ごとの宛先を作る。"""
    recipients = {"default": default}
    for item in overrides or ():
        role, separator, address = item.partition("=")
        if not separator or role not in ROLES or "@" not in address:
            raise ValueError(f"--recipient は 役割=アドレス の形で指定してください(役割: {'、'.join(ROLES)}): {item}")
        recipients[role] = address
    return recipients


def add_common_args(parser):
    parser.add_argument("--owner", required=True, type=owner_arg, help="名前に入れる担当者名(例: kojima)")
    parser.add_argument("--region", default=DEFAULT_REGION)
