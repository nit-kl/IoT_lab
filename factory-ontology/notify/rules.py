"""検知と宛先の決定。

誰に何を送るかはここに集約する。AI(台本)は文章を書くだけで、宛先を決めない。
送らない判断も理由つきで残し、通知の削減率の説明に使う。
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from notify import facts

ROLES = ("工場長", "KPI責任者", "ライン責任者", "保全当番", "品質保証", "生産管理")
NEAR_DUE_DAYS = 3


@dataclass
class Event:
    id: str
    kind: str  # critical_alarm / lot_quality
    facts: dict = field(default_factory=dict)
    source: str = ""  # 発生元のノード(復帰の確認に使う)


@dataclass
class Notice:
    role: str
    kind: str
    target: str = ""  # 例: ライン名
    needs_ack: bool = False


def compact(moment):
    return moment.replace("-", "").replace(":", "").replace("+", "_")[:15]


def detect(store, now):
    """今のグラフから、通知すべき出来事を見つける。同じ出来事は同じ id になる。"""
    events = []
    for alarm in facts.critical_alarms(store, now):
        number = alarm["alarm"].rsplit("/", 1)[-1]
        events.append(Event(f"alarm-{number}-{compact(alarm['started'])}", "critical_alarm", alarm_facts(store, alarm, now), alarm["alarm"]))
    for lot in facts.defective_lots(store, now):
        # 最初の不良の時刻で区別する。今の時刻を使うと、同じ不良を時間ごとに通知し続ける
        events.append(Event(f"lot-{lot['lot']}-{compact(lot['first'])}", "lot_quality", lot_facts(store, lot)))
    return events


def alarm_facts(store, alarm, now):
    equipment = alarm["equipmentId"]
    parts = facts.current_parts(store, equipment)
    shared = {}
    for part in parts:
        others = facts.same_lot_equipment(store, part["lot"], equipment)
        if others:
            shared[part["lot"]] = {"part": part["part"], "supplier": part["supplier"], "equipment": others}
    cases = [c for c in facts.similar_cases(store, equipment)]
    return {
        **alarm,
        "on_duty": facts.on_duty(store, equipment, now),
        "leader": facts.line_leader(store, equipment, now),
        "parts": parts,
        "shared_lots": shared,
        "cases": cases,
        "orders": facts.open_orders(store, equipment),
    }


def lot_facts(store, lot):
    impact = facts.lot_impact(store, lot["lot"])
    return {**lot, "impact": impact, "line_names": sorted({r["line"] for r in impact})}


def due_soon(orders, now):
    limit = now.date() + timedelta(days=NEAR_DUE_DAYS)
    return [o for o in orders if datetime.fromisoformat(o["due"]).date() <= limit]


def route(event, now):
    """(送る相手, 送らない相手と理由) を返す。"""
    data = event.facts
    if event.kind == "critical_alarm":
        notices = [Notice("保全当番", event.kind, needs_ack=True), Notice("ライン責任者", event.kind), Notice("工場長", event.kind)]
        skipped = {"KPI責任者": "KPI への影響は翌朝のレポートで伝える"}
        if data["shared_lots"]:
            notices.append(Notice("品質保証", event.kind))
        else:
            skipped["品質保証"] = "同じロットを使う設備がない"
        if due_soon(data["orders"], now):
            notices.append(Notice("生産管理", event.kind))
        else:
            skipped["生産管理"] = "納期が近い指図がない"
        return notices, skipped
    if event.kind == "lot_quality":
        notices = [Notice("品質保証", event.kind), Notice("生産管理", event.kind)]
        notices += [Notice("ライン責任者", event.kind, target=name) for name in data["line_names"]]
        skipped = {"保全当番": "設備の異常ではない", "KPI責任者": "KPI への影響は翌朝のレポートで伝える"}
        if int(data["lines"]) >= 2:
            notices.append(Notice("工場長", event.kind))
        else:
            skipped["工場長"] = "1 ラインで収まっている"
        return notices, skipped
    raise ValueError(f"未対応の出来事: {event.kind}")
