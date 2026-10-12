"""
docs/アーキテクチャとコスト.md を実装に追従させるためのツール(Python標準ライブラリのみ)

  python tools/docs_sync.py                 コスト表を再計算してドキュメントに書き込む
  python tools/docs_sync.py --check         ドキュメントが実装とずれていないか検査する(CIで実行。ずれていたら終了コード1)
  python tools/docs_sync.py --update-prices AWS公開の料金表(Price List)から東京リージョンの単価を取り直す

--check で見ること
  1. aws/template.yaml の全リソースが、ドキュメントの「AWSリソース一覧」に載っているか(余分な行が無いか)
  2. docs/ の全ハンズオン手順書が、ドキュメントの「ハンズオン一覧」からリンクされているか
  3. コスト表が、この下の使用量モデル(USAGE)と単価ファイルから計算した最新の内容になっているか

ハンズオンを追加・変更して使うリソースや件数が変わったら、usage() に行を足してから、引数なしで実行します。
"""
import argparse
import json
import re
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "アーキテクチャとコスト.md"
TEMPLATE = ROOT / "iot-lab" / "aws" / "template.yaml"
PRICES = Path(__file__).resolve().parent / "prices_ap-northeast-1.json"
REGION = "ap-northeast-1"
JPY_PER_USD = 150

# 単価: (価格表のサービス名, usagetype, 何件目からの単価か)。単価は1単位あたりUSD
PRICE_SOURCES = {
    "iot_message": ("AWSIoT", "APN1-Messages", "0"),
    "iot_connection_minute": ("AWSIoT", "APN1-ConnectionMinutes", "0"),
    "iot_rule_triggered": ("AWSIoT", "APN1-RulesTriggered", "0"),
    "iot_action_executed": ("AWSIoT", "APN1-ActionsExecuted", "0"),
    "lambda_request": ("AWSLambda", "APN1-Request", "0"),
    "lambda_gb_second": ("AWSLambda", "APN1-Lambda-GB-Second", "0"),
    "ddb_write_request_unit": ("AmazonDynamoDB", "APN1-WriteRequestUnits", "0"),
    "ddb_read_request_unit": ("AmazonDynamoDB", "APN1-ReadRequestUnits", "0"),
    "s3_put_request": ("AmazonS3", "APN1-Requests-Tier1", "0"),
    "s3_get_request": ("AmazonS3", "APN1-Requests-Tier2", "0"),
    "s3_storage_gb_month": ("AmazonS3", "APN1-TimedStorage-ByteHrs", "0"),
    "athena_scanned_tb": ("AmazonAthena", "APN1-DataScannedInTB", "0"),
    "logs_ingest_gb": ("AmazonCloudWatch", "APN1-VendedLog-Bytes", "0"),   # Lambdaのログはvended logs扱い
    "scheduler_invocation": ("AWSEvents", "APN1-ScheduledInvocation", "14000000"),
    "sns_request": ("AmazonSNS", "APN1-Requests-Tier1", "1000000"),
    "sns_email": ("AmazonSNS", "APN1-DeliveryAttempts-SMTP", "1000"),
    "glue_catalog_request": ("AWSGlue", "APN1-Catalog-Request", "0"),
}
# 毎月の無料枠(アカウント全体で共有。新規アカウントのクレジットとは別に、期限なしで使えるもの)
FREE_TIER = {
    "lambda_request": 1_000_000,
    "lambda_gb_second": 400_000,
    "scheduler_invocation": 14_000_000,
    "sns_request": 1_000_000,
    "sns_email": 1_000,
    "logs_ingest_gb": 5,
    "glue_catalog_request": 1_000_000,
}
UNIT_LABEL = {
    "iot_message": "件", "iot_connection_minute": "接続分", "iot_rule_triggered": "回", "iot_action_executed": "回",
    "lambda_request": "回", "lambda_gb_second": "GB秒", "ddb_write_request_unit": "WRU", "ddb_read_request_unit": "RRU",
    "s3_put_request": "回", "s3_get_request": "回", "s3_storage_gb_month": "GB月", "athena_scanned_tb": "TB",
    "logs_ingest_gb": "GB", "scheduler_invocation": "回", "sns_request": "回", "sns_email": "通",
    "glue_catalog_request": "回",
}

