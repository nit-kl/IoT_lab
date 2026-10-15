"""
店舗サーバー(POSのストアサーバー)を模擬する(学習用)

  レジで会計が終わるたびに、店舗サーバーのDBに取引が1件ずつ書かれていく様子を、SQLiteのファイル
  (simulator/data/pos.db)で再現します。実際の店舗サーバーは SQL Server などのことが多く、
  連携する側は「読み取り専用のユーザーで、決められたテーブル(ビュー)だけを読む」のが普通です。

テーブル(POSベンダーから受け取る「テーブル定義書」のつもりで読んでください)
--------------------------------------------------------------------
  pos_transaction        取引(会計1回=1行)
    txn_id       INTEGER   取引番号。DBに書かれた順に増える連番
    store_code   TEXT      店舗コード
    register_no  INTEGER   レジ番号(1〜3=有人レジ、4=セルフレジ)
    sold_at      TEXT      会計した時刻。店舗の現地時刻(日本時間)で、タイムゾーンは書かれていない
    txn_type     TEXT      SALE=売上、VOID=取消(元の取引を打ち消す。金額・数量はマイナス)
    ref_txn_id   INTEGER   VOID のとき、取り消した元の取引番号
    total_yen    INTEGER   合計金額(税込、円)
    item_count   INTEGER   点数
  pos_transaction_line   取引明細(商品1種類=1行)
    txn_id, line_no, jan(JANコード), product_name, category(部門), qty, unit_price_yen, amount_yen

  ※ セルフレジ(4番)は店舗サーバーとの通信が不安定な想定で、取引を手元に貯めて10分ごとにまとめて書きます。
     そのため「取引番号の順」と「会計した時刻の順」が一致しません(差分の取り方を考える材料)。

実行:  python simulator/pos_server.py [--db simulator/data/pos.db] [--verbose]
"""
import argparse
import random
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from store_model import JST, REGISTERS, STORE_CODE, basket, buyers

SELF_REGISTER = 4
SELF_FLUSH_MIN = 10       # セルフレジがまとめて書く間隔(分)
VOID_RATE = 0.02          # 取消の割合
BACKFILL_MIN = 120        # 初めて起動したとき、過去何分ぶんの取引を作っておくか

SCHEMA = """
CREATE TABLE IF NOT EXISTS pos_transaction (
  txn_id INTEGER PRIMARY KEY AUTOINCREMENT, store_code TEXT NOT NULL, register_no INTEGER NOT NULL,
  sold_at TEXT NOT NULL, txn_type TEXT NOT NULL, ref_txn_id INTEGER, total_yen INTEGER NOT NULL,
  item_count INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS pos_transaction_line (
  txn_id INTEGER NOT NULL, line_no INTEGER NOT NULL, jan TEXT NOT NULL, product_name TEXT NOT NULL,
  category TEXT NOT NULL, qty INTEGER NOT NULL, unit_price_yen INTEGER NOT NULL, amount_yen INTEGER NOT NULL,
  PRIMARY KEY (txn_id, line_no));
CREATE TABLE IF NOT EXISTS sim_state (key TEXT PRIMARY KEY, value TEXT);
"""


def make_sales(minute: int) -> list[dict]:
    """minute の1分間の会計(売上と、ときどき取消)を作る"""
    rng = random.Random(minute * 15485863)
    hour = datetime.fromtimestamp(minute * 60, JST).hour
    out = []
    for _ in range(buyers(minute)):
        sold = datetime.fromtimestamp(minute * 60 + rng.randint(0, 59), JST)
        lines = basket(rng, hour)
        sale = {"register_no": rng.choice(REGISTERS), "sold_at": sold, "txn_type": "SALE", "lines": lines}
        out.append(sale)
        if rng.random() < VOID_RATE:   # 打ち間違いなどで、直後に取り消す
            out.append({**sale, "txn_type": "VOID", "void_of": sale, "sold_at": sold.replace(second=min(59, sold.second + 1))})
    return sorted(out, key=lambda t: t["sold_at"])


def insert(db: sqlite3.Connection, txns: list[dict]) -> None:
    for t in txns:
        sign = -1 if t["txn_type"] == "VOID" else 1
        total = sign * sum(l["price_yen"] * l["qty"] for l in t["lines"])
        cur = db.execute(
            "INSERT INTO pos_transaction (store_code, register_no, sold_at, txn_type, ref_txn_id, total_yen, item_count)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (STORE_CODE, t["register_no"], t["sold_at"].strftime("%Y-%m-%d %H:%M:%S"), t["txn_type"],
             t["void_of"].get("txn_id") if "void_of" in t else None, total, sign * sum(l["qty"] for l in t["lines"])))
        t["txn_id"] = cur.lastrowid
        db.executemany(
            "INSERT INTO pos_transaction_line VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(t["txn_id"], i + 1, l["jan"], l["name"], l["category"], sign * l["qty"], l["price_yen"],
              sign * l["qty"] * l["price_yen"]) for i, l in enumerate(t["lines"])])


def main() -> None:
    ap = argparse.ArgumentParser(description="POS 店舗サーバー(SQLite)シミュレータ")
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent / "data" / "pos.db"))
    ap.add_argument("--verbose", action="store_true", help="1分ごとに書いた取引の件数を表示")
    args = ap.parse_args()

    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(args.db)
    # WALモード: 読む側(ゲートウェイ)がいても、書く側(レジ)が待たされない。POSを止めない連携の大前提
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    row = db.execute("SELECT value FROM sim_state WHERE key = 'last_minute'").fetchone()
    last = int(row[0]) if row else int(time.time()) // 60 - BACKFILL_MIN - 1
    held: list[dict] = []        # セルフレジが手元に貯めている取引
    print(f"POS 店舗サーバー起動: {args.db}  Ctrl+Cで終了", flush=True)
    try:
        while True:
            closed = int(time.time()) // 60 - 1       # 締まった最後の1分
            while last < closed:
                last += 1
                sales = make_sales(last)
                held += [t for t in sales if t["register_no"] == SELF_REGISTER]
                now_batch = [t for t in sales if t["register_no"] != SELF_REGISTER]
                flush = (last + 1) % SELF_FLUSH_MIN == 0 or last < closed - SELF_FLUSH_MIN
                if flush:   # 10分ごと(過去分を作っている間は遅らせない)にセルフレジの分もまとめて書く
                    now_batch, held = now_batch + held, []
                with db:    # 1分ぶんを1トランザクションで書く
                    insert(db, now_batch)
                    db.execute("INSERT OR REPLACE INTO sim_state VALUES ('last_minute', ?)", (str(last),))
                if args.verbose:
                    print(f"{datetime.fromtimestamp(last * 60, JST):%H:%M} 取引 {len(now_batch)} 件を書き込み"
                          f"(セルフレジ保留 {len(held)} 件)", flush=True)
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        db.close()


if __name__ == "__main__":
    main()
