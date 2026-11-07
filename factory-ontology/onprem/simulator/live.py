"""動いている工場をまねて、Oracle を更新し続ける。更新した内容を画面に流す。

    python simulator/live.py                 # 60 秒ごとに更新(Ctrl+C で停止)
    python simulator/live.py --interval 10
    python simulator/live.py --once          # 1 回だけ更新

追加だけでなく更新も起こす(アラームの復帰、指図の完了、電力量の積み上げ)。差分抽出が両方を拾えることを
確かめるためと、デモで「DB が動いている」ことを見せるため。
"""

import argparse
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import oracledb
from dotenv import load_dotenv

from masters import AREAS, LINES, MAIN_EQUIPMENT, MAINTENANCE_STAFF, PLC_ID, PRODUCTS, SENSOR_EQUIPMENT
from oracle_db import ONPREM, connect, env, identifier

SHIFT_HOURS = 8
ORDER_DAYS_TO_DUE = 3
MATERIAL_LOT = "LC-2610-09"
BOLT_LOT = "LM-2610-01"
ALARM_CHANCE = 0.35
NOISE_ALARMS = (("CONV1", "A201", "搬送詰まり"), ("CONV2", "A201", "搬送詰まり"), ("WELD1", "A301", "電流値の揺らぎ"),
                ("WELD2", "A301", "電流値の揺らぎ"))


@dataclass
class Tick:
    """1 回の更新で実行した内容。"""

    added: dict = field(default_factory=dict)
    events: list = field(default_factory=list)  # 画面に出す出来事

    def count(self, table, n=1):
        self.added[table] = self.added.get(table, 0) + n


def next_id(cur, table, column):
    cur.execute(f"SELECT NVL(MAX({column}), 0) + 1 FROM {table}")
    return cur.fetchone()[0]


def shift_start(now):
    return now.replace(hour=now.hour - now.hour % SHIFT_HOURS, minute=0, second=0, microsecond=0)


