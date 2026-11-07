"""グラフの検証。方針書 4 章の問いが、模擬データの正解どおりに答えられることを確かめる(Oracle は不要)。"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import sqlglot
from pyoxigraph import NamedNode, Quad, RdfFormat, Store, serialize
from sqlglot import exp

GRAPH_DIR = Path(__file__).resolve().parents[1]
ONPREM = GRAPH_DIR.parent / "onprem"
sys.path[:0] = [str(GRAPH_DIR), str(GRAPH_DIR / "tools"), str(ONPREM / "simulator")]

from generate import BEARING_LOT, COIL_LOT, generate  # noqa: E402
from kg.common import ID  # noqa: E402
from kg.store import load_query, open_store, query_params, run  # noqa: E402
from kg.transform import convert_tables, literal, load_mapping  # noqa: E402

END = datetime(2026, 11, 7, 12, 0)
QUERY_DIR = GRAPH_DIR / "queries"


@pytest.fixture(scope="module")
def source():
    return generate(END)


@pytest.fixture(scope="module")
def result(source):
    return convert_tables(source[0])


@pytest.fixture(scope="module")
def store(result):
    return open_store(quads=result.quads)


def ask(store, name, **params):
    return run(store, load_query(name, **params))


def test_every_column_is_used_or_explained():
    """全列について、グラフに入れるか、入れない理由があること。"""
    statements = sqlglot.parse((ONPREM / "db" / "schema.sql").read_text(encoding="utf-8"), read="oracle")
    columns = {s.this.this.name: {c.name for c in s.this.expressions if isinstance(c, exp.ColumnDef)}
               for s in statements if isinstance(s, exp.Create) and s.kind == "TABLE"}
    mapping = load_mapping()
    assert {t["name"] for t in mapping} == set(columns)
    for table in mapping:
        if "not_in_graph" in table:
            continue
        text = repr({k: v for k, v in table.items() if k != "skip"})
        unused = {c for c in columns[table["name"]] - {"UPD_TS", "DEL_FLG"} if c not in text} - set(table.get("skip", {}))
        assert not unused, (table["name"], unused)


def test_row_counts_reconcile(source, result):
    """元の行 = 変換 + 隔離 + 論理削除。"""
    for name, counts in result.counts.items():
        assert counts["source"] == len(source[0][name]), name
        if "not_in_graph" not in counts:
            assert counts["source"] == counts["converted"] + counts["quarantined"] + counts["deleted"], name


def test_node_counts_match_source(store, source):
    tables = source[0]
    for klass, table in [("xf:Alarm", "T_EQP_ALM"), ("xf:ProductionRun", "T_SEISAN_JISSEKI"), ("xf:WorkOrder", "T_SEIZO_SHIJI"),
                         ("xf:DutyShift", "T_TOBAN"), ("xf:Stoppage", "T_TEISHI_KIROKU"), ("xf:MaintenanceWork", "T_HOZEN")]:
        count = int(run(store, f"SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE {{ ?s a {klass} }}")[0]["n"])
        quarantined = 2 if table == "T_EQP_ALM" else 0
        assert count == len(tables[table]) - quarantined, klass


def test_sensitive_and_time_series_data_stays_out(result, source):
    text = " ".join(q.object.value for q in result.quads)
    for row in source[0]["M_SHAIN"]:
        assert row["SHAIN_NM"] not in text
    assert result.counts["T_DENRYOKU"]["converted"] == 0
    assert len({q.subject for q in result.quads if "observable/" in q.subject.value}) == 12


def test_cq1_current_parts_show_the_replaced_lot(store):
    rows = ask(store, "cq1_current_parts", EQUIPMENT="PRS-03")
    assert {(r["part"], r["lot"], r["supplier"]) for r in rows} == {
        ("主軸軸受", "LB-2610-02", "北斗ベアリング"), ("オイルシール", "LS-2609-03", "三協部品")}


def test_cq2_same_lot_finds_both_presses(store):
    rows = ask(store, "cq2_same_lot_equipment", LOT=BEARING_LOT)
    assert {r["equipmentId"]: r["removed"] is None for r in rows} == {"PRS-03": False, "PRS-07": True}
    assert {r["line"] for r in rows} == {"プレスライン1", "プレスライン2"}


def test_cq3_lot_impact_reaches_orders_on_two_lines(store, source):
    tables = source[0]
    rows = ask(store, "cq3_lot_impact", LOT=COIL_LOT)
    expected = {r["SHIJI_NO"] for r in tables["T_SHIJI_LOT"] if r["LOT_NO"] == COIL_LOT}
    assert {r["order"] for r in rows} == expected
    assert {r["line"] for r in rows} == {"プレスライン1", "プレスライン2"}
    assert {r["product"] for r in rows} == {"ブラケットA", "ハウジングB"}
    assert {r["defectType"] for r in rows if r["inspected"]} == {"割れ"}


def test_cq4_on_duty_is_time_dependent(store, source):
    tables, truth = source
    at = datetime.fromisoformat(truth["failure_at"])
    for moment in (at, at + timedelta(hours=8), at + timedelta(hours=16)):
        expected = [r["SHAIN_NO"] for r in tables["T_TOBAN"] if r["AREA_CD"] == "A10" and r["KAISHI_TS"] <= moment < r["SHURYO_TS"]]
        rows = ask(store, "cq4_on_duty", EQUIPMENT="PRS-03-M", AT=moment.isoformat() + "+09:00")  # 子設備からでも上位エリアへたどれる
        assert [r["employeeId"] for r in rows] == expected and len(expected) == 1
    assert ask(store, "cq4_on_duty", EQUIPMENT="PRS-03", AT=at.isoformat() + "+09:00")[0]["role"] == "保全当番"


def test_cq5_similar_case_on_same_model(store):
    rows = ask(store, "cq5_similar_cases", EQUIPMENT="PRS-03")
    assert [r["equipmentId"] for r in rows] == ["PRS-07", "PRS-03"]
    assert "グリス補充" in rows[0]["memo"] and rows[0]["model"] == "油圧プレス PR500"


@pytest.mark.parametrize("identifier", ["PRS-03", "PRESS3", "E-0103"])
def test_cq6_any_id_reaches_same_equipment(store, identifier):
    assert ask(store, "cq6_find_by_any_id", ID=identifier) == [
        {"equipmentId": "PRS-03", "equipment": "プレス3号機", "plcId": "PRESS3", "cmmsId": "E-0103"}]


def test_cq7_role_follows_handover(store):
    today = END.date()
    before = ask(store, "cq7_role_on_date", EQUIPMENT="ASM-01", DATE=(today - timedelta(days=7)).isoformat())
    after = ask(store, "cq7_role_on_date", EQUIPMENT="ASM-01", DATE=(today - timedelta(days=2)).isoformat())
    assert [r["employeeId"] for r in before] == ["1103"]
    assert [r["employeeId"] for r in after] == ["1105"]


def test_cq8_reports_every_seeded_problem(store, source):
    truth = source[1]["dirty"]
    rows = {(r["type"], r["value"]): r for r in ask(store, "cq8_data_issues")}
    assert rows["unknown_code", truth["unknown_alarm_code"]]["rows"] == "3"
    assert rows["unknown_equipment", truth["unknown_equipment"]]["rows"] == "2"
    assert rows["unknown_equipment", truth["unknown_equipment"]]["quarantined"] == "true"
    assert rows["missing_value", None]["rows"] == "1"
    assert len(rows) == 3


def test_deleted_equipment_and_quarantined_rows_are_absent(store):
    assert not run(store, 'ASK { ?e xf:equipmentId "CNV-99" }')
    assert not run(store, 'ASK { ?e xf:plcId "CONV99" }')
    assert not run(store, 'ASK { ?a a xf:Alarm ; xf:sourceEquipmentId "PRESS9" }')
    assert not run(store, "ASK { ?a a xf:Alarm . FILTER NOT EXISTS { ?a xf:onEquipment ?e } }")


def test_unknown_code_alarm_keeps_its_equipment_and_raw_code(store):
    rows = run(store, 'SELECT ?id WHERE { ?a xf:rawCode "A999" ; xf:onEquipment / xf:equipmentId ?id . '
                      'FILTER NOT EXISTS { ?a xf:alarmCode ?c } }')
    assert [r["id"] for r in rows] == ["CNC-01"] * 3


def test_rows_can_be_replaced_through_their_named_graph(result):
    """論理削除や更新は、その行のグラフを消すだけで反映できる(ステップ 4 の前提)。"""
    store = open_store(quads=result.quads)
    alarm = NamedNode(ID + "alarm/1")
    graph = next(q.graph_name for q in result.quads if q.subject == alarm)
    assert run(store, f"ASK {{ <{alarm.value}> a xf:Alarm }}")
    store.remove_graph(graph)
    assert not run(store, f"ASK {{ <{alarm.value}> a xf:Alarm }}")


def test_datetimes_carry_timezone():
    assert literal(datetime(2026, 11, 6, 10, 20), "datetime").value == "2026-11-06T10:20:00+09:00"


def test_query_parameters_reject_injection(store):
    with pytest.raises(ValueError):
        load_query("cq1_current_parts", EQUIPMENT='x" } DELETE')
    with pytest.raises(ValueError):
        load_query("cq1_current_parts")
    assert all(query_params(p.stem) or p.stem == "cq8_data_issues" for p in QUERY_DIR.glob("*.rq"))


@pytest.fixture(scope="module")
def shacl_data(result, tmp_path_factory):
    from validate import load_data
    path = tmp_path_factory.mktemp("kg") / "current.nq"
    serialize(result.quads, output=str(path), format=RdfFormat.N_QUADS)
    return path, load_data


def test_shacl_conforms(shacl_data):
    from validate import run as validate_run
    path, load = shacl_data
    conforms, problems = validate_run(load(path))
    assert conforms, problems[:10]


def test_shacl_catches_broken_data(shacl_data):
    from rdflib import Namespace, URIRef
    from rdflib.namespace import RDF

    from validate import run as validate_run
    path, load = shacl_data
    data = load(path)
    xf = Namespace("https://xcp-factory.example/def#")
    alarm = URIRef(ID + "alarm/1")
    data.remove((alarm, xf.onEquipment, None))
    data.add((URIRef(ID + "assignment/1"), xf.scope, URIRef(ID + "part/B-BRG01")))
    data.add((URIRef(ID + "ghost"), RDF.type, xf.WorkOrder))
    conforms, problems = validate_run(data)
    assert not conforms
    messages = {m for _, m in problems}
    assert "対象設備が 1 つ必要" in messages and "ラインが必要" in messages and "担当範囲が必要" in messages
