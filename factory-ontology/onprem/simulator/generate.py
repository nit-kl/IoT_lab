"""模擬データを生成する。乱数の種と終了時刻が同じなら、結果も同じになる。

筋書き（方針書 8 章）は SCENARIOS にまとめ、正解として ground_truth に残す。
後のステップで、グラフと通知がこの正解どおりに動くかを確かめるために使う。
"""

import random
from collections import defaultdict
from datetime import datetime, timedelta

from masters import (AREAS, CMMS_ID, LINES, MAIN_EQUIPMENT, MAINTENANCE_STAFF, PLC_ID, PRODUCTS, SENSOR_EQUIPMENT,
                     build_masters)

SEED = 20261107
DAYS = 14
SHIFT_HOURS = 8

# 表の投入順（外部キーの親が先）
TABLE_ORDER = [
    "M_KOJO", "M_AREA", "M_LINE", "M_KATASHIKI", "M_KIKAI", "M_KIKAI_ALIAS", "M_BUHIN", "M_BUHIN_KOSEI",
    "M_SHIIRESAKI", "M_HINMOKU", "M_SHAIN", "M_YAKUWARI", "M_TANTO_HANI", "M_CODE", "M_KPI_MOKUHYO",
    "T_TOBAN", "T_LOT", "T_BUHIN_TORITSUKE", "T_SEIZO_SHIJI", "T_SHIJI_LOT", "T_SEISAN_JISSEKI",
    "T_HINSHITSU_KENSA", "T_EQP_ALM", "T_TEISHI_KIROKU", "T_HOZEN", "T_DENRYOKU", "T_SENSOR_DATA",
]

BEARING_LOT = "LB-2609-17"
BEARING_LOT_NEW = "LB-2610-02"
COIL_LOT = "LC-2610-08"
SCENARIOS = {
    "bearing": {"equipment": "PRS-03", "same_lot_equipment": "PRS-07", "lot": BEARING_LOT, "supplier": "S-101"},
    "coil_lot": {"lot": COIL_LOT, "supplier": "S-102", "lines": ["L01", "L02"]},
    "idle_power": {"line": "L02"},
    "night_setup": {"line": "L04"},
}


class Ids:
    """表ごとの連番。"""

    def __init__(self):
        self.next = defaultdict(int)

    def __call__(self, table):
        self.next[table] += 1
        return self.next[table]


def row(ts, **kw):
    return {**kw, "UPD_TS": ts, "DEL_FLG": "0"}


def shift_starts(start, end):
    t = start
    while t < end:
        yield t
        t += timedelta(hours=SHIFT_HOURS)


def is_working(t):
    return t.weekday() != 6  # 日曜は全ライン休止


def generate(end=None, seed=SEED, days=DAYS):
    """(表名 → 行のリスト, 正解) を返す。end は省略時に現在時刻（時単位に切り捨て）。"""
    end = (end or datetime.now()).replace(minute=0, second=0, microsecond=0)
    start = (end - timedelta(days=days)).replace(hour=0)
    rng = random.Random(seed)
    ids = Ids()
    tables = defaultdict(list, build_masters(end))

    failure_at = (end - timedelta(days=1)).replace(hour=10, minute=20)
    while not is_working(failure_at):  # 故障はライン稼働中に起きる
        failure_at -= timedelta(days=1)
    repaired_at = failure_at + timedelta(hours=3, minutes=10)
    coil_from, coil_to = end - timedelta(days=9), end - timedelta(days=7)

    _lots_and_parts(tables, ids, end, repaired_at)
    _duty_roster(tables, ids, start, end)
    _production(tables, ids, rng, start, end, failure_at, repaired_at, coil_from, coil_to)
    alarm_id = _alarms(tables, ids, rng, start, end, failure_at, repaired_at)
    _maintenance(tables, ids, end, failure_at, repaired_at, alarm_id)
    _power(tables, rng, start, end)
    _sensors(tables, rng, start, end, failure_at, repaired_at)

    truth = {
        **SCENARIOS,
        "failure_at": failure_at.isoformat(),
        "repaired_at": repaired_at.isoformat(),
        "coil_window": [coil_from.isoformat(), coil_to.isoformat()],
        "dirty": {"unknown_alarm_code": "A999", "unknown_equipment": "PRESS9", "null_inspection_ng": 1},
    }
    return {t: tables[t] for t in TABLE_ORDER}, truth


