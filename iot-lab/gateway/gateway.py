"""
学習用 IoT ゲートウェイ

  Modbus TCP(空調機など)や三菱電機MCプロトコル(iQ-R)で機器を定期的に読み取り
  → ポイントリスト(points.json)に従って生値を工学値に変換
  → JSON にして SQLite のバッファに積む(ストア&フォワード)
  → MQTT(AWS IoT Core なら TLS + X.509 証明書)で送信し、届いたらバッファから消す

実行例:
  python gateway.py --console                 # 送信せず画面表示だけ(ステップ1)
  python gateway.py --console --dump-raw      # 生のレジスタ値も表示(ワード順の確認)
  python gateway.py --config gateway_config.json   # MQTTで送信(ステップ3以降)
"""
import argparse
import json
import logging
import sqlite3
import struct
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pymodbus.client import ModbusTcpClient

log = logging.getLogger("gateway")
BASE_DIR = Path(__file__).resolve().parent

# --------------------------------------------------------------------------
# 1. 生のレジスタ値 → 工学値への変換(ここが現場で一番つまずくところ)
# --------------------------------------------------------------------------
REG_COUNT = {"u16": 1, "s16": 1, "u32_lw": 2, "u32_hw": 2, "f32_hw": 2, "f32_lw": 2}


def decode(regs: list[int], dtype: str) -> float:
    if dtype == "u16":
        return regs[0]
    if dtype == "s16":
        return struct.unpack(">h", struct.pack(">H", regs[0]))[0]
    if dtype == "u32_lw":   # 下位ワードが先
        return (regs[1] << 16) | regs[0]
    if dtype == "u32_hw":   # 上位ワードが先
        return (regs[0] << 16) | regs[1]
    if dtype == "f32_hw":
        return struct.unpack(">f", struct.pack(">HH", regs[0], regs[1]))[0]
    if dtype == "f32_lw":
        return struct.unpack(">f", struct.pack(">HH", regs[1], regs[0]))[0]
    raise ValueError(f"未対応の型: {dtype}")


class ModbusReader:
    """Modbus TCP で読む(空調機・汎用機器)"""

    def __init__(self, cfg: dict):
        self.client = ModbusTcpClient(cfg["host"], port=cfg["port"], timeout=3)

    def read_words(self, eq: dict, p: dict, count: int) -> list[int]:
        if not self.client.connected:
            self.client.connect()
        rr = self.client.read_holding_registers(p["address"], count=count, device_id=eq["device_id"])
        if rr.isError():
            raise IOError(f"{eq['equipment_id']} {p['name']} 読み取り失敗: {rr}")
        return list(rr.registers)

    def read_bit(self, eq: dict, p: dict) -> int:
        raise ValueError("このキットのModbus読み取りはワード(保持レジスタ)のみ対応")

    def reset(self) -> None:
        self.client.close()


class McReader:
    """三菱電機 MCプロトコル(3Eフレーム/バイナリ)で読む(MELSEC iQ-R など)"""

    def __init__(self, cfg: dict):
        import pymcprotocol
        self.cfg = cfg
        self.client = pymcprotocol.Type3E(plctype=cfg.get("plctype", "iQ-R"))
        self.client.setaccessopt(commtype="binary")
        self.client.soc_timeout = 3
        self.connected = False

    def _ensure(self) -> None:
        if not self.connected:
            self.client.connect(self.cfg["host"], self.cfg["port"])
            self.connected = True

    def read_words(self, eq: dict, p: dict, count: int) -> list[int]:
        self._ensure()
        # pymcprotocol は符号付きで返すので、16bitの生値(0~65535)に戻してから型変換する
        return [v & 0xFFFF for v in self.client.batchread_wordunits(p["address"], count)]

    def read_bit(self, eq: dict, p: dict) -> int:
        self._ensure()
        return self.client.batchread_bitunits(p["address"], 1)[0]

    def reset(self) -> None:
        try:
            self.client.close()
        except Exception:
            pass
        self.connected = False


def read_equipment(reader, eq: dict, dump_raw: bool) -> dict:
    """1台分のポイントを読み、{名前: 値} を返す。通信エラー時は例外"""
    metrics = {}
    for p in eq["points"]:
        if p["type"] == "bit":
            raw_vals = [reader.read_bit(eq, p)]
            value = raw_vals[0]
            raw = str(value)
        else:
            raw_vals = reader.read_words(eq, p, REG_COUNT[p["type"]])
            value = decode(raw_vals, p["type"]) * p.get("scale", 1)
            raw = " ".join(f"0x{r:04X}" for r in raw_vals)
        metrics[p["name"]] = round(value, 3)
        if dump_raw:
            print(f"  [raw] {eq['equipment_id']:<10} {str(p['address']):<6} {raw:<14} -> {p['name']}={metrics[p['name']]}")
    return metrics


