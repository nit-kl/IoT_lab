"""Oracle への接続、表の作成、データ投入。

    python oracle_db.py setup          # ユーザーと表を作る（初回のみ）
    python oracle_db.py seed           # 全表を空にして模擬データを入れ直す
    python oracle_db.py check          # 件数と読み取り専用ユーザーの権限を確認する
    python oracle_db.py drop           # ユーザーごと全部消す（確認あり）
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

import oracledb
from dotenv import load_dotenv

from generate import TABLE_ORDER, generate

ONPREM = Path(__file__).resolve().parents[1]
SCHEMA_SQL = ONPREM / "db" / "schema.sql"
TRUTH_PATH = ONPREM / ".generated" / "ground_truth.json"
BATCH_SIZE = 5000
TABLESPACE = "FACTORY_DATA"  # 軽量版のイメージには USERS がなく、既定が SYSTEM なので専用に作る
DATAFILE_NAME = "factory_data01.dbf"
PERMISSION_DENIED = (1031, 41900)  # ORA-01031 と、Oracle 23 以降の ORA-41900


def env(name):
    value = os.environ.get(name)
    if not value:
        sys.exit(f"{name} が未設定です。onprem/.env を確認してください")
    return value


def identifier(name):
    """ユーザー名はバインドできないので、形式を確かめてから SQL に入れる。"""
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,29}", name):
        sys.exit(f"ユーザー名の形式が正しくありません: {name}")
    return name.upper()


def password(name):
    value = env(name)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{11,}", value):
        sys.exit(f"{name} は英字で始まる 12 文字以上の英数字と _ にしてください")
    return value


def connect(user_var, password_var):
    return oracledb.connect(user=env(user_var), password=env(password_var), dsn=env("ORACLE_DSN"))


def schema_statements():
    """schema.sql を文ごとに分ける（PL/SQL は含まない）。"""
    text = "\n".join(line for line in SCHEMA_SQL.read_text(encoding="utf-8").splitlines() if not line.startswith("--"))
    return [s.strip() for s in text.split(";") if s.strip()]


def user_exists(cursor, user):
    cursor.execute("SELECT COUNT(*) FROM ALL_USERS WHERE USERNAME = :1", [user])
    return cursor.fetchone()[0] > 0


def datafile_path(existing_file):
    """既存のデータファイルと同じフォルダに、専用表領域のファイルを置く。"""
    if "'" in existing_file:
        sys.exit(f"データファイルのパスに使えない文字があります: {existing_file}")
    return re.sub(r"[^/\\]+$", DATAFILE_NAME, existing_file)


def create_tablespace(cursor):
    cursor.execute("SELECT COUNT(*) FROM DBA_TABLESPACES WHERE TABLESPACE_NAME = :1", [TABLESPACE])
    if cursor.fetchone()[0]:
        return
    cursor.execute("SELECT FILE_NAME FROM DBA_DATA_FILES WHERE TABLESPACE_NAME = 'SYSTEM'")
    path = datafile_path(cursor.fetchone()[0])
    cursor.execute(f"CREATE TABLESPACE {TABLESPACE} DATAFILE '{path}' SIZE 100M AUTOEXTEND ON NEXT 50M MAXSIZE 2G")


def setup():
    app, ro = identifier(env("FACTORY_APP_USER")), identifier(env("FACTORY_RO_USER"))
    with connect("ORACLE_ADMIN_USER", "ORACLE_ADMIN_PASSWORD") as admin, admin.cursor() as cur:
        if user_exists(cur, app):
            sys.exit(f"{app} は既にあります。作り直すときは drop を先に実行してください")
        create_tablespace(cur)
        cur.execute(f'CREATE USER {app} IDENTIFIED BY "{password("FACTORY_APP_PASSWORD")}" '
                    f"DEFAULT TABLESPACE {TABLESPACE} QUOTA UNLIMITED ON {TABLESPACE}")
        cur.execute(f"GRANT CREATE SESSION, CREATE TABLE TO {app}")
        cur.execute(f'CREATE USER {ro} IDENTIFIED BY "{password("FACTORY_RO_PASSWORD")}"')
        cur.execute(f"GRANT CREATE SESSION TO {ro}")
    with connect("FACTORY_APP_USER", "FACTORY_APP_PASSWORD") as conn, conn.cursor() as cur:
        for statement in schema_statements():
            cur.execute(statement)
        for table in TABLE_ORDER:
            cur.execute(f"GRANT SELECT ON {table} TO {ro}")
    print(f"作成しました: ユーザー {app}, {ro} / 表 {len(TABLE_ORDER)} 個")


def seed(end=None):
    tables, truth = generate(end)
    with connect("FACTORY_APP_USER", "FACTORY_APP_PASSWORD") as conn, conn.cursor() as cur:
        for table in reversed(TABLE_ORDER):
            cur.execute(f"DELETE FROM {table}")
        for table in TABLE_ORDER:
            rows = tables[table]
            columns = list(rows[0])
            sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(':' + c for c in columns)})"
            for i in range(0, len(rows), BATCH_SIZE):
                cur.executemany(sql, rows[i:i + BATCH_SIZE])
            print(f"{table:20} {len(rows):>7} 行")
        conn.commit()
    TRUTH_PATH.parent.mkdir(exist_ok=True)
    TRUTH_PATH.write_text(json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"正解を保存しました: {TRUTH_PATH}")


def check():
    app, ro = identifier(env("FACTORY_APP_USER")), env("FACTORY_RO_USER")
    with connect("FACTORY_RO_USER", "FACTORY_RO_PASSWORD") as conn, conn.cursor() as cur:
        total = 0
        for table in TABLE_ORDER:
            cur.execute(f"SELECT COUNT(*) FROM {app}.{table}")
            count = cur.fetchone()[0]
            total += count
            print(f"{table:20} {count:>7} 行")
        print(f"合計 {total} 行")
        try:
            cur.execute(f"DELETE FROM {app}.M_KOJO")
        except oracledb.DatabaseError as e:
            if e.args[0].code in PERMISSION_DENIED:
                print(f"OK: {ro} は書き込みできません")
                return
            raise
        conn.rollback()
        sys.exit(f"NG: {ro} が書き込めてしまいます")


def drop():
    app, ro = identifier(env("FACTORY_APP_USER")), identifier(env("FACTORY_RO_USER"))
    if input(f"{app} と {ro} を全データごと削除します。よろしければ yes: ") != "yes":
        return
    with connect("ORACLE_ADMIN_USER", "ORACLE_ADMIN_PASSWORD") as admin, admin.cursor() as cur:
        for user in (ro, app):
            if user_exists(cur, user):
                cur.execute(f"DROP USER {user} CASCADE")
        cur.execute(f"DROP TABLESPACE {TABLESPACE} INCLUDING CONTENTS AND DATAFILES")
    print("削除しました")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["setup", "seed", "check", "drop"])
    args = parser.parse_args()
    load_dotenv(ONPREM / ".env")
    {"setup": setup, "seed": seed, "check": check, "drop": drop}[args.command]()


if __name__ == "__main__":
    main()
