"""
在庫・発注システムを模擬する(学習用、ハンズオン7)

  本部の基幹システムの店舗向け機能のつもりです。POSの売上を取り込んで帳簿上の在庫を減らし、
  発注点を割った商品を自動発注し、入荷したら在庫を増やします。そして、定期的に在庫の一覧を
  CSVファイルに書き出します(ファイル連携)。

  書き出すCSVは、古くからある基幹システムによくある形にしています。
    - 文字コードは Shift_JIS(CP932)、改行は CRLF
    - 見出しは日本語、日時は「2026/10/15 22:30」(日本時間、タイムゾーンなし)
    - 最後の行は「END,明細の件数」(トレーラー)。受け取る側は件数を照合して、途中で切れていないか確かめる
    - 書きかけのファイルを読まれないように、いったん .tmp で書き、書き終わってから .csv に名前を変える

  出力先: simulator/data/outbox/stock_S001_YYYYMMDD_HHMM.csv(10分ごと。実際の店舗では1時間ごとや夜間1回が多い)

  POSの店舗サーバー(pos_server.py)のDBを読み取り専用で読みます。先に pos_server.py を起動してください。

実行:  python simulator/inventory_system.py [--interval-min 10] [--outbox simulator/data/outbox]
"""
import argparse
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from store_model import JST, PRODUCTS, STORE_CODE

DATA = Path(__file__).resolve().parent / "data"
DAILY_PER_WEIGHT = 140    # 売れやすさ1あたりの1日の販売数(store_model の来店・かごのモデルからの概算)
REORDER_DAYS = 0.25       # 発注点 = 何日分の販売数か
ORDER_DAYS = 0.5          # 発注数 = 何日分の販売数か
LEAD_MIN = 120            # 発注してから入荷するまで(分)
HEADER = ["店舗コード", "JANコード", "商品名", "部門", "陳列場所", "売価", "在庫数", "発注点", "発注数", "入荷予定日時", "在庫日時"]


def setup(db: sqlite3.Connection) -> None:
    db.executescript("""
    CREATE TABLE IF NOT EXISTS stock (jan TEXT PRIMARY KEY, qty INTEGER, reorder_point INTEGER, order_qty INTEGER,
                                      pending_qty INTEGER, arrival_at INTEGER);
    CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
    """)
    for p in PRODUCTS:
        daily = p["weight"] * DAILY_PER_WEIGHT
        db.execute("INSERT OR IGNORE INTO stock VALUES (?, ?, ?, ?, 0, NULL)",
                   (p["jan"], round(daily * ORDER_DAYS), round(daily * REORDER_DAYS), round(daily * ORDER_DAYS)))
    db.commit()


def apply_sales(db: sqlite3.Connection, pos_path: Path) -> int:
    """POSの新しい取引の明細だけを読み、在庫を減らす(基幹システムも取引番号で差分を取っている)"""
    row = db.execute("SELECT value FROM meta WHERE key = 'last_txn_id'").fetchone()
    pos = sqlite3.connect(f"file:{pos_path.as_posix()}?mode=ro", uri=True, timeout=3)
    try:
        if row is None:   # 初回は「今の在庫数で棚卸しした」ことにして、これより前の取引は反映しない
            last = pos.execute("SELECT coalesce(max(txn_id), 0) FROM pos_transaction").fetchone()[0]
        else:
            last = int(row[0])
        rows = pos.execute("SELECT max(txn_id) FROM pos_transaction WHERE txn_id > ?", (last,)).fetchone()
        new_last = rows[0] or last
        sold = pos.execute("SELECT jan, sum(qty) FROM pos_transaction_line WHERE txn_id > ? AND txn_id <= ? GROUP BY jan",
                           (last, new_last)).fetchall()
    finally:
        pos.close()
    with db:
        for jan, qty in sold:   # 取消(VOID)は数量がマイナスなので、足せば在庫が戻る
            db.execute("UPDATE stock SET qty = qty - ? WHERE jan = ?", (qty, jan))
        db.execute("INSERT OR REPLACE INTO meta VALUES ('last_txn_id', ?)", (str(new_last),))
    return new_last - last


def receive_and_order(db: sqlite3.Connection, now: int) -> None:
    with db:
        # 入荷予定時刻を過ぎた発注分を在庫に足す
        db.execute("UPDATE stock SET qty = qty + pending_qty, pending_qty = 0, arrival_at = NULL "
                   "WHERE arrival_at IS NOT NULL AND arrival_at <= ?", (now,))
        # 発注点を割っていて、まだ発注していない商品を発注する
        db.execute("UPDATE stock SET pending_qty = order_qty, arrival_at = ? "
                   "WHERE qty <= reorder_point AND pending_qty = 0", (now + LEAD_MIN * 60,))


def export_csv(db: sqlite3.Connection, outbox: Path, now: int) -> Path:
    at = datetime.fromtimestamp(now, JST)
    stock = {r[0]: r for r in db.execute("SELECT jan, qty, reorder_point, order_qty, pending_qty, arrival_at FROM stock")}
    lines = [",".join(HEADER)]
    for p in PRODUCTS:
        _, qty, rp, oq, pending, arrival = stock[p["jan"]]
        due = datetime.fromtimestamp(arrival, JST).strftime("%Y/%m/%d %H:%M") if arrival else ""
        lines.append(",".join(map(str, [STORE_CODE, p["jan"], p["name"], p["category"], p["location"], p["price_yen"],
                                        qty, rp, oq if pending else 0, due, at.strftime("%Y/%m/%d %H:%M")])))
    lines.append(f"END,{len(PRODUCTS)}")
    outbox.mkdir(parents=True, exist_ok=True)
    final = outbox / f"stock_{STORE_CODE}_{at:%Y%m%d_%H%M}.csv"
    tmp = final.with_suffix(".csv.tmp")
    tmp.write_bytes("\r\n".join(lines).encode("cp932") + b"\r\n")
    tmp.replace(final)   # 書き終わってから名前を変える(受け取る側は .csv だけを読む)
    return final


def main() -> None:
    ap = argparse.ArgumentParser(description="在庫・発注システム(CSVファイル出力)シミュレータ")
    ap.add_argument("--interval-min", type=int, default=10, help="CSVを書き出す間隔(分)")
    ap.add_argument("--outbox", default=str(DATA / "outbox"))
    ap.add_argument("--pos-db", default=str(DATA / "pos.db"))
    args = ap.parse_args()

    DATA.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATA / "inventory.db")
    setup(db)
    pos_path, outbox = Path(args.pos_db), Path(args.outbox)
    print(f"在庫・発注システム起動: {args.interval_min}分ごとに {outbox} にCSVを書き出します  Ctrl+Cで終了", flush=True)
    next_run = 0.0
    try:
        while True:
            if time.time() >= next_run:
                if not pos_path.exists():
                    print("POSのDBがまだありません(pos_server.py を起動してください)。5秒後に確認します", flush=True)
                    next_run = time.time() + 5
                    continue
                now = int(time.time())
                n = apply_sales(db, pos_path)
                receive_and_order(db, now)
                f = export_csv(db, outbox, now)
                print(f"取引 {n} 件を反映し、{f.name} を書き出しました", flush=True)
                # 次は区切りのよい時刻(10分ごとなら 00, 10, 20 … 分)
                step = args.interval_min * 60
                next_run = (time.time() // step + 1) * step
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        db.close()


if __name__ == "__main__":
    main()
