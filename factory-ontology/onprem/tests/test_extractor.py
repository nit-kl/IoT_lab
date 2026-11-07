"""差分抽出のテスト(Oracle と AWS は使わない)。"""

import gzip
import json
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import sqlglot
from sqlglot import exp

ONPREM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ONPREM / "extractor"))

from extract import OVERLAP, batch_key, extract_once, load_policy, load_state, plain, save_state  # noqa: E402

BASE = datetime(2026, 11, 7, 12, 0)
POLICY = {"T": ["ID", "VAL", "UPD_TS"]}


class FakeDb:
    def __init__(self):
        self.rows = {}

    def upsert(self, id_, val, updated):
        self.rows[id_] = {"ID": id_, "VAL": val, "UPD_TS": updated}

    def fetch(self, table, columns, since):
        rows = [r for r in self.rows.values() if since is None or r["UPD_TS"] >= since]
        return [{c: r[c] for c in columns} for r in rows]


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.fail = False

    def put(self, key, body):
        if self.fail:
            raise ConnectionError("network down")
        self.objects[key] = body

    def rows(self, index=-1):
        lines = gzip.decompress(list(self.objects.values())[index]).decode().splitlines()
        return json.loads(lines[0]), [json.loads(line) for line in lines[1:]]


@pytest.fixture
def env():
    return FakeDb(), FakeS3()


def run(db, s3, state, now=BASE):
    return extract_once(db.fetch, s3.put, POLICY, state, now)


def ids(s3, index=-1):
    return [r["r"]["ID"] for r in s3.rows(index)[1]]


def test_first_run_sends_everything(env):
    db, s3 = env
    db.upsert(1, "a", BASE)
    db.upsert(2, "b", BASE + timedelta(seconds=1))
    sent, _ = run(db, s3, {})
    assert sent == {"T": 2} and sorted(ids(s3)) == [1, 2]


def test_no_change_uploads_nothing(env):
    db, s3 = env
    db.upsert(1, "a", BASE)
    _, state = run(db, s3, {})
    assert run(db, s3, state)[0] == {} and len(s3.objects) == 1


def test_updated_row_is_sent_again_with_new_value(env):
    db, s3 = env
    db.upsert(1, "open", BASE)
    _, state = run(db, s3, {})
    db.upsert(1, "recovered", BASE + timedelta(minutes=5))
    sent, _ = run(db, s3, state)
    assert sent == {"T": 1} and s3.rows()[1][0]["r"]["VAL"] == "recovered"


def test_late_commit_inside_overlap_is_caught(env):
    """読み取り位置より古い UPD_TS の行が、後からコミットされても拾う。"""
    db, s3 = env
    db.upsert(1, "a", BASE)
    _, state = run(db, s3, {})
    db.upsert(2, "late", BASE - OVERLAP / 2)
    sent, state = run(db, s3, state)
    assert sent == {"T": 1} and ids(s3) == [2]
    assert run(db, s3, state)[0] == {}


def test_sent_rows_inside_overlap_are_not_resent(env):
    db, s3 = env
    for i in range(3):
        db.upsert(i, "x", BASE - timedelta(seconds=i))
    _, state = run(db, s3, {})
    db.upsert(9, "new", BASE + timedelta(seconds=1))
    assert run(db, s3, state)[0] == {"T": 1} and ids(s3) == [9]


def test_failed_upload_keeps_position_and_retries(env, tmp_path):
    db, s3 = env
    path = tmp_path / "state.json"
    db.upsert(1, "a", BASE)
    _, state = run(db, s3, {})
    save_state(state, path)
    db.upsert(2, "b", BASE + timedelta(minutes=1))
    s3.fail = True
    with pytest.raises(ConnectionError):
        run(db, s3, load_state(path))
    s3.fail = False
    assert run(db, s3, load_state(path))[0] == {"T": 1} and ids(s3) == [2]


def test_catch_up_after_long_stop(env):
    db, s3 = env
    db.upsert(1, "a", BASE)
    _, state = run(db, s3, {})
    for i in range(2, 102):
        db.upsert(i, "x", BASE + timedelta(hours=i))
    sent, _ = run(db, s3, state)
    assert sent == {"T": 100}


def test_only_policy_columns_leave(env):
    db, s3 = env
    seen = []

    def fetch(table, columns, since):
        seen.append(list(columns))
        return []
    extract_once(fetch, s3.put, POLICY, {}, BASE)
    assert seen == [POLICY["T"]]


def test_batch_key_and_header(env):
    db, s3 = env
    db.upsert(1, "a", BASE)
    run(db, s3, {})
    key = next(iter(s3.objects))
    assert key.startswith("raw/2026/11/07/20261107T120000-") and key.endswith(".jsonl.gz")
    assert s3.rows()[0]["tables"] == {"T": 1}
    assert batch_key(BASE, "x") < batch_key(BASE + timedelta(seconds=1), "a")


def test_values_are_json_safe():
    assert plain(datetime(2026, 1, 2, 3)) == "2026-01-02T03:00:00"
    assert plain(date(2026, 1, 2)) == "2026-01-02"
    assert plain(Decimal("1.5")) == 1.5


def test_policy_matches_schema_and_excludes_personal_names():
    statements = sqlglot.parse((ONPREM / "db" / "schema.sql").read_text(encoding="utf-8"), read="oracle")
    schema = {s.this.this.name: [c.name for c in s.this.expressions if isinstance(c, exp.ColumnDef)]
              for s in statements if isinstance(s, exp.Create) and s.kind == "TABLE"}
    policy = load_policy()
    assert set(policy) == set(schema)
    for table, columns in policy.items():
        assert set(columns) <= set(schema[table]), table
        assert "UPD_TS" in columns, table
    assert "SHAIN_NM" not in policy["M_SHAIN"]
    assert {c for t in policy for c in set(schema[t]) - set(policy[t])} == {"SHAIN_NM"}
