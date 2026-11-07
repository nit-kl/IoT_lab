"""検知、宛先、文面、重複防止、上位通知のテスト。グラフは模擬データから作る(Oracle と AWS は使わない)。"""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

FACTORY = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(FACTORY), str(FACTORY / "graph"), str(FACTORY / "onprem" / "simulator"), str(FACTORY / "onprem" / "extractor"), str(FACTORY / "cloud" / "lambda")]
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-northeast-1")

import ack  # noqa: E402
import notifier as nf  # noqa: E402
from extract import plain  # noqa: E402
from generate import generate  # noqa: E402
from kg.update import apply_tables, load_store  # noqa: E402
from notify import digest  # noqa: E402
from notify.mailer import build, sign, subject_line, verify  # noqa: E402
from notify.rules import compact, detect, route  # noqa: E402
from notify.writer import NOTE, Report, ScriptedWriter  # noqa: E402

JST = timezone(timedelta(hours=9))
END = datetime(2026, 11, 7, 12, 0)
NOW = END.replace(tzinfo=JST)


def sent(tables):
    return {t: [{k: plain(v) for k, v in r.items() if k != "SHAIN_NM"} for r in rows] for t, rows in tables.items()}


@pytest.fixture(scope="module")
def tables():
    return generate(END)[0]


@pytest.fixture
def store(tables):
    s = load_store()
    apply_tables(s, sent(tables))
    return s


def alarm(number=9001, plc="PRESS7", at=None, end=None):
    at = at or END
    return {"ALM_ID": number, "SRC_EQP_ID": plc, "ALM_CD": "A103", "ALM_LV": 3, "HASSEI_TS": at, "FUKKI_TS": end,
            "MSG": "主軸異常により非常停止", "UPD_TS": at, "DEL_FLG": "0"}


def inject_alarm(store, **kw):
    apply_tables(store, sent({"T_EQP_ALM": [alarm(**kw)]}))


def inject_bad_lot(store, tables, rate=0.09):
    """2 ラインの実行中の指図の検査で不良を増やす。"""
    orders = {}
    for row in tables["T_SEIZO_SHIJI"]:
        orders[row["LINE_CD"]] = row["SHIJI_NO"]
    rows = [{"KENSA_ID": 8000 + i, "SHIJI_NO": orders[line], "KENSA_TS": END, "KENSA_SU": 400, "NG_SU": int(400 * rate), "FURYO_CD": "F20", "UPD_TS": END, "DEL_FLG": "0"}
            for i, line in enumerate(("L01", "L02"))]
    apply_tables(store, sent({"T_HINSHITSU_KENSA": rows}))


class FakeState:
    def __init__(self):
        self.items, self.counts = {}, {}

    def claim(self, key, now):
        if key in self.items:
            return False
        self.items[key] = {}
        return True

    def release(self, key):
        self.items.pop(key)

    def count_sent(self, day, now):
        self.counts[day] = self.counts.get(day, 0) + 1
        return self.counts[day]

    def sent_today(self, day):
        return self.counts.get(day, 0)

    def open_event(self, event, created, now):
        self.items[f"event#{event.id}"] = {"pk": f"event#{event.id}", "status": "OPEN", "level": 0, "created": created, "source": event.source, "equipment": event.facts["equipment"]}

    def open_events(self):
        return [v for k, v in self.items.items() if k.startswith("event#") and v["status"] == "OPEN"]

    def raise_level(self, key, level):
        item = self.items[key]
        if item["status"] != "OPEN" or item["level"] != level - 1:
            return False
        item["level"] = level
        return True

    def close(self, key, status="CLOSED"):
        self.items[key]["status"] = status


class Outbox:
    def __init__(self):
        self.mails, self.fail = [], False

    def __call__(self, to, subject, text, page):
        if self.fail:
            raise ConnectionError("SES down")
        self.mails.append((to, subject, text, page))

    def subjects(self):
        return [m[1] for m in self.mails]

    def role(self, name):
        return [m for m in self.mails if m[1].startswith(f"[{name}]")]


@pytest.fixture
def outbox():
    return Outbox()


@pytest.fixture
def notifier(outbox):
    return nf.Notifier(FakeState(), outbox, {"default": "demo@example.com"}, "https://ack.example/", b"secret", daily_limit=50, escalate_minutes=(5, 10))


# ---------- 検知 ----------


def test_recovered_past_alarm_is_not_detected(store):
    assert detect(store, NOW) == []


