"""
学習用 IoT ゲートウェイ(店舗ゲートウェイ)

  Modbus TCP(空調・ショーケース・電力計)や三菱電機MCプロトコル(冷凍機盤のiQ-R)で機器を定期的に読み取り
    (接続先は機器ごとに points.json の host / port。1台が応答しなくても他の機器は読み続ける)
  → ポイントリスト(points.json)に従って生値を工学値に変換
  → 店舗のシステム(入店カウンタのAPI、POSのDB)からは前回の続きの差分を取る(sources.py、ハンズオン6)
  → JSON にして SQLite のバッファに積む(ストア&フォワード)
  → MQTT(AWS IoT Core なら TLS + X.509 証明書)で送信し、届いたらバッファから消す
  ← クラウドからの命令(…/cmd)を受け取り、安全確認をしてから機器に書き込み、結果(…/cmd_result)を返す

実行例:
  python gateway.py --console                 # 送信せず画面表示だけ(ステップ1)
  python gateway.py --console --dump-raw      # 生のレジスタ値も表示(ワード順の確認)
  python gateway.py --config gateway_config.json   # MQTTで送信(ステップ3以降)
"""
import argparse
import json
import logging
import queue
import sqlite3
import struct
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pymodbus.client import ModbusTcpClient

from sources import load_sources

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


def encode(raw: int, dtype: str) -> list[int]:
    """decode の逆。書き込みは1ワードの型だけに絞る(2ワードの書き込みは途中で値が混ざる事故が起きやすい)"""
    if dtype == "u16" and 0 <= raw <= 0xFFFF:
        return [raw]
    if dtype == "s16" and -0x8000 <= raw <= 0x7FFF:
        return [raw & 0xFFFF]
    raise ValueError(f"{dtype} に書き込めない値です: {raw}")


class ModbusReader:
    """Modbus TCP で読む(空調・ショーケース・電力計など)"""

    def __init__(self, conn: dict):
        self.client = ModbusTcpClient(conn["host"], port=conn["port"], timeout=3)

    def read_words(self, eq: dict, p: dict, count: int) -> list[int]:
        if not self.client.connected:
            self.client.connect()
        rr = self.client.read_holding_registers(p["address"], count=count, device_id=eq["device_id"])
        if rr.isError():
            raise IOError(f"{eq['equipment_id']} {p['name']} 読み取り失敗: {rr}")
        return list(rr.registers)

    def read_bit(self, eq: dict, p: dict) -> int:
        raise ValueError("このキットのModbus読み取りはワード(保持レジスタ)のみ対応")

    def write_word(self, eq: dict, p: dict, raw: int) -> None:
        if not self.client.connected:
            self.client.connect()
        rr = self.client.write_register(p["address"], raw, device_id=eq["device_id"])   # ファンクションコード06
        if rr.isError():
            raise IOError(f"{eq['equipment_id']} {p['name']} 書き込み失敗: {rr}")

    def reset(self) -> None:
        self.client.close()


class McReader:
    """三菱電機 MCプロトコル(3Eフレーム/バイナリ)で読む(MELSEC iQ-R など)"""

    def __init__(self, conn: dict):
        import pymcprotocol
        self.conn = conn
        self.client = pymcprotocol.Type3E(plctype=conn.get("plctype", "iQ-R"))
        self.client.setaccessopt(commtype="binary")
        self.client.soc_timeout = 3
        self.connected = False

    def _ensure(self) -> None:
        if not self.connected:
            self.client.connect(self.conn["host"], self.conn["port"])
            self.connected = True

    def read_words(self, eq: dict, p: dict, count: int) -> list[int]:
        self._ensure()
        # pymcprotocol は符号付きで返すので、16bitの生値(0~65535)に戻してから型変換する
        return [v & 0xFFFF for v in self.client.batchread_wordunits(p["address"], count)]

    def read_bit(self, eq: dict, p: dict) -> int:
        self._ensure()
        return self.client.batchread_bitunits(p["address"], 1)[0]

    def write_word(self, eq: dict, p: dict, raw: int) -> None:
        raise ValueError("このキットの書き込みはModbusのみ対応")

    def reset(self) -> None:
        try:
            self.client.close()
        except Exception:
            pass
        self.connected = False


