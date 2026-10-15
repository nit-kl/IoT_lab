"""
ファイル連携(ハンズオン7):在庫・発注システムが書き出すCSVを検証・変換して、S3にアップロードする

  MQTTは「小さなメッセージをたくさん」送るのに向いています。数十〜数千行のCSVのような「まとまったファイル」は、
  MQTTに分けて流すより、ファイルのままS3に置く方が素直です。

  流れ(1つのファイルごと)
    1. 書き終わったファイル(*.csv)だけを見る(書きかけの *.tmp は見ない)
    2. 検証する:文字コード(Shift_JIS)、見出し、各行の列数と数値、最後の行の件数(END,件数)
       → 1つでもおかしければアップロードせず error/ に移す(中途半端なデータを分析に混ぜない)
    3. 変換する:UTF-8・LF、見出しを英語の列名に、日時を日本時間からUTCのISO8601に
    4. 原本(inventory-raw/)と変換後(inventory/dt=…/)の2つをS3にアップロード
    5. アップロードできたら sent/ に移す(できなければ残しておき、次の周期でやり直す)

  S3のキー(ファイル名)は、在庫日時とファイル名だけから決まります。同じファイルを2回アップロードしても
  上書きになるだけで、二重になりません(MQTTのメッセージとの違い)。

  AWSの認証情報は、ゲートウェイのX.509証明書を使って AWS IoT の credential provider からもらう
  一時的なもの(1時間で失効)です。ゲートウェイに長期のアクセスキーを置きません。
"""
import json
import logging
import ssl
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("gateway")
BASE_DIR = Path(__file__).resolve().parent
JST = timezone(timedelta(hours=9))

# (CSVの見出し, 変換後の列名, 種類)。この順で並んでいることを確かめる
COLUMNS = [("店舗コード", "store_code", "str"), ("JANコード", "jan", "str"), ("商品名", "product_name", "str"),
           ("部門", "category", "str"), ("陳列場所", "location", "str"), ("売価", "price_yen", "int"),
           ("在庫数", "stock_qty", "int"), ("発注点", "reorder_point", "int"), ("発注数", "order_qty", "int"),
           ("入荷予定日時", "arrival_due", "time"), ("在庫日時", "snapshot_at", "time")]


class Rejected(Exception):
    """検証で受け付けなかったファイル"""


def to_utc(text: str) -> str:
    if not text:
        return ""
    dt = datetime.strptime(text, "%Y/%m/%d %H:%M").replace(tzinfo=JST)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def convert(raw: bytes) -> tuple[str, str]:
    """検証して、UTF-8のCSV(英語の見出し)と、在庫日時(UTC)を返す。おかしければ Rejected"""
    try:
        text = raw.decode("cp932")
    except UnicodeDecodeError as e:
        raise Rejected(f"Shift_JISとして読めません: {e}") from e
    lines = text.splitlines()
    if len(lines) < 2 or not lines[-1].startswith("END,"):
        raise Rejected("最後の行が END,件数 ではありません(途中で切れている可能性)")
    header, rows, trailer = lines[0].split(","), lines[1:-1], lines[-1]
    if header != [c[0] for c in COLUMNS]:
        raise Rejected(f"見出しが仕様と違います: {header}")
    if int(trailer.split(",")[1]) != len(rows):
        raise Rejected(f"件数が合いません(END行は{trailer.split(',')[1]}件、明細は{len(rows)}行)")
    out = [",".join(c[1] for c in COLUMNS)]
    for n, line in enumerate(rows, start=2):
        fields = line.split(",")
        if len(fields) != len(COLUMNS):
            raise Rejected(f"{n}行目の列数が {len(fields)} です(正しくは {len(COLUMNS)})")
        try:
            values = [str(int(v)) if kind == "int" else to_utc(v) if kind == "time" else v
                      for v, (_, _, kind) in zip(fields, COLUMNS)]
        except ValueError as e:
            raise Rejected(f"{n}行目に読めない値があります: {e}") from e
        out.append(",".join(values))
    snapshot = out[1].split(",")[-1] if rows else ""
    if not snapshot:
        raise Rejected("明細が0行です")
    return "\n".join(out) + "\n", snapshot