def ensure_roster(cur, now, tick):
    """今の時間帯の当番がなければ追加する(seed から日がたっても当番が空にならないように)。"""
    start = shift_start(now)
    for area_index, area in enumerate(AREAS):
        cur.execute("SELECT COUNT(*) FROM T_TOBAN WHERE AREA_CD = :1 AND KAISHI_TS <= :2 AND :3 < SHURYO_TS", [area, now, now])
        if cur.fetchone()[0]:
            continue
        staff = MAINTENANCE_STAFF[area_index * 3 + (start.toordinal() * 3 + start.hour // SHIFT_HOURS) % 3]
        cur.execute("INSERT INTO T_TOBAN (TOBAN_ID, SHAIN_NO, AREA_CD, KAISHI_TS, SHURYO_TS, UPD_TS) VALUES (:1, :2, :3, :4, :5, :6)",
                    [next_id(cur, "T_TOBAN", "TOBAN_ID"), staff, area, start, start + timedelta(hours=SHIFT_HOURS), now])
        tick.count("T_TOBAN")
        tick.events.append(f"当番を登録  {AREAS[area]} 社員 {staff}")


def current_order(cur, line, now, tick):
    """ラインの実行中の指図を返す。時間帯が変わっていれば完了にして、新しい指図を作る。"""
    cur.execute("SELECT SHIJI_NO, KAISHI_YOTEI_TS FROM T_SEIZO_SHIJI WHERE LINE_CD = :1 AND JOTAI_KBN = 'RUN' "
                "ORDER BY KAISHI_YOTEI_TS DESC", [line])
    running = cur.fetchall()
    start = shift_start(now)
    for number, planned in running:
        if planned >= start:
            return number
        cur.execute("UPDATE T_SEIZO_SHIJI SET JOTAI_KBN = 'DONE', UPD_TS = :1 WHERE SHIJI_NO = :2", [now, number])
        tick.count("T_SEIZO_SHIJI")
        tick.events.append(f"指図を完了  {number}")
    product, (_, _, cycle) = next((p, v) for p, v in PRODUCTS.items() if v[1] == line)
    number = f"WO-{start:%y%m%d%H}-{line}"
    quantity = int(3600 * SHIFT_HOURS * 0.8 / cycle)
    cur.execute("INSERT INTO T_SEIZO_SHIJI (SHIJI_NO, HIN_CD, LINE_CD, SHIJI_SURYO, KAISHI_YOTEI_TS, NOKI_DT, JOTAI_KBN, UPD_TS) "
                "VALUES (:1, :2, :3, :4, :5, :6, 'RUN', :7)",
                [number, product, line, quantity, start, (start + timedelta(days=ORDER_DAYS_TO_DUE)).date(), now])
    lot = MATERIAL_LOT if line in ("L01", "L02") else BOLT_LOT
    cur.execute("INSERT INTO T_SHIJI_LOT (SHIJI_NO, LOT_NO, SHIYO_SURYO, UPD_TS) VALUES (:1, :2, :3, :4)",
                [number, lot, round(quantity * 0.4, 2), now])
    tick.count("T_SEIZO_SHIJI")
    tick.count("T_SHIJI_LOT")
    tick.events.append(f"指図を開始  {number}")
    return number


def add_production(cur, now, interval, rng, tick):
    """実績は時間ごとの行に積み上げる。更新のたびに行を増やすと、グラフが増え続けるため。"""
    hour = now.replace(minute=0, second=0, microsecond=0)
    for line, equipment in MAIN_EQUIPMENT.items():
        order = current_order(cur, line, now, tick)
        cycle = next(v[2] for v in PRODUCTS.values() if v[1] == line)
        made = max(1, int(interval / cycle * rng.uniform(0.86, 0.96)))
        defects = int(made * rng.uniform(0.005, 0.012))
        cur.execute("UPDATE T_SEISAN_JISSEKI SET OK_SU = OK_SU + :1, NG_SU = NG_SU + :2, SHURYO_TS = :3, UPD_TS = :4 "
                    "WHERE SHIJI_NO = :5 AND KIKAI_CD = :6 AND KAISHI_TS = :7",
                    [made - defects, defects, now, now, order, equipment, hour])
        if cur.rowcount == 0:
            cur.execute("INSERT INTO T_SEISAN_JISSEKI (JISSEKI_ID, SHIJI_NO, KIKAI_CD, KAISHI_TS, SHURYO_TS, OK_SU, NG_SU, UPD_TS) "
                        "VALUES (:1, :2, :3, :4, :5, :6, :7, :8)",
                        [next_id(cur, "T_SEISAN_JISSEKI", "JISSEKI_ID"), order, equipment, hour, now, made - defects, defects, now])
        tick.count("T_SEISAN_JISSEKI")


def add_sensors(cur, now, rng, tick):
    stamp = now.replace(microsecond=0)
    rows = []
    for plc in SENSOR_EQUIPMENT:
        rows.append([plc, "VIB", stamp, round(rng.gauss(2.0, 0.12), 3), now])
        rows.append([plc, "TEMP", stamp, round(rng.gauss(42, 1.5), 3), now])
    cur.executemany("INSERT INTO T_SENSOR_DATA (SRC_EQP_ID, METRIC_CD, KEISOKU_TS, VAL, UPD_TS) VALUES (:1, :2, :3, :4, :5)", rows)
    tick.count("T_SENSOR_DATA", len(rows))


def add_power(cur, now, interval, rng, tick):
    """電力量は時間ごとの行に積み上げる(既存の行の更新になる)。"""
    hour = now.replace(minute=0, second=0, microsecond=0)
    for line in LINES:
        kwh = round(rng.uniform(70, 85) * interval / 3600, 2)
        cur.execute("UPDATE T_DENRYOKU SET KWH = KWH + :1, UPD_TS = :2 WHERE LINE_CD = :3 AND KEISOKU_TS = :4", [kwh, now, line, hour])
        if cur.rowcount == 0:
            cur.execute("INSERT INTO T_DENRYOKU (LINE_CD, KEISOKU_TS, KWH, UPD_TS) VALUES (:1, :2, :3, :4)", [line, hour, kwh, now])
        tick.count("T_DENRYOKU")


def recover_alarms(cur, now, tick):
    """前回出した軽微なアラームを復帰させる(既存の行の更新)。マスタにない設備の行は対象にしない。"""
    known = list(PLC_ID.values())
    binds = ", ".join(f":{i + 2}" for i in range(len(known)))
    cur.execute(f"SELECT ALM_ID, SRC_EQP_ID, HASSEI_TS FROM T_EQP_ALM WHERE ALM_LV = 1 AND FUKKI_TS IS NULL AND HASSEI_TS < :1 "
                f"AND SRC_EQP_ID IN ({binds})", [now, *known])
    for alarm, plc, started in cur.fetchall():
        cur.execute("UPDATE T_EQP_ALM SET FUKKI_TS = :1, UPD_TS = :2 WHERE ALM_ID = :3", [now, now, alarm])
        equipment = next(e for e, p in PLC_ID.items() if p == plc)
        cur.execute("INSERT INTO T_TEISHI_KIROKU (TEISHI_ID, KIKAI_CD, RIYU_CD, KAISHI_TS, SHURYO_TS, UPD_TS) VALUES (:1, :2, 'T30', :3, :4, :5)",
                    [next_id(cur, "T_TEISHI_KIROKU", "TEISHI_ID"), equipment, started, now, now])
        tick.count("T_EQP_ALM")
        tick.count("T_TEISHI_KIROKU")
        tick.events.append(f"アラーム復帰 {alarm} {plc}")


def maybe_raise_alarm(cur, now, rng, tick):
    if rng.random() >= ALARM_CHANCE:
        return
    plc, code, message = rng.choice(NOISE_ALARMS)
    alarm = next_id(cur, "T_EQP_ALM", "ALM_ID")
    cur.execute("INSERT INTO T_EQP_ALM (ALM_ID, SRC_EQP_ID, ALM_CD, ALM_LV, HASSEI_TS, MSG, UPD_TS) VALUES (:1, :2, :3, 1, :4, :5, :6)",
                [alarm, plc, code, now, message, now])
    tick.count("T_EQP_ALM")
    tick.events.append(f"アラーム発生 {alarm} {plc} {message}")


def run_tick(conn, now, interval, rng):
    """1 回分の更新を実行する。確定は呼び出し側で行う。"""
    tick = Tick()
    with conn.cursor() as cur:
        ensure_roster(cur, now, tick)
        add_production(cur, now, interval, rng, tick)
        add_sensors(cur, now, rng, tick)
        add_power(cur, now, interval, rng, tick)
        recover_alarms(cur, now, tick)
        maybe_raise_alarm(cur, now, rng, tick)
    return tick


def show(now, tick):
    tables = "  ".join(f"{table} {count}" for table, count in sorted(tick.added.items()))
    print(f"{now:%H:%M:%S} 更新 {sum(tick.added.values())} 行: {tables}")
    for event in tick.events:
        print(f"         ▶ {event}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--interval", type=int, default=60, help="更新の間隔(秒)")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    load_dotenv(ONPREM / ".env")
    identifier(env("FACTORY_APP_USER"))
    rng = random.Random()
    while True:
        now = datetime.now()
        try:
            # 毎回つなぎ直す。Oracle が再起動しても、そのまま動き続けるため
            with connect("FACTORY_APP_USER", "FACTORY_APP_PASSWORD") as conn:
                tick = run_tick(conn, now, args.interval, rng)
                conn.commit()
            show(now, tick)
        except oracledb.Error as e:
            print(f"{now:%H:%M:%S} 更新に失敗しました。次回にやり直します: {e}")
        if args.once:
            return
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            return


if __name__ == "__main__":
    main()