def _lots_and_parts(tables, ids, end, repaired_at):
    received = (end - timedelta(days=60)).date()
    for no, part, supplier, qty in [
        (BEARING_LOT, "B-BRG01", "S-101", 40), (BEARING_LOT_NEW, "B-BRG01", "S-101", 40),
        ("LS-2609-03", "B-SEAL1", "S-104", 100), ("LN-2609-11", "B-NOZ01", "S-104", 60),
        ("LC-2610-07", "M-COIL1", "S-102", 8000), (COIL_LOT, "M-COIL1", "S-102", 8000),
        ("LC-2610-09", "M-COIL1", "S-102", 8000), ("LM-2610-01", "M-BOLT1", "S-103", 50000),
    ]:
        tables["T_LOT"].append(row(end - timedelta(days=60), LOT_NO=no, BUHIN_CD=part, SHIIRE_CD=supplier,
                                   UKEIRE_DT=received, SURYO=qty))
    installed = end - timedelta(days=45)
    for equipment, part, lot, removed in [
        ("PRS-03", "B-BRG01", BEARING_LOT, repaired_at), ("PRS-07", "B-BRG01", BEARING_LOT, None),
        ("PRS-03", "B-SEAL1", "LS-2609-03", None), ("WLD-01", "B-NOZ01", "LN-2609-11", None),
    ]:
        tables["T_BUHIN_TORITSUKE"].append(row(removed or installed, TORI_ID=ids("T_BUHIN_TORITSUKE"),
                                               KIKAI_CD=equipment, BUHIN_CD=part, LOT_NO=lot,
                                               TORITSUKE_TS=installed, TORIHAZUSHI_TS=removed))
    tables["T_BUHIN_TORITSUKE"].append(row(repaired_at, TORI_ID=ids("T_BUHIN_TORITSUKE"), KIKAI_CD="PRS-03",
                                           BUHIN_CD="B-BRG01", LOT_NO=BEARING_LOT_NEW, TORITSUKE_TS=repaired_at,
                                           TORIHAZUSHI_TS=None))


def _duty_roster(tables, ids, start, end):
    staff = {"A10": MAINTENANCE_STAFF[:3], "A20": MAINTENANCE_STAFF[3:]}
    for i, t in enumerate(shift_starts(start, end + timedelta(days=1))):
        for area in AREAS:
            tables["T_TOBAN"].append(row(min(t, end), TOBAN_ID=ids("T_TOBAN"), SHAIN_NO=staff[area][i % 3], AREA_CD=area,
                                         KAISHI_TS=t, SHURYO_TS=t + timedelta(hours=SHIFT_HOURS)))


def _coil_lot_for(t, coil_from, coil_to):
    if coil_from <= t < coil_to:
        return COIL_LOT
    return "LC-2610-07" if t < coil_from else "LC-2610-09"