def test_open_critical_alarm_is_detected_with_graph_facts(store):
    inject_alarm(store)
    events = detect(store, NOW)
    assert [e.kind for e in events] == ["critical_alarm"]
    data = events[0].facts
    assert data["equipmentId"] == "PRS-07" and data["line"] == "プレスライン2"
    assert {p["lot"] for p in data["parts"]} == {"LB-2609-17"}
    assert [c["equipmentId"] for c in data["cases"]][0] == "PRS-07"
    assert data["on_duty"] and data["orders"]


def test_same_lot_includes_equipment_where_part_was_replaced(store):
    """3 号機は交換済みでも、同じロットで故障した事実は品質保証に伝える。"""
    inject_alarm(store)
    data = detect(store, NOW)[0].facts
    equipment = data["shared_lots"]["LB-2609-17"]["equipment"]
    assert [e["equipmentId"] for e in equipment] == ["PRS-03"] and equipment[0]["removed"]
    assert "に交換済み" in ScriptedWriter().write("品質保証", "critical_alarm", data).body


def test_shared_lot_still_in_use_is_marked(store, tables):
    row = next(r for r in tables["T_BUHIN_TORITSUKE"] if r["KIKAI_CD"] == "PRS-03" and r["TORIHAZUSHI_TS"] is not None)
    apply_tables(store, sent({"T_BUHIN_TORITSUKE": [{**row, "TORI_ID": 7000, "KIKAI_CD": "CNC-01", "TORIHAZUSHI_TS": None}]}))
    inject_alarm(store)
    data = detect(store, NOW)[0].facts
    assert "使用中" in ScriptedWriter().write("品質保証", "critical_alarm", data).body


def test_quality_is_skipped_when_no_other_equipment_shares_the_lot(store):
    inject_alarm(store)
    event = detect(store, NOW)[0]
    event.facts["shared_lots"] = {}
    notices, skipped = route(event, NOW)
    assert "品質保証" in skipped and "品質保証" not in {n.role for n in notices}


def test_old_open_alarm_is_ignored(store):
    inject_alarm(store, at=END - timedelta(hours=7))
    assert detect(store, NOW) == []


def test_lot_rule_needs_two_orders_and_high_rate(store, tables):
    inject_bad_lot(store, tables, rate=0.01)
    assert detect(store, NOW) == []
    inject_bad_lot(store, tables, rate=0.09)
    events = detect(store, NOW)
    assert [e.kind for e in events] == ["lot_quality"]
    assert int(events[0].facts["lines"]) == 2


def test_same_alarm_keeps_same_id(store):
    inject_alarm(store)
    assert detect(store, NOW)[0].id == detect(store, NOW + timedelta(minutes=9))[0].id


def test_same_lot_problem_is_notified_once_across_hours(store, tables, notifier):
    inject_bad_lot(store, tables)
    first = detect(store, NOW)[0].id
    later = NOW + timedelta(hours=5)
    assert detect(store, later)[0].id == first
    sent = notifier.handle_events(store, NOW)
    assert sent == 5 and notifier.handle_events(store, later) == 0


def test_reused_alarm_number_after_reseed_gets_new_id():
    assert compact("2026-11-07T12:00:00+09:00") != compact("2026-11-08T12:00:00+09:00")


# ---------- 宛先 ----------


def test_critical_alarm_routing_and_reasons(store):
    inject_alarm(store)
    notices, skipped = route(detect(store, NOW)[0], NOW)
    assert {n.role for n in notices} == {"保全当番", "ライン責任者", "工場長", "品質保証", "生産管理"}
    assert set(skipped) == {"KPI責任者"}
    assert [n.role for n in notices if n.needs_ack] == ["保全当番"]


def test_production_control_is_skipped_when_no_due_date_is_near(store):
    inject_alarm(store)
    event = detect(store, NOW)[0]
    event.facts["orders"] = []
    notices, skipped = route(event, NOW)
    assert "生産管理" in skipped and "生産管理" not in {n.role for n in notices}


def test_lot_routing_reaches_each_affected_line(store, tables):
    inject_bad_lot(store, tables)
    notices, skipped = route(detect(store, NOW)[0], NOW)
    assert [n.target for n in notices if n.role == "ライン責任者"] == ["プレスライン1", "プレスライン2"]
    assert "保全当番" in skipped and "工場長" in {n.role for n in notices}


# ---------- 文面 ----------


def writer_report(store, role):
    inject_alarm(store)
    return ScriptedWriter().write(role, "critical_alarm", detect(store, NOW)[0].facts)


def test_each_role_gets_appropriate_amount(store):
    lengths = {role: len(writer_report(store, role).body) for role in ("保全当番", "工場長", "生産管理")}
    assert lengths["保全当番"] > 3 * lengths["工場長"]


