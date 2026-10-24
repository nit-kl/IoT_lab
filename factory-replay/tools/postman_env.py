"""Postman の Environment ファイルを作る（HTTP API の URL と、いまの AWS 認証情報を入れる）。

    python tools/postman_env.py --owner kojima

出力した postman/xcp-factory-<owner>.postman_environment.json を Postman にインポートする。
認証情報は一時的なものなので、Postman で 403（The security token included in the request is expired など）が
出たら、このスクリプトを再実行してインポートし直す。ファイルには秘密情報が入るので Git に入れないこと。
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "aws"))
from apiclient import Client  # noqa: E402
from naming import add_common_args, stack_name  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "postman"


def main():
    ap = argparse.ArgumentParser(description="Postman の Environment ファイルを作る")
    add_common_args(ap)
    args = ap.parse_args()

    client = Client(args.owner, args.region)
    creds = client.session.get_credentials()
    frozen = creds.get_frozen_credentials()
    name = stack_name(args.owner)
    values = [
        ("baseUrl", client.base, "default"),
        ("region", args.region, "default"),
        ("accessKeyId", frozen.access_key, "secret"),
        ("secretAccessKey", frozen.secret_key, "secret"),
        ("sessionToken", frozen.token or "", "secret"),
    ]
    env = {
        "name": name,
        "values": [{"key": k, "value": v, "type": t, "enabled": True} for k, v, t in values],
        "_postman_variable_scope": "environment",
    }
    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / f"{name}.postman_environment.json"
    out.write_text(json.dumps(env, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"作成しました: {out}")
    print(f"API の URL : {client.base}")
    expiry = getattr(creds, "_expiry_time", None)
    if expiry:
        print(f"認証情報の有効期限: {expiry.astimezone().isoformat(timespec='minutes')}（切れたら再実行してインポートし直す）")
    else:
        print("認証情報の有効期限は不明です。Postman で 403 が出たら再実行してインポートし直してください")


if __name__ == "__main__":
    main()
