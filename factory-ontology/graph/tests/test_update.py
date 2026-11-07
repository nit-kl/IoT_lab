"""差分によるグラフ更新のテスト。全件を一度に変換した結果と一致することを基準にする。"""

import copy
import gzip
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

GRAPH_DIR = Path(__file__).resolve().parents[1]
FACTORY = GRAPH_DIR.parent
sys.path[:0] = [str(GRAPH_DIR), str(FACTORY / "onprem" / "simulator"), str(FACTORY / "onprem" / "extractor"), str(FACTORY / "cloud" / "lambda")]

import os  # noqa: E402

os.environ.setdefault("AWS_DEFAULT_REGION", "ap-northeast-1")

from extract import plain  # noqa: E402
from generate import generate  # noqa: E402
from kg.common import ID, PREFIXES  # noqa: E402
from kg.store import run  # noqa: E402
from kg.transform import JST, convert_tables  # noqa: E402
from kg.update import apply_tables, context_from_store, dump_store, load_store, parse_batch, prune_runs  # noqa: E402
from pyoxigraph import NamedNode  # noqa: E402

END = datetime(2026, 11, 7, 12, 0)
XF = PREFIXES["xf"]


def sent(tables):
    """抽出プログラムが送る形(ISO 文字列の行)にする。"""
    return {t: [{k: plain(v) for k, v in r.items() if k != "SHAIN_NM"} for r in rows] for t, rows in tables.items()}


@pytest.fixture(scope="module")
def tables():
    return generate(END)[0]


@pytest.fixture
def store(tables):
    s = load_store()
    apply_tables(s, sent(tables))
    return s


def quads(store):
    return set(map(str, store))


def expected(tables):
    return set(map(str, convert_tables(tables).quads))


def test_incremental_result_equals_full_conversion(store, tables):
    assert quads(store) == expected(tables)


def test_applying_same_batch_twice_changes_nothing(store, tables):
    before = quads(store)
    apply_tables(store, sent(tables))
    assert quads(store) == before