def test_maintenance_mail_has_facts_from_the_graph(store):
    body = writer_report(store, "保全当番").body
    assert "プレス7号機" in body and "LB-2609-17" in body and "グリス補充" in body and "社員 " in body


def test_every_script_renders_without_missing_slots():
    writer = ScriptedWriter()
    for kind, roles in writer.scripts.items():
        if kind in ("critical_alarm", "lot_quality"):
            continue
        for role in roles:
            report = writer.write(role, kind, {})
            assert report.subject


def test_missing_duty_falls_back_to_a_clear_message(store):
    inject_alarm(store)
    data = detect(store, NOW)[0].facts
    data["on_duty"] = []
    assert "未登録" in ScriptedWriter().write("保全当番", "critical_alarm", data).body


def test_mail_marks_demo_script_and_escapes_html():
    subject, text, page = build("保全当番", Report("緊急", "件名\n改行", "<script>alert(1)</script>"), "https://ack.example/?e=1&s=2")
    assert NOTE in text and NOTE in page
    assert "<script>" not in page and "&lt;script&gt;" in page
    assert subject == "[保全当番][緊急] 件名 改行" and "\n" not in subject
    assert "e=1&amp;s=2" in page


def test_subject_prefix_identifies_role():
    assert subject_line("工場長", Report("日次", "11/07 工場サマリー", "")).startswith("[工場長][日次] ")


# ---------- 送信と重複防止 ----------


def test_detection_sends_each_role_once(store, notifier, outbox):
    inject_alarm(store)
    assert notifier.handle_events(store, NOW) == 5
    assert notifier.handle_events(store, NOW + timedelta(minutes=1)) == 0
    assert len(outbox.mails) == 5 and {m[0] for m in outbox.mails} == {"demo@example.com"}


def test_failed_send_can_be_retried_without_duplicates(store, notifier, outbox):
    inject_alarm(store)
    outbox.fail = True
    with pytest.raises(ConnectionError):
        notifier.handle_events(store, NOW)
    outbox.fail = False
    assert notifier.handle_events(store, NOW) == 5
    assert len(outbox.mails) == 5


def test_daily_limit_stops_sending(store, outbox):
    limited = nf.Notifier(FakeState(), outbox, {"default": "demo@example.com"}, daily_limit=2)
    inject_alarm(store)
    assert limited.handle_events(store, NOW) == 2
    assert len(outbox.mails) == 2


def test_only_maintenance_mail_has_ack_link(store, notifier, outbox):
    inject_alarm(store)
    notifier.handle_events(store, NOW)
    assert "https://ack.example/?e=" in outbox.role("保全当番")[0][2]
    assert all("ack.example" not in m[2] for m in outbox.mails if not m[1].startswith("[保全当番]"))


def test_role_specific_address_is_used(store, outbox):
    custom = nf.Notifier(FakeState(), outbox, {"default": "a@example.com", "工場長": "boss@example.com"})
    inject_alarm(store)
    custom.handle_events(store, NOW)
    assert outbox.role("工場長")[0][0] == "boss@example.com" and outbox.role("保全当番")[0][0] == "a@example.com"


# ---------- 上位通知 ----------


def opened(store, notifier):
    inject_alarm(store)
    notifier.handle_events(store, NOW)
    return next(k for k in notifier.state.items if k.startswith("event#"))


def test_escalation_climbs_after_each_threshold(store, notifier, outbox):
    opened(store, notifier)
    outbox.mails.clear()
    assert notifier.escalate(NOW + timedelta(minutes=4)) == 0
    assert notifier.escalate(NOW + timedelta(minutes=6)) == 1
    assert outbox.subjects()[0].startswith("[ライン責任者][未対応]")
    assert notifier.escalate(NOW + timedelta(minutes=7)) == 0
    assert notifier.escalate(NOW + timedelta(minutes=11)) == 1
    assert outbox.subjects()[1].startswith("[工場長][未対応]")
    assert notifier.escalate(NOW + timedelta(minutes=30)) == 0


def test_ack_stops_escalation(store, notifier, outbox):
    key = opened(store, notifier)
    outbox.mails.clear()
    notifier.state.close(key, "ACK")
    assert notifier.escalate(NOW + timedelta(minutes=20)) == 0 and not outbox.mails


def test_recovered_alarm_stops_escalation(store, notifier, outbox):
    opened(store, notifier)
    outbox.mails.clear()
    inject_alarm(store, end=END + timedelta(minutes=3))
    notifier.close_recovered(store)
    assert notifier.escalate(NOW + timedelta(minutes=20)) == 0


# ---------- 定時と管理者 ----------