def read_point(reader, eq: dict, p: dict, dump_raw: bool = False) -> float:
    if p["type"] == "bit":
        value = reader.read_bit(eq, p)
        raw = str(value)
    else:
        raw_vals = reader.read_words(eq, p, REG_COUNT[p["type"]])
        value = round(decode(raw_vals, p["type"]) * p.get("scale", 1), 3)
        raw = " ".join(f"0x{r:04X}" for r in raw_vals)
    if dump_raw:
        print(f"  [raw] {eq['equipment_id']:<10} {str(p['address']):<6} {raw:<14} -> {p['name']}={value}")
    return value


def read_equipment(reader, eq: dict, dump_raw: bool) -> dict:
    """1台分のポイントを読み、{名前: 値} を返す。通信エラー時は例外"""
    return {p["name"]: read_point(reader, eq, p, dump_raw) for p in eq["points"]}


def make_readers(equipment: list[dict]) -> dict:
    """機器IDごとの読み取り口を作る。protocol(省略時は modbus)で読み方を切り替える。
    同じ host:port の機器(1台のModbusゲートウェイの後ろにデバイスIDを変えて並ぶ機器など)は接続を共有する"""
    conns, readers = {}, {}
    for eq in equipment:
        proto = eq.get("protocol", "modbus")
        key = (proto, eq["host"], eq["port"])
        if key not in conns:
            conn = {k: eq[k] for k in ("host", "port", "plctype") if k in eq}
            conns[key] = ModbusReader(conn) if proto == "modbus" else McReader(conn)
        readers[eq["equipment_id"]] = conns[key]
    return readers