def test_split_batches_equal_full_conversion(tables):
    """マスタと実績を別の回に送っても同じ結果になる(グラフから別名を復元する)。"""
    store = load_store()
    masters = {t: r for t, r in tables.items() if t.startswith("M_")}
    apply_tables(store, sent(masters))
    rows = sent({t: r for t, r in tables.items() if t.startswith("T_")})
    half = {t: r[:len(r) // 2] for t, r in rows.items()}
    rest = {t: r[len(r) // 2:] for t, r in rows.items()}
    apply_tables(store, rest)  # 後の分が先に届いても結果は同じ
    apply_tables(store, half)
    assert quads(store) == expected(tables)


def changed(tables, table, match, **values):
    row = copy.deepcopy(next(r for r in tables[table] if all(r[k] == v for k, v in match.items())))
    row.update(values)
    return row


def test_updated_row_replaces_old_values(store, tables):
    row = changed(tables, "T_EQP_ALM", {"ALM_ID": 1}, FUKKI_TS=None, MSG="書き換え")
    apply_tables(store, sent({"T_EQP_ALM": [row]}))
    assert run(store, f'ASK {{ <{ID}alarm/1> xf:message "書き換え" }}')
    assert len(run(store, f"SELECT ?m WHERE {{ <{ID}alarm/1> xf:message ?m }}")) == 1
    assert not run(store, f"ASK {{ <{ID}alarm/1> prov:endedAtTime ?t }}")


def test_logical_delete_removes_the_row(store, tables):
    row = changed(tables, "T_EQP_ALM", {"ALM_ID": 1}, DEL_FLG="1")
    apply_tables(store, sent({"T_EQP_ALM": [row]}))
    assert not run(store, f"ASK {{ <{ID}alarm/1> ?p ?o }}")


def test_new_alias_in_same_batch_is_used(store, tables):
    alias = {"SYS_KBN": "PLC", "GAIBU_CD": "PRESS3B", "KIKAI_CD": "PRS-07", "UPD_TS": END.isoformat(), "DEL_FLG": "0"}
    alarm = changed(tables, "T_EQP_ALM", {"ALM_ID": 1}, ALM_ID=5000, SRC_EQP_ID="PRESS3B")
    apply_tables(store, sent({"T_EQP_ALM": [alarm], "M_KIKAI_ALIAS": [alias]}))
    assert run(store, f"ASK {{ <{ID}alarm/5000> xf:onEquipment <{ID}equipment/PRS-07> }}")


def test_quarantined_row_recovers_when_master_arrives(store, tables):
    row = next(r for r in tables["T_EQP_ALM"] if r["SRC_EQP_ID"] == "PRESS9")
    alarm = f"<{ID}alarm/{row['ALM_ID']}>"
    assert not run(store, f"ASK {{ {alarm} ?p ?o }}")
    alias = {"SYS_KBN": "PLC", "GAIBU_CD": "PRESS9", "KIKAI_CD": "PRS-07", "UPD_TS": END.isoformat(), "DEL_FLG": "0"}
    apply_tables(store, sent({"M_KIKAI_ALIAS": [alias], "T_EQP_ALM": [row]}))
    assert run(store, f"ASK {{ {alarm} xf:onEquipment <{ID}equipment/PRS-07> }}")
    assert len(run(store, 'SELECT ?i WHERE { ?i xf:sourceValue "PRESS9" }')) == 1  # もう 1 行は隔離のまま


def test_bad_row_does_not_stop_the_batch(store, tables):
    good = changed(tables, "T_EQP_ALM", {"ALM_ID": 1}, MSG="正常な行")
    bad = {"ALM_ID": 2}
    stats = apply_tables(store, sent({"T_EQP_ALM": [bad, good]}))
    assert stats["failed"] == 1 and stats["converted"] == 1
    assert run(store, f'ASK {{ <{ID}alarm/1> xf:message "正常な行" }}')


def test_sensor_rows_collapse_to_one_definition(store, tables):
    rows = [r for r in tables["T_SENSOR_DATA"] if r["SRC_EQP_ID"] == "PRESS3"][:50]
    before = quads(store)
    stats = apply_tables(store, sent({"T_SENSOR_DATA": rows}))
    assert quads(store) == before and stats["converted"] <= 2


def test_context_is_rebuilt_from_store(store):
    context = context_from_store(store)
    assert context.aliases["PLC", "PRESS3"] == "PRS-03"
    assert context.aliases["CMMS", "E-0103"] == "PRS-03"
    assert ("ALM", "A101") in context.codes and ("ALM", "A999") not in context.codes


def test_old_runs_are_pruned(store):
    runs = lambda: len(list(store.quads_for_pattern(None, NamedNode(PREFIXES["rdf"] + "type"), NamedNode(XF + "ProductionRun"), None)))
    total = runs()
    assert prune_runs(store, datetime(2026, 11, 7, 12, tzinfo=JST), days=90) == 0
    removed = prune_runs(store, datetime(2026, 11, 7, 12, tzinfo=JST), days=7)
    assert 0 < removed < total and runs() == total - removed
    assert run(store, "ASK { ?a a xf:Alarm }")


def test_store_round_trip(store):
    assert quads(load_store(dump_store(store))) == quads(store)


class FakeS3:
    class exceptions:
        pass

    def __init__(self):
        self.objects = {}
        self.etag = 0

    def batch(self, key, tables, full=False):
        header = {"batch": key, "tables": {t: len(r) for t, r in tables.items()}, "full": full}
        lines = [json.dumps(header)] + [json.dumps({"t": t, "r": r}) for t, rows in tables.items() for r in rows]
        self.objects[key] = ({}, gzip.compress("\n".join(lines).encode()))

    def get_paginator(self, _):
        outer = self

        class Paginator:
            def paginate(self, Bucket, Prefix, StartAfter=""):
                yield {"Contents": [{"Key": k} for k in sorted(outer.objects) if k.startswith(Prefix) and k > StartAfter]}
        return Paginator()

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        meta, body = self.objects[Key]

        class Body:
            def read(self):
                return body
        return {"Body": Body(), "Metadata": meta, "ETag": meta.get("etag")}

    def put_object(self, Bucket, Key, Body, ContentType, Metadata, IfMatch=None, IfNoneMatch=None):
        current = self.objects.get(Key)
        if (IfNoneMatch and current) or (IfMatch and current[0].get("etag") != IfMatch):
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.etag += 1
        self.objects[Key] = ({**Metadata, "etag": str(self.etag)}, Body)


@pytest.fixture
def updater():
    import updater as module
    return module


def graph_of(s3, updater):
    return load_store(s3.objects[updater.CURRENT_KEY][1])


def test_updater_processes_pending_batches_in_key_order(updater, tables):
    s3 = FakeS3()
    data = sent(tables)
    s3.batch("raw/2026/11/07/2-b.jsonl.gz", {"T_EQP_ALM": data["T_EQP_ALM"]})
    s3.batch("raw/2026/11/07/1-a.jsonl.gz", {t: r for t, r in data.items() if t != "T_EQP_ALM"}, full=True)
    result = updater.process(s3, "b")
    assert result["batches"] == 2 and result["last_key"].endswith("2-b.jsonl.gz")
    assert quads(graph_of(s3, updater)) == expected(tables)
    assert updater.process(s3, "b") == {"batches": 0}


def test_updater_full_batch_clears_rows_that_no_longer_exist(updater, tables):
    s3 = FakeS3()
    data = sent(tables)
    s3.batch("raw/1.jsonl.gz", data, full=True)
    updater.process(s3, "b")
    extra = changed(tables, "T_EQP_ALM", {"ALM_ID": 1}, ALM_ID=7777)
    s3.batch("raw/2.jsonl.gz", sent({"T_EQP_ALM": [extra]}))
    updater.process(s3, "b")
    assert run(graph_of(s3, updater), f"ASK {{ <{ID}alarm/7777> a xf:Alarm }}")
    s3.batch("raw/3.jsonl.gz", data, full=True)
    updater.process(s3, "b")
    assert quads(graph_of(s3, updater)) == expected(tables)


def test_updater_position_advances_only_with_the_graph(updater, tables):
    s3 = FakeS3()
    s3.batch("raw/1.jsonl.gz", sent(tables), full=True)
    updater.process(s3, "b")
    s3.batch("raw/2.jsonl.gz", sent({"T_EQP_ALM": [changed(tables, "T_EQP_ALM", {"ALM_ID": 1}, MSG="再試行")]}))
    original = s3.put_object

    def failing(**kwargs):
        raise ClientError({"Error": {"Code": "SlowDown"}}, "PutObject")
    s3.put_object = failing
    with pytest.raises(ClientError):
        updater.process(s3, "b")
    s3.put_object = original
    assert updater.process(s3, "b")["batches"] == 1
    assert run(graph_of(s3, updater), f'ASK {{ <{ID}alarm/1> xf:message "再試行" }}')


def test_updater_skips_unreadable_file_and_continues(updater, tables):
    """壊れたファイルで止まると、以後の更新がすべて止まる。記録して飛ばす。"""
    s3 = FakeS3()
    s3.batch("raw/1.jsonl.gz", sent(tables), full=True)
    s3.objects["raw/2.jsonl.gz"] = ({}, b"not gzip")
    s3.objects["raw/3.jsonl.gz"] = ({}, gzip.compress(b"{broken json"))
    s3.objects["raw/4.jsonl.gz"] = ({}, gzip.compress(b""))
    s3.batch("raw/5.jsonl.gz", sent({"T_EQP_ALM": [changed(tables, "T_EQP_ALM", {"ALM_ID": 1}, MSG="後続の更新")]}))
    result = updater.process(s3, "b")
    assert result["rejected"] == 3 and result["last_key"] == "raw/5.jsonl.gz"
    assert run(graph_of(s3, updater), f'ASK {{ <{ID}alarm/1> xf:message "後続の更新" }}')
    assert sorted(k for k in s3.objects if k.startswith("kg/rejected/")) == [
        "kg/rejected/2.jsonl.gz.json", "kg/rejected/3.jsonl.gz.json", "kg/rejected/4.jsonl.gz.json"]
    assert updater.process(s3, "b") == {"batches": 0}


def test_unreadable_file_does_not_move_the_position(updater, tables):
    """読めないファイルの名前で位置を進めると、それより前に並ぶ正常なファイルを読み飛ばす。"""
    s3 = FakeS3()
    s3.batch("raw/1.jsonl.gz", sent(tables), full=True)
    updater.process(s3, "b")
    s3.objects["raw/9.jsonl.gz"] = ({}, b"not gzip")
    result = updater.process(s3, "b")
    assert result["rejected"] == 1 and result["batches"] == 0
    assert s3.objects[updater.CURRENT_KEY][0]["last-key"] == "raw/1.jsonl.gz"
    s3.batch("raw/2.jsonl.gz", sent({"T_EQP_ALM": [changed(tables, "T_EQP_ALM", {"ALM_ID": 1}, MSG="読み飛ばさない")]}))
    result = updater.process(s3, "b")
    assert result["batches"] == 1 and result["last_key"] == "raw/2.jsonl.gz"
    assert run(graph_of(s3, updater), f'ASK {{ <{ID}alarm/1> xf:message "読み飛ばさない" }}')


def test_updater_detects_a_competing_write(updater, tables):
    s3 = FakeS3()
    s3.batch("raw/1.jsonl.gz", sent(tables), full=True)
    updater.process(s3, "b")
    with pytest.raises(ClientError):
        updater.write_current(s3, "b", b"x", "raw/1.jsonl.gz", "stale-etag")