def test_daily_report_reaches_three_roles(store, notifier, outbox):
    assert notifier.digest(store, "daily", NOW) == 3
    assert {s.split("]")[0][1:] for s in outbox.subjects()} == {"工場長", "KPI責任者", "品質保証"}
    assert "通知の実績" in outbox.role("KPI責任者")[0][2]
    assert "unknown_code" in outbox.role("品質保証")[0][2]
    assert notifier.digest(store, "daily", NOW) == 0


def test_reduction_rate_is_computed():
    data = {"broadcast": 60, "sent": 6}
    assert 1 - data["sent"] / data["broadcast"] == pytest.approx(0.9)


def test_daily_numbers_match_the_source(store, tables):
    data = digest.gather(store, "daily", NOW)
    start = END - timedelta(days=1)
    rows = [r for r in tables["T_SEISAN_JISSEKI"] if start <= r["KAISHI_TS"] < END]
    ok, ng = sum(r["OK_SU"] for r in rows), sum(r["NG_SU"] for r in rows)
    assert data["yield_all"] == f"{ok / (ok + ng) * 100:.1f}%"
    assert data["critical"] == sum(1 for r in tables["T_EQP_ALM"] if r["ALM_LV"] == 3 and start <= r["HASSEI_TS"] < END)


def test_weekly_and_shift_reports(store, notifier, outbox):
    assert notifier.digest(store, "weekly", NOW) == 2
    assert notifier.shift(store, NOW) == 4
    assert any("引き継ぎ" in s and "プレスライン1" in s for s in outbox.subjects())


def test_scheduled_report_is_skipped_when_data_stopped(store, notifier, outbox):
    late = NOW + timedelta(days=3)
    assert "skipped" in nf.run_event(notifier, {"kind": "daily"}, late, lambda: store)
    assert "skipped" not in nf.run_event(notifier, {"kind": "daily", "force": True}, late, lambda: store)


def test_rejected_file_and_alarm_reach_admin(notifier, outbox):
    event = {"source": "aws.s3", "detail": {"object": {"key": "kg/rejected/x.json"}}}
    nf.run_event(notifier, event, NOW, lambda: pytest.fail("グラフは読まない"))
    alarm_event = {"source": "aws.cloudwatch", "detail": {"alarmName": "dlq", "state": {"reason": "threshold"}}}
    nf.run_event(notifier, alarm_event, NOW, lambda: pytest.fail("グラフは読まない"))
    assert outbox.subjects()[0].startswith("[管理者][管理] 読めないファイル") and "dlq" in outbox.subjects()[1]


def test_graph_update_event_does_not_page_admin(store, notifier, outbox):
    event = {"source": "aws.s3", "detail": {"object": {"key": "kg/current.nq.gz"}}}
    nf.run_event(notifier, event, NOW, lambda: store)
    assert not outbox.mails


# ---------- 対応済みの記録 ----------


class FakeTable:
    def __init__(self):
        self.updated = []

    def update_item(self, **kw):
        self.updated.append(kw["Key"]["pk"])


def http(method, params=None, body=None):
    return {"requestContext": {"http": {"method": method}}, "queryStringParameters": params, "body": body}


def link(event_id="alarm-1", expires=2000):
    return {"e": event_id, "x": str(expires), "s": sign(b"k", event_id, expires)}


def test_signature_rejects_tampering_and_expiry():
    good = link()
    assert verify(b"k", "alarm-1", "2000", good["s"], 1000)
    assert not verify(b"k", "alarm-2", "2000", good["s"], 1000)
    assert not verify(b"other", "alarm-1", "2000", good["s"], 1000)
    assert not verify(b"k", "alarm-1", "2000", good["s"], 3000)


def test_opening_the_link_does_not_record(notifier):
    table = FakeTable()
    response = ack.respond(http("GET", link()), b"k", table, 1000)
    assert response["statusCode"] == 200 and "<form" in response["body"] and not table.updated


def test_post_records_ack():
    table = FakeTable()
    p = link()
    response = ack.respond(http("POST", body=f"e={p['e']}&x={p['x']}&s={p['s']}"), b"k", table, 1000)
    assert response["statusCode"] == 200 and table.updated == ["event#alarm-1"]


@pytest.mark.parametrize("params", [None, {"e": "alarm-1"}, {"e": "alarm-1", "x": "abc", "s": "x"}, {**link(), "s": "wrong"}, link(expires=500)])
def test_bad_links_are_refused(params):
    table = FakeTable()
    assert ack.respond(http("GET", params), b"k", table, 1000)["statusCode"] == 403 and not table.updated


def test_event_id_is_escaped_on_page():
    bad = "<x>"
    response = ack.respond(http("GET", link(bad)), b"k", FakeTable(), 1000)
    assert "<x>" not in response["body"]
