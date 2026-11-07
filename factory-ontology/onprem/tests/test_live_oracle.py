"""live.py の更新を実物の Oracle で確かめる。Oracle につながらなければ飛ばす。確定はしない(ロールバックする)。"""

import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from dotenv import load_dotenv

ONPREM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ONPREM / "simulator"))

load_dotenv(ONPREM / ".env")

import oracledb  # noqa: E402

from live import run_tick  # noqa: E402
from oracle_db import connect  # noqa: E402


@pytest.fixture
def conn():
    try:
        connection = connect("FACTORY_APP_USER", "FACTORY_APP_PASSWORD")
    except (SystemExit, oracledb.Error):
        pytest.skip("Oracle につながりません")
    yield connection
    connection.rollback()
    connection.close()


def count(conn, sql, *binds):
    with conn.cursor() as cur:
        cur.execute(sql, list(binds))
        return cur.fetchone()[0]


def test_tick_adds_rows_and_updates_existing_ones(conn):
    now = datetime.now()
    sensors = count(conn, "SELECT COUNT(*) FROM T_SENSOR_DATA")
    tick = run_tick(conn, now, 60, random.Random(1))
    assert count(conn, "SELECT COUNT(*) FROM T_SENSOR_DATA") == sensors + 12
    assert tick.added["T_SEISAN_JISSEKI"] == 4
    assert count(conn, "SELECT COUNT(*) FROM T_TOBAN WHERE AREA_CD = 'A10' AND KAISHI_TS <= :1 AND :2 < SHURYO_TS", now, now) >= 1


def test_production_accumulates_on_the_hourly_row(conn):
    """何度更新しても、実績は時間ごとに 1 行のまま増えていく(グラフを増やさない)。"""
    now = datetime.now()
    run_tick(conn, now, 60, random.Random(1))
    rows = count(conn, "SELECT COUNT(*) FROM T_SEISAN_JISSEKI")
    hour = now.replace(minute=0, second=0, microsecond=0)
    good = count(conn, "SELECT SUM(OK_SU) FROM T_SEISAN_JISSEKI WHERE KAISHI_TS = :1", hour)
    run_tick(conn, now + timedelta(seconds=1), 60, random.Random(2))
    assert count(conn, "SELECT COUNT(*) FROM T_SEISAN_JISSEKI") == rows
    assert count(conn, "SELECT SUM(OK_SU) FROM T_SEISAN_JISSEKI WHERE KAISHI_TS = :1", hour) > good


def test_power_accumulates_on_the_hourly_row(conn):
    now = datetime.now()
    run_tick(conn, now, 60, random.Random(1))
    hour = now.replace(minute=0, second=0, microsecond=0)
    first = count(conn, "SELECT KWH FROM T_DENRYOKU WHERE LINE_CD = 'L01' AND KEISOKU_TS = :1", hour)
    run_tick(conn, now + timedelta(seconds=1), 60, random.Random(2))
    assert count(conn, "SELECT KWH FROM T_DENRYOKU WHERE LINE_CD = 'L01' AND KEISOKU_TS = :1", hour) > first


def test_open_alarm_recovers_on_next_tick_but_unknown_equipment_stays_open(conn):
    now = datetime.now()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO T_EQP_ALM (ALM_ID, SRC_EQP_ID, ALM_CD, ALM_LV, HASSEI_TS, UPD_TS) VALUES (999999, 'CONV1', 'A201', 1, :1, :2)",
                    [now - timedelta(minutes=1), now])
    unknown_open = count(conn, "SELECT COUNT(*) FROM T_EQP_ALM WHERE SRC_EQP_ID = 'PRESS9' AND FUKKI_TS IS NULL")
    tick = run_tick(conn, now, 60, random.Random(1))
    assert any("アラーム復帰 999999" in e for e in tick.events)
    assert count(conn, "SELECT COUNT(*) FROM T_EQP_ALM WHERE ALM_ID = 999999 AND FUKKI_TS IS NOT NULL") == 1
    assert count(conn, "SELECT COUNT(*) FROM T_EQP_ALM WHERE SRC_EQP_ID = 'PRESS9' AND FUKKI_TS IS NULL") == unknown_open


def test_finished_shift_order_is_closed_and_replaced(conn):
    """前の時間帯の実行中の指図は完了になり、新しい指図が始まる。"""
    now = datetime.now()
    run_tick(conn, now, 60, random.Random(1))
    tick = run_tick(conn, now + timedelta(hours=8), 60, random.Random(1))
    assert sum("指図を完了" in e for e in tick.events) == 4
    assert sum("指図を開始" in e for e in tick.events) == 4
