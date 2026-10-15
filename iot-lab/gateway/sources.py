"""
店舗のシステム(機器ではないもの)からデータを取り込む部品(ハンズオン6)

  設備はModbusなどで「今の値」を周期的に読みますが、店舗のシステムは「前回からの増えた分(差分)」を取ります。
  どこまで取ったか(ウォーターマーク)を覚えておき、次はその続きから取るのが基本です。

    PeopleCounterSource  入店カウンタのREST API。1分ごとの集計を「時刻」で続きから取る
    PosDbSource          店舗サーバー(POS)のDB。取引を「取引番号(連番)」で続きから取る

  どちらも poll(前回のウォーターマーク) → (送るメッセージの一覧, 新しいウォーターマーク) を返すだけで、
  ウォーターマークの保存はゲートウェイがバッファへの追加と同じトランザクションで行います
  (「バッファに積んだのに印が進まない」「印は進んだのにバッファに無い」を起こさないため)。
"""
import json
import sqlite3
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
BASE_DIR = Path(__file__).resolve().parent


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class PeopleCounterSource:
    """入店カウンタ(メーカーのREST API)"""

    MAX_PAGES = 5   # 1周期で取りに行く最大ページ数(長く止まっていた後でも、1周期を長引かせない)

    def __init__(self, cfg: dict, gateway_id: str):
        self.cfg, self.gateway_id = cfg, gateway_id
        self.source_id = cfg["source_id"]

    def get(self, since: str) -> dict:
        url = f"{self.cfg['url']}/api/v1/counts?since={since}&limit=60"
        req = urllib.request.Request(url, headers={"X-API-Key": self.cfg["api_key"]})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:   # タイムアウトは必ず付ける(付けないと永久に待つことがある)
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise RuntimeError("APIキーが違います(401)。sources.json の api_key を確認してください") from e
            raise RuntimeError(f"入店カウンタのAPIがエラーを返しました({e.code})。次の周期で取り直します") from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise RuntimeError(f"入店カウンタのAPIに接続できません({e})。次の周期で取り直します") from e

    def poll(self, watermark: str | None) -> tuple[list[tuple[str, dict]], str]:
        since = watermark or iso_utc(datetime.now(timezone.utc) - timedelta(minutes=self.cfg.get("backfill_minutes", 120)))
        out = []
        for _ in range(self.MAX_PAGES):
            res = self.get(since)
            for b in res["bins"]:
                out.append(("people_count", {
                    "message_id": str(uuid.uuid4()),
                    "gateway_id": self.gateway_id,
                    "source_id": self.source_id,
                    "sensor_id": res["sensor_id"],
                    "kind": "people_count",
                    "ts": b["start"].replace("Z", ".000Z"),   # 1分の始まり。テレメトリと同じ形(ミリ秒付きUTC)にそろえる
                    "bin_end": b["end"].replace("Z", ".000Z"),
                    # メーカーの項目名(in/out)は、SQLの予約語と重なるので、こちらの名前に付け替える
                    "in_count": b["in"],
                    "out_count": b["out"],
                }))
            since = res["next_since"]
            if not res["has_more"]:
                break
        return out, since


class PosDbSource:
    """店舗サーバー(POS)のDB。読み取り専用で開き、取引番号で差分を取る"""

    def __init__(self, cfg: dict, gateway_id: str):
        self.cfg, self.gateway_id = cfg, gateway_id
        self.source_id = cfg["source_id"]
        self.path = (BASE_DIR / cfg["path"]).resolve()

    def poll(self, watermark: str | None) -> tuple[list[tuple[str, dict]], str]:
        last_id = int(watermark or 0)
        if not self.path.exists():
            raise RuntimeError(f"POSのDBが見つかりません: {self.path}(pos_server.py は起動していますか?)")
        # mode=ro: 読み取り専用で開く。連携のせいでPOSのデータを壊す・ロックする事故を防ぐ
        db = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True, timeout=3)
        db.row_factory = sqlite3.Row
        try:
            txns = db.execute(
                "SELECT * FROM pos_transaction WHERE txn_id > ? ORDER BY txn_id LIMIT ?",
                (last_id, self.cfg.get("batch", 200))).fetchall()
            lines = {}
            if txns:
                for l in db.execute("SELECT * FROM pos_transaction_line WHERE txn_id BETWEEN ? AND ? ORDER BY txn_id, line_no",
                                    (txns[0]["txn_id"], txns[-1]["txn_id"])):
                    lines.setdefault(l["txn_id"], []).append(l)
        finally:
            db.close()   # 接続を持ちっぱなしにしない

        out = []
        for t in txns:
            # POSの時刻は「日本時間・タイムゾーンなし」。ここでUTCに直してから送る
            sold_at = datetime.strptime(t["sold_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=JST)
            out.append(("pos_txn", {
                "message_id": str(uuid.uuid4()),
                "gateway_id": self.gateway_id,
                "source_id": self.source_id,
                "kind": "pos_txn",
                "ts": iso_utc(sold_at),
                "store_code": t["store_code"],
                "txn_id": t["txn_id"],
                "register_no": t["register_no"],
                "txn_type": t["txn_type"],
                "ref_txn_id": t["ref_txn_id"],
                "total_yen": t["total_yen"],
                "item_count": t["item_count"],
                "items": [{"jan": l["jan"], "product_name": l["product_name"], "category": l["category"],
                           "qty": l["qty"], "unit_price_yen": l["unit_price_yen"], "amount_yen": l["amount_yen"]}
                          for l in lines.get(t["txn_id"], [])],
            }))
        return out, str(txns[-1]["txn_id"] if txns else last_id)


SOURCE_TYPES = {"people_counter": PeopleCounterSource, "pos_db": PosDbSource}


def load_sources(path: Path, gateway_id: str) -> list:
    if not path.exists():
        return []
    cfg = json.loads(path.read_text(encoding="utf-8"))
    return [SOURCE_TYPES[s["type"]](s, gateway_id) for s in cfg["sources"] if s.get("enabled")]