# ---------------------------------------------------------------- 使用量モデル
EQUIPMENT = 3            # 機器の台数(points.json)
POLL_SEC = 10            # ゲートウェイの読み取り周期
MSG_BYTES = 500          # テレメトリ1件のJSONの大きさ(S3の1ファイル)
LOG_BYTES = 400          # Lambda 1回あたりのログ(START/END/REPORT+自分のprint)
GRAFANA_PANELS = 5       # ダッシュボードのパネル数(1パネル=1クエリ)
GRAFANA_REFRESH_SEC = 30 # ダッシュボードの自動更新間隔
ATHENA_MIN_BYTES = 10 * 1024**2   # Athenaは1クエリ最低10MB分課金

SCENARIOS = {
    "study": dict(title="学習(ゲートウェイを3時間動かす)", hours=3, athena_queries=20, grafana_hours=1,
                  commands=20, emails=20),
    "month": dict(title="1か月つけっぱなし(Grafanaは開かない)", hours=24 * 30, athena_queries=20, grafana_hours=0,
                  commands=0, emails=0),
}


def usage(s: dict) -> list[tuple[str, str, str, float]]:
    """(ハンズオン, 内訳, 単価キー, 数量) の一覧"""
    n = s["hours"] * 3600 / POLL_SEC * EQUIPMENT           # テレメトリの件数
    minutes = s["hours"] * 60
    objects_today = min(n, 24 * 3600 / POLL_SEC * EQUIPMENT) / 2   # その日のパーティションの平均ファイル数
    grafana_queries = s["grafana_hours"] * 3600 / GRAFANA_REFRESH_SEC * GRAFANA_PANELS
    gb = 1024**3
    return [
        ("1", "テレメトリのMQTTメッセージ", "iot_message", n),
        ("1", "ゲートウェイの接続時間", "iot_connection_minute", minutes),
        ("1", "ルール1(S3保存)の起動", "iot_rule_triggered", n),
        ("1", "ルール1のS3アクション", "iot_action_executed", n),
        ("1", "S3 PUT(1メッセージ1ファイル)", "s3_put_request", n),
        ("1", "S3 保存容量", "s3_storage_gb_month", n * MSG_BYTES / gb * (s["hours"] / 720) / 2),
        ("1", "Athena 手動クエリのGET(当日の全ファイル)", "s3_get_request", s["athena_queries"] * objects_today),
        ("1", "Athena 手動クエリのスキャン量", "athena_scanned_tb", s["athena_queries"] * ATHENA_MIN_BYTES / 1024**4),
        ("1", "Glueデータカタログの参照", "glue_catalog_request", s["athena_queries"] * 2),
        ("2", "Grafana 自動更新のGET(当日の全ファイル)", "s3_get_request", grafana_queries * objects_today),
        ("2", "Grafana 自動更新のスキャン量", "athena_scanned_tb", grafana_queries * ATHENA_MIN_BYTES / 1024**4),
        ("2", "Grafana のGlue参照", "glue_catalog_request", grafana_queries * 2),
        ("3", "ルール2(最新値)の起動", "iot_rule_triggered", n),
        ("3", "ルール2のLambdaアクション", "iot_action_executed", n),
        ("3", "最新値Lambdaの実行回数", "lambda_request", n),
        ("3", "最新値Lambdaの実行時間(128MB×50ms)", "lambda_gb_second", n * 0.125 * 0.05),
        ("3", "最新値テーブルへの書き込み", "ddb_write_request_unit", n),
        ("3", "最新値Lambdaのログ", "logs_ingest_gb", n * LOG_BYTES / gb),
        ("4", "通知Lambda(Streams、最大1件1回)", "lambda_request", n),
        ("4", "通知Lambda(1分ごとの途絶チェック)", "lambda_request", minutes),
        ("4", "通知Lambdaの実行時間(128MB×50ms)", "lambda_gb_second", (n + minutes) * 0.125 * 0.05),
        ("4", "EventBridge Scheduler", "scheduler_invocation", minutes),
        ("4", "途絶チェックのScan(2テーブル)", "ddb_read_request_unit", minutes * 2 * 0.5),
        ("4", "通知Lambdaのログ", "logs_ingest_gb", (n + minutes) * LOG_BYTES / gb),
        ("4", "SNS 送信", "sns_request", s["emails"]),
        ("4", "SNS メール配信", "sns_email", s["emails"]),
        ("5", "命令・受信・結果のMQTTメッセージ", "iot_message", s["commands"] * 3),
        ("5", "結果記録ルールの起動", "iot_rule_triggered", s["commands"]),
        ("5", "結果記録ルールのDynamoDBアクション", "iot_action_executed", s["commands"]),
        ("5", "命令テーブルへの書き込み(送信時+結果)", "ddb_write_request_unit", s["commands"] * 2),
        ("5", "結果待ちの読み取り(1秒ごと、約5回)", "ddb_read_request_unit", s["commands"] * 5),
    ]


