"""Oracle の差分を読み、許可された列だけを S3 へ送る。オンプレ側から外向きに HTTPS で送るだけで、受信口は持たない。

    python extractor/extract.py              # 1 回だけ送る
    python extractor/extract.py --loop 60    # 60 秒ごとに送り続ける(Ctrl+C で停止)
    python extractor/extract.py --reset      # 読み取り位置を消す(次回は全件を送る)

差分は UPD_TS で判定する。遅れてコミットされた行を拾うため、読み取り位置より少し前から読み、送信済みの行は除く。
読み取り位置は S3 への送信に成功してから進めるので、失敗しても次回に同じ範囲を送り直す。
"""

import argparse
import gzip
import hashlib
import json
import os
import sys
import time
import tomllib
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

ONPREM = Path(__file__).resolve().parents[1]
POLICY = Path(__file__).resolve().parent / "policy.toml"
STATE = ONPREM / ".state" / "extractor.json"
AWS_CONFIG = ONPREM / ".secrets" / "aws_config"
SETTINGS = ONPREM / ".secrets" / "extractor.json"
OVERLAP = timedelta(minutes=2)
AWS_PROFILE = "factory-extractor"


def load_policy(path=POLICY):
    with open(path, "rb") as f:
        return tomllib.load(f)["tables"]


def plain(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def digest(table, row):
    text = json.dumps({"t": table, "r": row}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def load_state(path=STATE):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save_state(state, path=STATE):
    """途中で止まっても壊れないよう、別のファイルに書いてから置き換える。"""
    path.parent.mkdir(exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state), encoding="utf-8")
    os.replace(temporary, path)


def changed_rows(fetch, table, columns, entry):
    """前回より後に変わった行を返す。

    fetch(table, columns, since) は UPD_TS が since 以降の行(dict)を返す。since が None なら全行。
    戻り値は (送る行, 次の読み取り位置の情報)。
    """
    watermark = datetime.fromisoformat(entry["watermark"]) if entry else None
    since = watermark - OVERLAP if watermark else None
    seen = set(entry["seen"]) if entry else set()
    fresh, marks = [], []
    for row in fetch(table, columns, since):
        updated = row["UPD_TS"]
        record = {k: plain(v) for k, v in row.items()}
        key = digest(table, record)
        marks.append((updated, key))
        if key not in seen:
            fresh.append(record)
    if not marks:
        return [], entry
    latest = max(updated for updated, _ in marks)
    if watermark:
        latest = max(latest, watermark)
    keep_from = latest - OVERLAP
    return fresh, {"watermark": latest.isoformat(), "seen": sorted(k for u, k in marks if u >= keep_from)}


def build_batch(rows_by_table, now, batch_id):
    """1 回分を gzip の JSON Lines にする。1 行目は内容の要約。"""
    header = {"batch": batch_id, "created": now.isoformat(), "tables": {t: len(r) for t, r in rows_by_table.items()}}
    lines = [json.dumps(header, ensure_ascii=False)]
    for table, rows in rows_by_table.items():
        lines += [json.dumps({"t": table, "r": row}, ensure_ascii=False) for row in rows]
    return gzip.compress(("\n".join(lines) + "\n").encode(), mtime=0)


def batch_key(now, batch_id):
    return f"raw/{now:%Y/%m/%d}/{now:%Y%m%dT%H%M%S}-{batch_id}.jsonl.gz"


def extract_once(fetch, put, policy, state, now=None):
    """差分を送る。戻り値は (送った内容, 新しい状態)。差分がなければ送らず、内容は {}。"""
    now = now or datetime.now()
    rows_by_table, new_state = {}, dict(state)
    for table, columns in policy.items():
        rows, entry = changed_rows(fetch, table, columns, state.get(table))
        if entry:
            new_state[table] = entry
        if rows:
            rows_by_table[table] = rows
    if not rows_by_table:
        return {}, new_state
    batch_id = uuid.uuid4().hex[:8]
    put(batch_key(now, batch_id), build_batch(rows_by_table, now, batch_id))
    return {t: len(r) for t, r in rows_by_table.items()}, new_state


def oracle_fetcher(cursor, owner):
    def fetch(table, columns, since):
        names = ", ".join(columns)  # 表名と列名は policy.toml の固定値
        sql = f"SELECT {names} FROM {owner}.{table}"
        if since is None:
            cursor.execute(sql)
        else:
            cursor.execute(f"{sql} WHERE UPD_TS >= :1", [since])
        return [dict(zip(columns, row)) for row in cursor]
    return fetch


def s3_putter(bucket):
    import boto3
    os.environ["AWS_CONFIG_FILE"] = str(AWS_CONFIG)
    clients = []

    def put(key, body):
        # 接続は最初の送信時に作る。起動時に回線が切れていても、失敗として扱って次回にやり直すため
        if not clients:
            clients.append(boto3.Session(profile_name=AWS_PROFILE).client("s3"))
        # IfNoneMatch: 同じキーへの上書きを禁止する(受信記録は変更しない)
        clients[0].put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/gzip", IfNoneMatch="*")
    return put


def run_once(cursor, owner, put, policy):
    now = datetime.now()
    state = load_state()
    sent, new_state = extract_once(oracle_fetcher(cursor, owner), put, policy, state, now)
    save_state(new_state)  # 送信に失敗すると例外になり、ここには来ない
    if sent:
        detail = "  ".join(f"{t} {n}" for t, n in sorted(sent.items()))
        print(f"{now:%H:%M:%S} S3 へ送信 {sum(sent.values())} 行: {detail}")
    else:
        print(f"{now:%H:%M:%S} 差分なし(送信しません)")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--loop", type=int, help="送信の間隔(秒)")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    if args.reset:
        STATE.unlink(missing_ok=True)
        print("読み取り位置を消しました。次回は全件を送ります")
        return

    import oracledb
    from botocore.exceptions import BotoCoreError, ClientError
    from dotenv import load_dotenv

    load_dotenv(ONPREM / ".env")
    if not SETTINGS.exists():
        sys.exit("AWS の設定がありません。cloud/setup_aws.py を先に実行してください")
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    policy = load_policy()
    put = s3_putter(settings["bucket"])
    owner = os.environ["FACTORY_APP_USER"]
    while True:
        try:
            # 毎回つなぎ直す。Oracle が再起動しても、そのまま動き続けるため
            with oracledb.connect(user=os.environ["FACTORY_RO_USER"], password=os.environ["FACTORY_RO_PASSWORD"],
                                  dsn=os.environ["ORACLE_DSN"]) as conn, conn.cursor() as cursor:
                run_once(cursor, owner, put, policy)
        except (BotoCoreError, ClientError, oracledb.Error) as e:
            print(f"{datetime.now():%H:%M:%S} 送信に失敗しました。次回に送り直します: {e}")
            if not args.loop:
                sys.exit(1)
        if not args.loop:
            return
        try:
            time.sleep(args.loop)
        except KeyboardInterrupt:
            return


if __name__ == "__main__":
    main()