def _production(tables, ids, rng, start, end, failure_at, repaired_at, coil_from, coil_to):
    for t in shift_starts(start, end):
        if not is_working(t):
            continue
        for line, (hin, (_, hin_line, cycle)) in zip(LINES, PRODUCTS.items()):
            assert line == hin_line
            shift_end = t + timedelta(hours=SHIFT_HOURS)
            done = shift_end <= end
            no = f"WO-{t:%y%m%d%H}-{line}"
            quantity = int(3600 * SHIFT_HOURS * 0.8 / cycle)
            state = "DONE" if done else "RUN"
            tables["T_SEIZO_SHIJI"].append(row(min(shift_end, end), SHIJI_NO=no, HIN_CD=hin, LINE_CD=line,
                                               SHIJI_SURYO=quantity, KAISHI_YOTEI_TS=t,
                                               NOKI_DT=(t + timedelta(days=3)).date(), JOTAI_KBN=state))
            press = line in ("L01", "L02")
            lot = _coil_lot_for(t, coil_from, coil_to) if press else "LM-2610-01"
            tables["T_SHIJI_LOT"].append(row(t, SHIJI_NO=no, LOT_NO=lot, SHIYO_SURYO=round(quantity * 0.4, 2)))

            setup = 45 if line == "L04" and t.hour == 0 else 15  # 筋書き: 夜勤の段取り替えが長い
            tables["T_TEISHI_KIROKU"].append(row(t, TEISHI_ID=ids("T_TEISHI_KIROKU"), KIKAI_CD=MAIN_EQUIPMENT[line],
                                                 RIYU_CD="T20", KAISHI_TS=t, SHURYO_TS=t + timedelta(minutes=setup)))

            total_ok = total_ng = 0
            for hour in range(SHIFT_HOURS):
                h = t + timedelta(hours=hour)
                if h >= end:
                    break
                stopped = line == "L01" and h < repaired_at and h + timedelta(hours=1) > failure_at
                pace = 0.15 if stopped else rng.uniform(0.86, 0.96)
                made = int(3600 / cycle * pace)
                rate = _ng_rate(rng, line, h, lot, failure_at)
                ng = int(made * rate)
                total_ok += made - ng
                total_ng += ng
                tables["T_SEISAN_JISSEKI"].append(row(h + timedelta(hours=1), JISSEKI_ID=ids("T_SEISAN_JISSEKI"),
                                                      SHIJI_NO=no, KIKAI_CD=MAIN_EQUIPMENT[line], KAISHI_TS=h,
                                                      SHURYO_TS=h + timedelta(hours=1), OK_SU=made - ng, NG_SU=ng))
            if done:
                code = "F20" if lot == COIL_LOT else "F10" if total_ng else None
                tables["T_HINSHITSU_KENSA"].append(row(shift_end, KENSA_ID=ids("T_HINSHITSU_KENSA"), SHIJI_NO=no,
                                                       KENSA_TS=shift_end, KENSA_SU=total_ok + total_ng,
                                                       NG_SU=total_ng, FURYO_CD=code))
    # 不正データ: 検査数だけ入り、不良数が抜けている行
    tables["T_HINSHITSU_KENSA"][-1]["NG_SU"] = None


def _ng_rate(rng, line, t, lot, failure_at):
    rate = rng.uniform(0.005, 0.012)
    if lot == COIL_LOT:
        rate += rng.uniform(0.05, 0.08)  # 筋書き: コイルロット由来の不良が 2 ラインに集中
    if line == "L01" and failure_at - timedelta(days=3) <= t < failure_at:
        rate += 0.04 * (1 - (failure_at - t) / timedelta(days=3))  # 筋書き: 軸受の摩耗で不良が増える
    return rate


def _alarms(tables, ids, rng, start, end, failure_at, repaired_at):
    def alarm(ts, equipment_plc, code, level, recovered, msg):
        alarm_id = ids("T_EQP_ALM")
        tables["T_EQP_ALM"].append(row(recovered or ts, ALM_ID=alarm_id, SRC_EQP_ID=equipment_plc, ALM_CD=code, ALM_LV=level,
                                       HASSEI_TS=ts, FUKKI_TS=recovered, MSG=msg))
        return alarm_id

    noise = (("CONV1", "A201", "搬送詰まり"), ("CONV2", "A201", "搬送詰まり"), ("WELD1", "A301", "電流値の揺らぎ"),
             ("WELD2", "A301", "電流値の揺らぎ"))
    for _ in range((end - start).days * 2):
        ts = start + timedelta(minutes=rng.randrange(int((end - start).total_seconds() / 60) - 60))
        plc, code, msg = rng.choice(noise)
        recovered = ts + timedelta(minutes=rng.randint(2, 12))
        alarm(ts, plc, code, 1, recovered, msg)
        equipment = next(e for e, p in PLC_ID.items() if p == plc)
        tables["T_TEISHI_KIROKU"].append(row(recovered, TEISHI_ID=ids("T_TEISHI_KIROKU"), KIKAI_CD=equipment, RIYU_CD="T30",
                                             KAISHI_TS=ts, SHURYO_TS=recovered))

    # 筋書き: 軸受の摩耗。振動の警告が増え、最後に主軸が停止する
    for days_before in (3, 2, 2, 1):
        ts = failure_at - timedelta(days=days_before, hours=rng.randint(1, 5))
        alarm(ts, "PRESS3", "A101", 2, ts + timedelta(minutes=5), "振動値が上限に接近")
    critical = alarm(failure_at, "PRESS3", "A103", 3, repaired_at, "主軸異常により非常停止")
    tables["T_TEISHI_KIROKU"].append(row(repaired_at, TEISHI_ID=ids("T_TEISHI_KIROKU"), KIKAI_CD="PRS-03", RIYU_CD="T10",
                                         KAISHI_TS=failure_at, SHURYO_TS=repaired_at))

    # 不正データ: マスタにないアラームコードと、マスタにない設備
    for hours_before in (40, 20, 6):
        ts = end - timedelta(hours=hours_before)
        alarm(ts, "CNC1", "A999", 1, ts + timedelta(minutes=3), "未定義コード")
    for hours_before in (30, 12):
        ts = end - timedelta(hours=hours_before)
        alarm(ts, "PRESS9", "A101", 1, None, "PLC 設定の誤り")
    return critical


