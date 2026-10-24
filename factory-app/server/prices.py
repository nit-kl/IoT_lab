"""config/agent.toml の単価を、AWS Pricing API（東京リージョン）の今の単価と比べる（費用はかからない。aws login が必要）。

    python server/prices.py

日本国内の推論プロファイル（jp.）の単価は、Pricing API では「Standard」（Global でも Long Context でもないもの）。
違いがあれば表示して終了コード 1 で終わる。直すときは config/agent.toml を手で書き換える。
"""

import json
import sys
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import CONFIG_PATH, load_config  # noqa: E402

# Pricing API の usagetype の末尾 → 設定の項目
USAGE_TYPES = {
    "input_tokens_standard-Units": "input",
    "output_tokens_standard-Units": "output",
    "cache_read_tokens_standard-Units": "cache_read",
    "cache_write_tokens_standard-Units": "cache_write",
}


def official_prices(region="ap-northeast-1"):
    """{モデル名（例: Claude Haiku 5.5）: {"input": ドル, ...}}"""
    pricing = boto3.client("pricing", region_name="us-east-1")  # Pricing API は us-east-1 など一部のリージョンだけ
    out = {}
    pages = pricing.get_paginator("get_products").paginate(
        ServiceCode="AmazonBedrockFoundationModels", Filters=[{"Type": "TERM_MATCH", "Field": "regionCode", "Value": region}])
    for page in pages:
        for item in page["PriceList"]:
            product = json.loads(item)
            attrs = product["product"]["attributes"]
            name = (attrs.get("servicename") or "").replace(" (Amazon Bedrock Edition)", "")
            field = next((f for suffix, f in USAGE_TYPES.items() if attrs.get("usagetype", "").endswith("_" + suffix)), None)
            if not name or not field:
                continue
            for term in product["terms"].get("OnDemand", {}).values():
                for dim in term["priceDimensions"].values():
                    out.setdefault(name, {})[field] = float(dim["pricePerUnit"]["USD"])
    return out


def main():
    config = load_config()
    official = official_prices()
    differences = 0
    for key, model in config["models"].items():
        # 例: "Claude Haiku 5.5（日本国内）" → "Claude Haiku 5.5"
        name = model["label"].split("（")[0].strip()
        prices = official.get(name)
        if not prices:
            print(f"{key}: Pricing API に {name} が見つかりません")
            differences += 1
            continue
        for field in ("input", "output", "cache_read", "cache_write"):
            ours, theirs = model[field], prices.get(field)
            mark = "OK" if theirs is not None and abs(ours - theirs) < 1e-9 else "違う"
            differences += mark != "OK"
            print(f"{key:6} {field:12} 設定 ${ours:<8} 公式 ${theirs}  {mark}")
    print(f"\n{CONFIG_PATH.relative_to(CONFIG_PATH.parents[1])}: " + ("すべて公式の単価と一致" if not differences else f"{differences} か所が違います"))
    sys.exit(1 if differences else 0)


if __name__ == "__main__":
    main()