# ---------------------------------------------------------------- 計算と出力
def load_prices() -> dict:
    return json.loads(PRICES.read_text(encoding="utf-8"))


def yen(usd: float) -> str:
    v = usd * JPY_PER_USD
    return "0円" if v < 0.005 else (f"{v:.2f}円" if v < 1 else f"{v:,.0f}円")


def qty(v: float) -> str:
    return f"{v:,.0f}" if v >= 10 else ("0.01未満" if 0 < v < 0.01 else f"{v:.2f}".rstrip("0").rstrip(".") if v else "0")


def render_scenario(key: str, prices: dict) -> str:
    s = SCENARIOS[key]
    rows = usage(s)
    out = [f"#### {s['title']}", "",
           "| ハンズオン | 内訳 | 数量 | 料金(無料枠適用前) |", "| --- | --- | --- | --- |"]
    by_ho, by_key = {}, {}
    for ho, label, pk, q in rows:
        cost = q * prices["unit"][pk]
        by_ho[ho] = by_ho.get(ho, 0) + cost
        by_key[pk] = by_key.get(pk, 0) + q
        if q:
            out.append(f"| {ho} | {label} | {qty(q)} {UNIT_LABEL[pk]} | {yen(cost)} |")
    gross = sum(by_ho.values())
    net = sum(max(0, q - FREE_TIER.get(pk, 0)) * prices["unit"][pk] for pk, q in by_key.items())
    out += ["", "| ハンズオン | " + " | ".join(sorted(by_ho)) + " | 合計(無料枠適用前) | **合計(無料枠適用後)** |",
            "| --- | " + " | ".join("---" for _ in by_ho) + " | --- | --- |",
            "| 料金 | " + " | ".join(yen(by_ho[h]) for h in sorted(by_ho)) + f" | {yen(gross)} | **{yen(net)}** |", ""]
    return "\n".join(out)


def render_grafana(prices: dict) -> str:
    per_hour = 3600 / GRAFANA_REFRESH_SEC * GRAFANA_PANELS
    out = ["| その日にたまったファイル数 | 目安 | 1時間開いたままにしたときの料金 |", "| --- | --- | --- |"]
    for objs, note in [(1_000, "ゲートウェイを約1時間動かした"), (10_000, "約9時間動かした"),
                       (25_920, "24時間動かした(1日分)")]:
        usd = per_hour * (objs * prices["unit"]["s3_get_request"] + ATHENA_MIN_BYTES / 1024**4 * prices["unit"]["athena_scanned_tb"])
        out.append(f"| {objs:,} | {note} | {yen(usd)} |")
    return "\n".join(out)


def render_prices(prices: dict) -> str:
    out = [f"東京リージョン(ap-northeast-1)、AWS Price List の公開データより。取得日: {prices['fetched']}。1ドル={JPY_PER_USD}円で換算。", "",
           "| 単価キー | 単価(USD) | 単位 | 毎月の無料枠 |", "| --- | --- | --- | --- |"]
    for pk, (svc, ut, _) in PRICE_SOURCES.items():
        free = f"{FREE_TIER[pk]:,} {UNIT_LABEL[pk]}" if pk in FREE_TIER else "-"
        out.append(f"| {pk} | {prices['unit'][pk]:.10g} | 1 {UNIT_LABEL[pk]}({svc} {ut}) | {free} |")
    return "\n".join(out)


def generated_blocks(prices: dict) -> dict:
    return {
        "cost": "\n".join(render_scenario(k, prices) for k in SCENARIOS).rstrip(),
        "grafana": render_grafana(prices),
        "prices": render_prices(prices),
    }


