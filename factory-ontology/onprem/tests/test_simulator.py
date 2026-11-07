"""Oracle なしで確かめられる範囲のテスト（表定義の構文、生成データの整合、筋書き）。"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import sqlglot
from sqlglot import exp

ONPREM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ONPREM / "simulator"))

from generate import BEARING_LOT, COIL_LOT, TABLE_ORDER, generate  # noqa: E402

END = datetime(2026, 11, 7, 12, 0)


@pytest.fixture(scope="module")
def data():
    return generate(END)


@pytest.fixture(scope="module")
def schema():
    """表名 → {列名: (NOT NULL か, 型の文字列)}、外部キー、主キーを schema.sql から読む。"""
    tables, foreign, primary = {}, [], {}
    for statement in sqlglot.parse((ONPREM / "db" / "schema.sql").read_text(encoding="utf-8"), read="oracle"):
        if not isinstance(statement, exp.Create) or statement.kind != "TABLE":
            continue
        name = statement.this.this.name.upper()
        tables[name] = {}
        for item in statement.this.expressions:
            if isinstance(item, exp.ColumnDef):
                required = any(isinstance(c.kind, (exp.NotNullColumnConstraint, exp.PrimaryKeyColumnConstraint))
                               for c in item.constraints)
                tables[name][item.name.upper()] = required
                for c in item.constraints:
                    if isinstance(c.kind, exp.PrimaryKeyColumnConstraint):
                        primary[name] = [item.name.upper()]
                    if isinstance(c.kind, exp.Reference):
                        target = c.kind.this
                        foreign.append((name, item.name.upper(), target.this.name.upper(), target.expressions[0].name.upper()))
            elif isinstance(item, exp.PrimaryKey):
                primary[name] = [e.name.upper() for e in item.expressions]
    return tables, foreign, primary


def test_datafile_goes_next_to_existing_files():
    oracle_db = pytest.importorskip("oracle_db")
    assert (oracle_db.datafile_path("/opt/oracle/oradata/FREE/FREEPDB1/system01.dbf")
            == "/opt/oracle/oradata/FREE/FREEPDB1/factory_data01.dbf")
    assert oracle_db.datafile_path(r"C:\app\oradata\FREEPDB1\SYSTEM01.DBF") == r"C:\app\oradata\FREEPDB1\factory_data01.dbf"


def test_table_count(schema):
    assert len(schema[0]) == 27
    assert set(schema[0]) == set(TABLE_ORDER)


def test_rows_match_columns(data, schema):
    tables, truth = data
    for name, rows in tables.items():
        assert rows, name
        for r in rows:
            assert set(r) == set(schema[0][name]), name


def test_required_columns_and_primary_keys(data, schema):
    columns, _, primary = schema
    for name, rows in data[0].items():
        required = [c for c, needed in columns[name].items() if needed]
        keys = [tuple(r[c] for c in primary[name]) for r in rows]
        assert len(keys) == len(set(keys)), name
        for r in rows:
            assert all(r[c] is not None for c in required), name


def test_foreign_keys_and_insert_order(data, schema):
    tables = data[0]
    position = {t: i for i, t in enumerate(TABLE_ORDER)}
    for table, column, target, target_column in schema[1]:
        assert position[target] <= position[table]
        known = {r[target_column] for r in tables[target]}
        seen = set()
        for r in tables[table]:
            value = r[column]
            if value is not None:
                assert value in known or (table == target and value in seen), (table, column, value)
            if table == target:
                seen.add(r[target_column])


def test_same_input_gives_same_output():
    assert generate(END) == generate(END)


def test_bearing_scenario(data):
    tables, truth = data
    failure = datetime.fromisoformat(truth["failure_at"])
    critical = [a for a in tables["T_EQP_ALM"] if a["ALM_LV"] == 3]
    assert len(critical) == 1 and critical[0]["SRC_EQP_ID"] == "PRESS3" and critical[0]["HASSEI_TS"] == failure
    on_lot = {r["KIKAI_CD"] for r in tables["T_BUHIN_TORITSUKE"] if r["LOT_NO"] == BEARING_LOT}
    assert on_lot == {"PRS-03", "PRS-07"}
    current = [r for r in tables["T_BUHIN_TORITSUKE"]
               if r["KIKAI_CD"] == "PRS-03" and r["BUHIN_CD"] == "B-BRG01" and r["TORIHAZUSHI_TS"] is None]
    assert len(current) == 1 and current[0]["LOT_NO"] != BEARING_LOT
    vibration = [r["VAL"] for r in tables["T_SENSOR_DATA"]
                 if r["SRC_EQP_ID"] == "PRESS3" and r["METRIC_CD"] == "VIB"
                 and failure - timedelta(hours=2) <= r["KEISOKU_TS"] < failure]
    assert sum(vibration) / len(vibration) > 5


def test_coil_lot_raises_defects_on_two_lines(data):
    tables, _ = data
    orders = {r["SHIJI_NO"]: r["LINE_CD"] for r in tables["T_SEIZO_SHIJI"]}
    affected = {r["SHIJI_NO"] for r in tables["T_SHIJI_LOT"] if r["LOT_NO"] == COIL_LOT}
    assert {orders[o] for o in affected} == {"L01", "L02"}

    def rate(group):
        rows = [r for r in tables["T_SEISAN_JISSEKI"] if r["SHIJI_NO"] in group]
        ng = sum(r["NG_SU"] for r in rows)
        return ng / (ng + sum(r["OK_SU"] for r in rows))

    press = {o for o, line in orders.items() if line in ("L01", "L02")}
    assert rate(affected) > 3 * rate(press - affected)


def test_idle_power_and_night_setup(data):
    tables, _ = data
    sunday = [r for r in tables["T_DENRYOKU"] if r["KEISOKU_TS"].weekday() == 6]
    average = lambda line: sum(r["KWH"] for r in sunday if r["LINE_CD"] == line) / len([r for r in sunday if r["LINE_CD"] == line])
    assert average("L02") > 3 * average("L01")
    setups = [r for r in tables["T_TEISHI_KIROKU"] if r["KIKAI_CD"] == "ASM-02" and r["RIYU_CD"] == "T20"]
    night = {(r["SHURYO_TS"] - r["KAISHI_TS"]).seconds // 60 for r in setups if r["KAISHI_TS"].hour == 0}
    day = {(r["SHURYO_TS"] - r["KAISHI_TS"]).seconds // 60 for r in setups if r["KAISHI_TS"].hour != 0}
    assert night == {45} and day == {15}


def test_duty_roster_covers_failure_and_line_handover(data):
    tables, truth = data
    failure = datetime.fromisoformat(truth["failure_at"])
    on_duty = [r for r in tables["T_TOBAN"] if r["AREA_CD"] == "A10" and r["KAISHI_TS"] <= failure < r["SHURYO_TS"]]
    assert len(on_duty) == 1
    repair = next(r for r in tables["T_HOZEN"] if r["ALM_ID"] is not None)
    assert repair["SHAIN_NO"] == on_duty[0]["SHAIN_NO"]
    leaders = [r for r in tables["M_TANTO_HANI"] if r["YAKU_CD"] == "R-LINE" and r["HANI_CD"] == "L03"]
    assert len(leaders) == 2 and sum(r["SHURYO_DT"] is None for r in leaders) == 1


def test_three_id_systems_and_dirty_rows(data):
    tables, truth = data
    plc = {r["GAIBU_CD"] for r in tables["M_KIKAI_ALIAS"] if r["SYS_KBN"] == "PLC"}
    cmms = {r["GAIBU_CD"] for r in tables["M_KIKAI_ALIAS"] if r["SYS_KBN"] == "CMMS"}
    assert {r["GAIBU_KIKAI_CD"] for r in tables["T_HOZEN"]} <= cmms
    unknown = {r["SRC_EQP_ID"] for r in tables["T_EQP_ALM"]} - plc
    assert unknown == {truth["dirty"]["unknown_equipment"]}
    codes = {r["CD"] for r in tables["M_CODE"] if r["CD_KBN"] == "ALM"}
    assert {r["ALM_CD"] for r in tables["T_EQP_ALM"]} - codes == {truth["dirty"]["unknown_alarm_code"]}
    assert sum(r["NG_SU"] is None for r in tables["T_HINSHITSU_KENSA"]) == truth["dirty"]["null_inspection_ng"]
    assert sum(r["DEL_FLG"] == "1" for r in tables["M_KIKAI"]) == 1


@pytest.mark.parametrize("offset", range(7))
def test_scenarios_hold_on_every_weekday(offset):
    """生成日の曜日によらず、故障は稼働日に起き、ロット不良は 2 ラインに出る。"""
    end = END + timedelta(days=offset)
    tables, truth = generate(end)
    failure = datetime.fromisoformat(truth["failure_at"])
    assert failure.weekday() != 6 and failure < end
    assert any(r["KIKAI_CD"] == "PRS-03" and r["KAISHI_TS"] <= failure < r["SHURYO_TS"] for r in tables["T_SEISAN_JISSEKI"])
    orders = {r["SHIJI_NO"]: r["LINE_CD"] for r in tables["T_SEIZO_SHIJI"]}
    assert {orders[r["SHIJI_NO"]] for r in tables["T_SHIJI_LOT"] if r["LOT_NO"] == COIL_LOT} == {"L01", "L02"}
    assert max(r["UPD_TS"] for rows in tables.values() for r in rows) <= end


def test_timestamps_do_not_exceed_end(data):
    """差分抽出が基準にする UPD_TS は、全行で生成の終了時刻までに収まる。"""
    for name, rows in data[0].items():
        assert max(r["UPD_TS"] for r in rows) <= END, name
