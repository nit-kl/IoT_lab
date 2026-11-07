"""定時レポートの事実を集める。"""

from datetime import timedelta

from notify import facts
from notify.writer import percent, rows

ROLES_BY_KIND = {"daily": ("工場長", "KPI責任者", "品質保証"), "weekly": ("工場長", "KPI責任者")}
SPANS = {"daily": timedelta(days=1), "weekly": timedelta(days=7), "shift": timedelta(hours=8)}
ALL_ROLES = 6


def yield_rate(ok, ng):
    total = ok + ng
    return ok / total if total else None


def show_rate(value):
    return percent(value) if value is not None else "データなし"


def totals(results):
    ok = sum(float(r["ok"]) for r in results)
    ng = sum(float(r["ng"]) for r in results)
    return yield_rate(ok, ng)


def yield_table(results):
    def line(r):
        rate = yield_rate(float(r["ok"]), float(r["ng"]))
        gap = (rate - float(r["target"])) * 100 if rate is not None else 0
        return f"・{r['line']}: {show_rate(rate)}(目標差 {gap:+.1f} ポイント) 不良 {int(float(r['ng']))} 個"
    return rows(results, line)


def stop_table(stops):
    return rows(sorted(stops.items()), lambda x: f"・{x[0]}: {x[1][0]} 件 {x[1][1]:.0f} 分")


def gather(store, kind, now, sent=0, issues=None):
    span = SPANS[kind]
    start, previous = now - span, now - 2 * span
    results = facts.line_results(store, start, now)
    stops = facts.stoppages(store, start, now)
    alarms = facts.alarm_counts(store, start, now)
    broadcast = sum(alarms.values()) * ALL_ROLES
    critical = alarms.get(3, 0)
    return {
        "date": now.strftime("%m/%d"),
        "critical": critical,
        "stop_minutes": f"{sum(v[1] for v in stops.values()):.0f}",
        "yield_all": show_rate(totals(results)),
        "yield_prev": show_rate(totals(facts.line_results(store, previous, start))),
        "yield_table": yield_table(results),
        "stop_table": stop_table(stops),
        "decision": "判断が必要な事項があります。保全の報告を確認してください。" if critical else "判断が必要な事項はありません。",
        "broadcast": broadcast,
        "sent": sent,
        "reduction": percent(1 - sent / broadcast) if broadcast else "-",
        "issues": issues or "新しい問題はありません",
    }


def shift_reports(store, now):
    """ラインごとの引き継ぎの事実。"""
    results = facts.line_results(store, now - SPANS["shift"], now)
    for r in results:
        ok, ng = float(r["ok"]), float(r["ng"])
        yield r["line"], {"ok": int(ok), "ng": int(ng), "yield_line": show_rate(yield_rate(ok, ng)), "target_yield": percent(float(r["target"]))}