class CredentialProvider:
    """X.509証明書で AWS IoT credential provider から一時的な認証情報をもらう(失効の5分前まで使い回す)"""

    def __init__(self, cfg: dict):
        aws, mqtt = cfg["aws"], cfg["mqtt"]
        self.url = f"https://{aws['credentials_endpoint']}/role-aliases/{aws['role_alias']}/credentials"
        self.thing_name = mqtt["client_id"]
        tls = mqtt["tls"]
        self.ctx = ssl.create_default_context(cafile=str(BASE_DIR / tls["ca"]))
        self.ctx.load_cert_chain(str(BASE_DIR / tls["cert"]), str(BASE_DIR / tls["key"]))
        self.creds, self.expires = None, 0.0

    def get(self) -> dict:
        if self.creds is None or time.time() > self.expires - 300:
            req = urllib.request.Request(self.url, headers={"x-amzn-iot-thingname": self.thing_name})
            with urllib.request.urlopen(req, context=self.ctx, timeout=10) as r:
                self.creds = json.loads(r.read())["credentials"]
            self.expires = datetime.fromisoformat(self.creds["expiration"].replace("Z", "+00:00")).timestamp()
            log.info("一時的な認証情報を取得しました(失効 %s)", self.creds["expiration"])
        return self.creds


class S3Uploader:
    def __init__(self, cfg: dict):
        if "aws" not in cfg:
            raise SystemExit("gateway_config.json にファイル連携の設定(aws)がありません。python aws/setup_aws.py を再実行してください")
        self.bucket, self.region = cfg["aws"]["bucket"], cfg["aws"]["region"]
        self.provider = CredentialProvider(cfg)

    def put(self, key: str, body: bytes, content_type: str) -> None:
        import boto3   # ファイル連携を使うときだけ読み込む
        c = self.provider.get()
        s3 = boto3.client("s3", region_name=self.region, aws_access_key_id=c["accessKeyId"],
                          aws_secret_access_key=c["secretAccessKey"], aws_session_token=c["sessionToken"])
        s3.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType=content_type)


class InventoryFileSource:
    """在庫・発注システムの出力フォルダ(outbox)を見て、新しいCSVをS3にアップロードする"""

    is_file_source = True

    def __init__(self, cfg: dict, gateway_id: str):
        self.source_id, self.gateway_id = cfg["source_id"], gateway_id
        self.outbox = (BASE_DIR / cfg["path"]).resolve()

    def process(self, uploader: "S3Uploader | None") -> None:
        """uploader が None(--console)のときは検証と変換だけ行い、ファイルは動かさない"""
        for f in sorted(self.outbox.glob("*.csv")):
            raw = f.read_bytes()
            try:
                body, snapshot = convert(raw)
            except Rejected as e:
                log.warning("%s %s を受け付けません: %s → error/ に移します", self.source_id, f.name, e)
                if uploader:
                    (self.outbox / "error").mkdir(exist_ok=True)
                    f.replace(self.outbox / "error" / f.name)
                continue
            dt = snapshot[:10]   # 在庫日時(UTC)の日付。アップロードした日ではないので、やり直しても同じ場所になる
            key = f"inventory/dt={dt}/{self.gateway_id}-{f.stem}.csv"
            if uploader is None:
                print(f"[console] {f.name} → s3://…/{key}({body.count(chr(10)) - 1}行)\n{body[:300]}")
                continue
            try:
                uploader.put(f"inventory-raw/{self.gateway_id}/{f.name}", raw, "text/csv; charset=Shift_JIS")
                uploader.put(key, body.encode("utf-8"), "text/csv; charset=utf-8")
            except Exception as e:   # 回線断など。ファイルは残しておき、次の周期でやり直す
                log.warning("%s %s のアップロードに失敗しました(次の周期でやり直します): %s", self.source_id, f.name, e)
                return
            (self.outbox / "sent").mkdir(exist_ok=True)
            f.replace(self.outbox / "sent" / f.name)
            log.info("%s %s をアップロードしました → %s", self.source_id, f.name, key)
