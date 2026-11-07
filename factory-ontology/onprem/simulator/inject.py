"""デモのトラブルを起こす。Oracle に行を入れるだけで、検知と通知はクラウド側が行う。

    python simulator/inject.py bearing   # プレス7号機が主軸の異常で停止(3 号機を壊した軸受と同じロットが付いている)
    python simulator/inject.py lot       # 使用中のコイルロットで、2 ラインの検査の不良が急増
    python simulator/inject.py resolve   # 重大アラームを復旧する(保全の記録も入れる)
"""

import argparse
import sys
from datetime import datetime, timedelta

from dotenv import load_dotenv

from live import next_id
from oracle_db import ONPREM, connect

PLC = "PRESS7"
EQUIPMENT = "PRS-07"
CMMS = "E-0207"
BAD_RATE = 0.09
INSPECTED = 400


def bearing(cur, now):
    alarm = next_id(cur, "T_EQP_ALM", "ALM_ID")
    cur.execute("INSERT INTO T_EQP_ALM (ALM_ID, SRC_EQP_ID, ALM_CD, ALM_LV, HASSEI_TS, MSG, UPD_TS) VALUES (:1, :2, 'A103', 3, :3, :4, :5)",
                [alarm, PLC, now, "主軸異常により非常停止", now])
    cur.execute("INSERT INTO T_TEISHI_KIROKU (TEISHI_ID, KIKAI_CD, RIYU_CD, KAISHI_TS, UPD_TS) VALUES (:1, :2, 'T10', :3, :4)",
                [next_id(cur, "T_TEISHI_KIROKU", "TEISHI_ID"), EQUIPMENT, now, now])
    print(f"発生: アラーム {alarm} {EQUIPMENT} 主軸異常(重大)")


def lot(cur, now):
    made = 0
    for line in ("L01", "L02"):
        cur.execute("SELECT SHIJI_NO FROM T_SEIZO_SHIJI WHERE LINE_CD = :1 AND JOTAI_KBN = 'RUN' ORDER BY KAISHI_YOTEI_TS DESC", [line])
        row = cur.fetchone()
        if row is None:
            sys.exit(f"{line} に実行中の指図がありません。live.py を一度動かしてください")
        cur.execute("INSERT INTO T_HINSHITSU_KENSA (KENSA_ID, SHIJI_NO, KENSA_TS, KENSA_SU, NG_SU, FURYO_CD, UPD_TS) VALUES (:1, :2, :3, :4, :5, 'F20', :6)",
                    [next_id(cur, "T_HINSHITSU_KENSA", "KENSA_ID"), row[0], now, INSPECTED, int(INSPECTED * BAD_RATE), now])
        made += 1
    print(f"発生: {made} ラインの検査で不良率 {BAD_RATE:.0%}")


def resolve(cur, now):
    cur.execute("SELECT ALM_ID, SRC_EQP_ID FROM T_EQP_ALM WHERE ALM_LV = 3 AND FUKKI_TS IS NULL")
    alarms = cur.fetchall()
    for alarm, plc in alarms:
        cur.execute("UPDATE T_EQP_ALM SET FUKKI_TS = :1, UPD_TS = :2 WHERE ALM_ID = :3", [now, now, alarm])
        cur.execute("UPDATE T_TEISHI_KIROKU SET SHURYO_TS = :1, UPD_TS = :2 WHERE RIYU_CD = 'T10' AND SHURYO_TS IS NULL", [now, now])
        cur.execute("SELECT SHAIN_NO FROM T_TOBAN WHERE AREA_CD = 'A10' AND KAISHI_TS <= :1 AND :2 < SHURYO_TS", [now, now])
        duty = cur.fetchone()
        cur.execute("SELECT GAIBU_CD FROM M_KIKAI_ALIAS WHERE SYS_KBN = 'CMMS' AND KIKAI_CD = (SELECT KIKAI_CD FROM M_KIKAI_ALIAS WHERE SYS_KBN = 'PLC' AND GAIBU_CD = :1)", [plc])
        cmms = cur.fetchone()
        cur.execute("INSERT INTO T_HOZEN (HOZEN_ID, GAIBU_KIKAI_CD, ALM_ID, SHAIN_NO, CHAKUSHU_TS, KANRYO_TS, SHOCHI_MEMO, UPD_TS) VALUES (:1, :2, :3, :4, :5, :6, :7, :8)",
                    [next_id(cur, "T_HOZEN", "HOZEN_ID"), cmms[0] if cmms else CMMS, alarm, duty[0] if duty else "2001", now - timedelta(minutes=30), now,
                     "主軸軸受を交換し、振動が正常範囲に戻ったことを確認", now])
        print(f"復旧: アラーム {alarm} {plc}")
    if not alarms:
        print("未復旧の重大アラームはありません")


ACTIONS = {"bearing": bearing, "lot": lot, "resolve": resolve}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=list(ACTIONS))
    args = parser.parse_args()
    load_dotenv(ONPREM / ".env")
    now = datetime.now()
    with connect("FACTORY_APP_USER", "FACTORY_APP_PASSWORD") as conn, conn.cursor() as cur:
        ACTIONS[args.action](cur, now)
        conn.commit()


if __name__ == "__main__":
    main()