# --------------------------------------------------------------------------
# 2. ストア&フォワード用のバッファ(回線が切れても取りこぼさない)
# --------------------------------------------------------------------------
class Buffer:
    def __init__(self, path: Path, max_rows: int = 100_000):
        self.db = sqlite3.connect(path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS queue (id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT, payload TEXT)"
        )
        self.db.commit()
        self.max_rows = max_rows

    def put(self, topic: str, payload: dict) -> None:
        self.db.execute("INSERT INTO queue (topic, payload) VALUES (?, ?)", (topic, json.dumps(payload, ensure_ascii=False)))
        # 溢れたら古いものから捨てる(容量設計も実務では重要)
        self.db.execute(
            "DELETE FROM queue WHERE id <= (SELECT MAX(id) FROM queue) - ?", (self.max_rows,)
        )
        self.db.commit()

    def peek(self, n: int) -> list[tuple[int, str, str]]:
        return self.db.execute("SELECT id, topic, payload FROM queue ORDER BY id LIMIT ?", (n,)).fetchall()

    def delete(self, row_id: int) -> None:
        self.db.execute("DELETE FROM queue WHERE id = ?", (row_id,))
        self.db.commit()

    def size(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM queue").fetchone()[0]


# --------------------------------------------------------------------------
# 3. MQTT 送信
# --------------------------------------------------------------------------
def make_mqtt_client(cfg: dict):
    import paho.mqtt.client as mqtt

    m = cfg["mqtt"]
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=m["client_id"],
        protocol=mqtt.MQTTv311,
    )
    tls = m.get("tls")
    if tls:
        client.tls_set(
            ca_certs=str(BASE_DIR / tls["ca"]),
            certfile=str(BASE_DIR / tls["cert"]),
            keyfile=str(BASE_DIR / tls["key"]),
        )
    client.on_connect = lambda c, u, f, rc, p: log.info("MQTT接続: %s", rc)
    client.on_disconnect = lambda c, u, f, rc, p: log.warning("MQTT切断: %s(自動で再接続します)", rc)
    client.reconnect_delay_set(min_delay=1, max_delay=60)   # 指数バックオフ
    client.connect_async(m["host"], m.get("port", 8883), keepalive=60)
    client.loop_start()
    return client


def flush(client, buf: Buffer, batch: int = 200) -> int:
    """バッファの古い順に送る。QoS1 で PUBACK が返ったものだけ消す"""
    sent = 0
    if client is None or not client.is_connected():
        return 0
    for row_id, topic, payload in buf.peek(batch):
        info = client.publish(topic, payload, qos=1)
        try:
            info.wait_for_publish(timeout=5)
        except (RuntimeError, ValueError):
            break
        if not info.is_published():
            break   # 届かなかったので残して次回再送
        buf.delete(row_id)
        sent += 1
    return sent


# --------------------------------------------------------------------------
# メインループ
# --------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="学習用IoTゲートウェイ")
    ap.add_argument("--config", default=str(BASE_DIR / "gateway_config.json"))
    ap.add_argument("--points", default=str(BASE_DIR / "points.json"))
    ap.add_argument("--console", action="store_true", help="MQTT送信せず画面に表示する")
    ap.add_argument("--dump-raw", action="store_true", help="生のレジスタ値も表示する")
    ap.add_argument("--once", action="store_true", help="1回だけ読み取って終了")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        cfg_path = BASE_DIR / "gateway_config.example.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    points = json.loads(Path(args.points).read_text(encoding="utf-8"))

    gw_id = cfg["gateway_id"]
    prefix = cfg.get("mqtt", {}).get("topic_prefix", "lab")
    interval = cfg.get("poll_interval_sec", 10)

    # 機器ごとの protocol に応じて読み取り方法を切り替える(省略時は modbus)
    readers = {}
    for eq in points["equipment"]:
        proto = eq.get("protocol", "modbus")
        if proto not in readers:
            if proto not in cfg:
                raise SystemExit(f"設定ファイルに {proto} の接続先がありません({eq['equipment_id']})")
            readers[proto] = ModbusReader(cfg[proto]) if proto == "modbus" else McReader(cfg[proto])
    buf = Buffer(BASE_DIR / "buffer.db")
    client = None if args.console else make_mqtt_client(cfg)
    seq = int(time.time())  # 再起動しても増え続ける連番(重複・欠損チェック用)

    log.info("開始: gateway=%s 機器=%d台 周期=%ss モード=%s",
             gw_id, len(points["equipment"]), interval, "console" if args.console else "mqtt")
    while True:
        cycle_start = time.time()
        ts = datetime.now(timezone.utc)   # 時刻は必ずUTCで持つ
        for eq in points["equipment"]:
            reader = readers[eq.get("protocol", "modbus")]
            seq += 1
            payload = {
                "message_id": str(uuid.uuid4()),
                "gateway_id": gw_id,
                "equipment_id": eq["equipment_id"],
                "kind": eq["kind"],
                "ts": ts.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "seq": seq,
            }
            try:
                payload["status"] = "ok"
                payload["metrics"] = read_equipment(reader, eq, args.dump_raw)
            except Exception as e:   # 通信エラーも「データ」として送る(欠損の理由が分かる)
                log.warning("%s %s", eq["equipment_id"], e)
                reader.reset()
                payload["status"] = "comm_error"
                payload["metrics"] = {}

            topic = f"{prefix}/{gw_id}/{eq['equipment_id']}/telemetry"
            if args.console:
                print(topic, json.dumps(payload, ensure_ascii=False))
            else:
                buf.put(topic, payload)

        if not args.console:
            sent = flush(client, buf)
            log.info("送信 %d 件 / バッファ残 %d 件", sent, buf.size())

        if args.once:
            break
        time.sleep(max(0, interval - (time.time() - cycle_start)))

    if client:
        time.sleep(2)
        client.loop_stop()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