def _maintenance(tables, ids, end, failure_at, repaired_at, alarm_id):
    # 同型設備の過去事例（方針書 4 章の問い 5）
    past = end - timedelta(days=11, hours=4)
    tables["T_HOZEN"].append(row(past + timedelta(hours=2), HOZEN_ID=ids("T_HOZEN"), GAIBU_KIKAI_CD=CMMS_ID["PRS-07"], ALM_ID=None,
                                 SHAIN_NO=MAINTENANCE_STAFF[1], CHAKUSHU_TS=past, KANRYO_TS=past + timedelta(hours=2),
                                 SHOCHI_MEMO="主軸軸受から異音。グリス補充で一時回復。次回点検で交換を検討"))
    start = failure_at + timedelta(minutes=25)
    on_duty = next(r["SHAIN_NO"] for r in tables["T_TOBAN"]
                   if r["AREA_CD"] == "A10" and r["KAISHI_TS"] <= failure_at < r["SHURYO_TS"])
    tables["T_HOZEN"].append(row(repaired_at, HOZEN_ID=ids("T_HOZEN"), GAIBU_KIKAI_CD=CMMS_ID["PRS-03"], ALM_ID=alarm_id,
                                 SHAIN_NO=on_duty, CHAKUSHU_TS=start, KANRYO_TS=repaired_at,
                                 SHOCHI_MEMO=f"主軸軸受の損傷を確認し交換。交換品ロット {BEARING_LOT_NEW}"))


def _power(tables, rng, start, end):
    t = start
    while t < end:
        for line in LINES:
            if is_working(t):
                kwh = rng.uniform(70, 85)
            else:
                kwh = rng.uniform(38, 45) if line == "L02" else rng.uniform(6, 10)  # 筋書き: L02 の休止中の電力が大きい
            tables["T_DENRYOKU"].append(row(t + timedelta(hours=1), LINE_CD=line, KEISOKU_TS=t, KWH=round(kwh, 2)))
        t += timedelta(hours=1)


def _sensors(tables, rng, start, end, failure_at, repaired_at):
    t = start
    wear_from = failure_at - timedelta(days=6)
    while t < end:
        for plc in SENSOR_EQUIPMENT:
            vibration = rng.gauss(2.0, 0.12)
            if plc == "PRESS3" and wear_from <= t < repaired_at:
                vibration += 4.0 * min((t - wear_from) / (failure_at - wear_from), 1) ** 2  # 筋書き: 振動の上昇
            tables["T_SENSOR_DATA"].append(row(t, SRC_EQP_ID=plc, METRIC_CD="VIB", KEISOKU_TS=t, VAL=round(vibration, 3)))
            tables["T_SENSOR_DATA"].append(row(t, SRC_EQP_ID=plc, METRIC_CD="TEMP", KEISOKU_TS=t, VAL=round(rng.gauss(42, 1.5), 3)))
        t += timedelta(minutes=10)