def replace_blocks(text: str, blocks: dict) -> str:
    for name, body in blocks.items():
        pat = re.compile(rf"(<!-- BEGIN:{name} [^\n]*-->\n)(?:.*?\n)?(<!-- END:{name} -->)", re.S)
        if not pat.search(text):
            raise SystemExit(f"{DOC.name} に <!-- BEGIN:{name} … --> / <!-- END:{name} --> がありません")
        text = pat.sub(lambda m: m.group(1) + body + "\n" + m.group(2), text)
    return text


# ---------------------------------------------------------------- 検査
def template_resources() -> dict:
    text = TEMPLATE.read_text(encoding="utf-8")
    body = text.split("\nResources:\n", 1)[1].split("\nOutputs:\n", 1)[0]
    return dict(re.findall(r"^  ([A-Za-z0-9]+):\n    Type: (\S+)", body, re.M))


def doc_resources(text: str) -> dict:
    section = text.split("## AWSリソース一覧", 1)[1].split("\n## ", 1)[0]
    return dict(re.findall(r"^\| `([A-Za-z0-9]+)` \| `([A-Za-z0-9:]+)` \|", section, re.M))


def check(text: str, prices: dict) -> list[str]:
    errors = []
    tpl, doc = template_resources(), doc_resources(text)
    for name, typ in tpl.items():
        if name not in doc:
            errors.append(f"AWSリソース一覧に {name}({typ})がありません")
        elif doc[name] != typ:
            errors.append(f"AWSリソース一覧の {name} の種類が {doc[name]} になっています(実際は {typ})")
    for name in doc.keys() - tpl.keys():
        errors.append(f"AWSリソース一覧の {name} は template.yaml にありません(削除漏れ)")
    links = {urllib.parse.unquote(t) for t in re.findall(r"\]\(\./([^)]+)\)", text)}
    for f in sorted((ROOT / "docs").glob("IoT学習ハンズオン*.md")):
        if f.name not in links:
            errors.append(f"ハンズオン一覧に {f.name} へのリンクがありません")
    if replace_blocks(text, generated_blocks(prices)) != text:
        errors.append("コスト表が古いままです。python tools/docs_sync.py を実行して更新してください")
    return errors


# ---------------------------------------------------------------- 単価の取得
def update_prices() -> None:
    from datetime import date
    unit, published = {}, {}
    with tempfile.TemporaryDirectory() as tmp:
        for svc in sorted({s for s, _, _ in PRICE_SOURCES.values()}):
            url = f"https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/{svc}/current/{REGION}/index.json"
            path = Path(tmp) / f"{svc}.json"
            print(f"取得中: {svc}")
            urllib.request.urlretrieve(url, path)
            j = json.loads(path.read_text(encoding="utf-8"))
            published[svc] = j.get("publicationDate")
            for pk, (s, ut, begin) in PRICE_SOURCES.items():
                if s != svc:
                    continue
                for sku, p in j["products"].items():
                    if p.get("attributes", {}).get("usagetype") != ut:
                        continue
                    for term in j["terms"].get("OnDemand", {}).get(sku, {}).values():
                        for pd in term["priceDimensions"].values():
                            if pd["beginRange"] == begin:
                                unit[pk] = float(pd["pricePerUnit"]["USD"])
    missing = PRICE_SOURCES.keys() - unit.keys()
    if missing:
        raise SystemExit(f"単価が見つかりません: {sorted(missing)}(AWS側で usagetype が変わった可能性)")
    # 単位をそろえる(Athena は TB、ログ・S3容量は GB のまま)
    PRICES.write_text(json.dumps({"fetched": date.today().isoformat(), "region": REGION, "published": published,
                                  "unit": dict(sorted(unit.items()))}, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8", newline="\n")
    print(f"{PRICES.name} を更新しました")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--update-prices", action="store_true")
    args = ap.parse_args()

    if args.update_prices:
        update_prices()
    prices = load_prices()
    text = DOC.read_text(encoding="utf-8")
    if args.check:
        errors = check(text, prices)
        for e in errors:
            print("NG:", e)
        if errors:
            print("\ndocs/アーキテクチャとコスト.md を更新してください(手順はドキュメント末尾の「このドキュメントの更新のしかた」)")
            sys.exit(1)
        print("OK: アーキテクチャとコストのドキュメントは実装と一致しています")
        return
    new = replace_blocks(text, generated_blocks(prices))
    DOC.write_text(new, encoding="utf-8", newline="\n")
    print("コスト表を更新しました" if new != text else "コスト表は最新です")


if __name__ == "__main__":
    main()