# --------------------------------------------------------------------------
# 2. ストア&フォワード用のバッファ(回線が切れても取りこぼさない)
# --------------------------------------------------------------------------
class Buffer:
    def __init__(self, path: Path, max_rows: int = 100_000):
        self.db = sqlite3.connect(path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS queue (id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT, payload TEXT)"
        )
        # 店舗のシステムから「どこまで取ったか」(ウォーターマーク)。ソースごとに1行
        self.db.execute("CREATE TABLE IF NOT EXISTS watermarks (source_id TEXT PRIMARY KEY, value TEXT)")
        self.db.commit()
        self.max_rows = max_rows

    def _insert(self, topic: str, payload: dict) -> None:
        self.db.execute("INSERT INTO queue (topic, payload) VALUES (?, ?)", (topic, json.dumps(payload, ensure_ascii=False)))
        # 溢れたら古いものから捨てる(容量設計も実務では重要)
        self.db.execute(
            "DELETE FROM queue WHERE id <= (SELECT MAX(id) FROM queue) - ?", (self.max_rows,)
        )

    def put(self, topic: str, payload: dict) -> None:
        self._insert(topic, payload)
        self.db.commit()

    def watermark(self, source_id: str):
        row = self.db.execute("SELECT value FROM watermarks WHERE source_id = ?", (source_id,)).fetchone()
        return row[0] if row else None

    def put_with_watermark(self, messages: list[tuple[str, dict]], source_id: str, watermark: str) -> None:
        """取ってきた差分をバッファに積み、ウォーターマークを進める。1つのトランザクションで行うので、
        途中で落ちても「積んだのに印が進まない(=次に二重に取る)」「印だけ進む(=取りこぼす)」が起きない"""
        with self.db:
            for topic, payload in messages:
                self._insert(topic, payload)
            self.db.execute("INSERT OR REPLACE INTO watermarks VALUES (?, ?)", (source_id, watermark))

    def peek(self, n: int) -> list[tuple[int, str, str]]:
        return self.db.execute("SELECT id, topic, payload FROM queue ORDER BY id LIMIT ?", (n,)).fetchall()

    def delete(self, row_id: int) -> None:
        self.db.execute("DELETE FROM queue WHERE id = ?", (row_id,))
        self.db.commit()

    def size(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM queue").fetchone()[0]


# --------------------------------------------------------------------------
# 3. 遠隔操作(クラウド → 機器への書き込み)。読むより事故の影響が大きいので確認を重ねる
# --------------------------------------------------------------------------
class Rejected(Exception):
    """安全確認で断った命令(機器には何もしていない)"""


class CommandLog:
    """実行済みの命令を覚えておく。QoS1 では同じ命令が2回届くことがあるため"""

    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self.db.execute("CREATE TABLE IF NOT EXISTS commands (command_id TEXT PRIMARY KEY, result TEXT)")
        self.db.commit()

    def get(self, command_id: str):
        row = self.db.execute("SELECT result FROM commands WHERE command_id = ?", (command_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, command_id: str, result: dict) -> None:
        self.db.execute("INSERT INTO commands VALUES (?, ?)", (command_id, json.dumps(result, ensure_ascii=False)))
        self.db.commit()


def write_point(cmd: dict, equipment: dict, readers: dict) -> dict:
    # 1. 期限切れの命令は実行しない(回線の遅れで数分後に届いた「設定温度を上げろ」は、もう誰も望んでいないかもしれない)
    if datetime.fromisoformat(cmd["expires_at"].replace("Z", "+00:00")) < datetime.now(timezone.utc):
        raise Rejected("期限切れ")
    # 2. ポイントリストで writable と書かれたポイントだけ書ける(それ以外は読み取り専用)
    eq = equipment.get(cmd["equipment_id"])
    p = next((p for p in eq["points"] if p["name"] == cmd["point"]), None) if eq else None
    if p is None:
        raise Rejected(f"未登録のポイント: {cmd['equipment_id']} {cmd['point']}")
    limits = p.get("writable")
    if not limits:
        raise Rejected(f"書き込み禁止のポイント: {p['name']}")
    # 3. 範囲外の値は書かない(打ち間違いの 2.5 や 250 で機器を壊さない)
    value = float(cmd["value"])
    if not limits["min"] <= value <= limits["max"]:
        raise Rejected(f"範囲外の値: {value}(許可 {limits['min']}〜{limits['max']})")
    # 4. 書く前の値を読み、書いて、読み返して確かめる
    reader = readers[eq["equipment_id"]]
    before = read_point(reader, eq, p)
    scale = p.get("scale", 1)
    reader.write_word(eq, p, encode(round(value / scale), p["type"])[0])
    after = read_point(reader, eq, p)
    if abs(after - value) > scale / 2:
        return {"status": "failed", "reason": f"読み返した値が違う: {after}", "before": before, "after": after}
    return {"status": "done", "before": before, "after": after}


def execute_command(cmd: dict, equipment: dict, readers: dict, done: CommandLog) -> dict:
    prev = done.get(cmd["command_id"])
    if prev:   # 2回目以降は実行せず、前回の結果を返す(冪等性)
        return {**prev, "duplicate": True}
    result = {**cmd, "executed_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")}
    try:
        result.update(write_point(cmd, equipment, readers))
    except Rejected as e:
        result.update(status="rejected", reason=str(e))
    except (KeyError, TypeError, ValueError, AttributeError) as e:   # 項目の不足・値の型違いなど(機器には書いていない)
        result.update(status="rejected", reason=f"命令の形式が不正: {e!r}")
    except Exception as e:   # 通信エラーなど
        readers[cmd["equipment_id"]].reset()
        result.update(status="failed", reason=str(e))
    done.save(cmd["command_id"], result)
    return result


# --------------------------------------------------------------------------
# 4. MQTT 送受信
# --------------------------------------------------------------------------
def make_mqtt_client(cfg: dict, commands: queue.Queue):
    import paho.mqtt.client as mqtt

    m = cfg["mqtt"]
    cmd_filter = f"{m.get('topic_prefix', 'store')}/{cfg['gateway_id']}/+/cmd"
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
    def on_connect(c, u, f, rc, p):
        log.info("MQTT接続: %s", rc)
        # 購読は接続のたびにやり直す(再接続後に購読し忘れて命令が届かなくなる、はよくある不具合)
        c.subscribe(cmd_filter, qos=1)

    def on_message(c, u, msg):
        # MQTTの受信スレッドでは機器に触らず、キューに積んでメインループで実行する
        try:
            cmd = json.loads(msg.payload)
            cmd["equipment_id"] = msg.topic.split("/")[2]   # 宛先はトピックで決める
            commands.put(cmd)
        except (ValueError, AttributeError, TypeError):
            log.warning("命令を読めません: %s %r", msg.topic, msg.payload[:200])

    client.on_connect = on_connect
    client.on_message = on_message
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
    ap.add_argument("--sources", default=str(BASE_DIR / "sources.json"), help="店舗のシステムの接続先(ハンズオン6)")
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
    prefix = cfg.get("mqtt", {}).get("topic_prefix", "store")
    interval = cfg.get("poll_interval_sec", 10)

    readers = make_readers(points["equipment"])
    sources = load_sources(Path(args.sources), gw_id)
    console_marks: dict = {}   # --console のときはバッファを使わないので、ウォーターマークはメモリに持つ
    equipment = {eq["equipment_id"]: eq for eq in points["equipment"]}
    buf = Buffer(BASE_DIR / "buffer.db")
    done = CommandLog(buf.db)
    commands: queue.Queue = queue.Queue()
    client = None if args.console else make_mqtt_client(cfg, commands)
    seq = int(time.time())  # 再起動しても増え続ける連番(重複・欠損チェック用)

    log.info("開始: gateway=%s 機器=%d台 店舗システム=%s 周期=%ss モード=%s",
             gw_id, len(points["equipment"]), ",".join(s.source_id for s in sources) or "なし",
             interval, "console" if args.console else "mqtt")
    while True:
        cycle_start = time.time()
        ts = datetime.now(timezone.utc)   # 時刻は必ずUTCで持つ
        for eq in points["equipment"]:
            reader = readers[eq["equipment_id"]]
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

        # 店舗のシステムからは、前回の続きの差分を取る
        for src in sources:
            mark = console_marks.get(src.source_id) if args.console else buf.watermark(src.source_id)
            try:
                items, new_mark = src.poll(mark)
            except Exception as e:   # 取れなかったら印を進めずに次の周期で取り直す
                log.warning("%s %s", src.source_id, e)
                continue
            messages = [(f"{prefix}/{gw_id}/{src.source_id}/{kind}", p) for kind, p in items]
            if args.console:
                console_marks[src.source_id] = new_mark
                if messages:
                    print(f"{messages[-1][0]} {len(messages)}件(最後の1件: "
                          f"{json.dumps(messages[-1][1], ensure_ascii=False)[:300]}…)")
            else:
                buf.put_with_watermark(messages, src.source_id, new_mark)
            log.info("%s: %d件を取得(ウォーターマーク %s → %s)", src.source_id, len(messages), mark, new_mark)

        if not args.console:
            sent = flush(client, buf)
            log.info("送信 %d 件 / バッファ残 %d 件", sent, buf.size())

        if args.once:
            break
        # 次の周期まで、届いた命令を処理しながら待つ
        while (remaining := cycle_start + interval - time.time()) > 0:
            try:
                cmd = commands.get(timeout=remaining)
            except queue.Empty:
                break
            if not isinstance(cmd.get("command_id"), str) or cmd["equipment_id"] not in equipment:
                log.warning("命令を無視しました(command_id が無い、または未登録の機器): %s", cmd)
                continue
            result = execute_command(cmd, equipment, readers, done)
            log.info("命令 %s %s %s=%s → %s %s", cmd["command_id"][:8], cmd["equipment_id"], cmd.get("point"),
                     cmd.get("value"), result["status"],
                     "(重複のため再実行せず)" if result.get("duplicate") else result.get("reason", ""))
            buf.put(f"{prefix}/{gw_id}/{cmd['equipment_id']}/cmd_result", result)   # 結果もバッファ経由で確実に返す
            flush(client, buf)

    if client:
        time.sleep(2)
        client.loop_stop()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
