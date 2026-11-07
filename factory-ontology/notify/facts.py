"""通知に使う事実をグラフから取り出す。ここに文章は書かない。"""

from datetime import timedelta

from kg.common import ID
from kg.store import load_query, run


def stamp(moment):
    return moment.isoformat()


def critical_alarms(store, now, hours=6):
    """未復帰の重大アラーム。"""
    cutoff = stamp(now - timedelta(hours=hours))
    return run(store, f"""
SELECT ?alarm ?equipmentId ?equipment ?line ?started ?message ?code WHERE {{
  ?alarm a xf:Alarm ; xf:severity 3 ; prov:startedAtTime ?started ; xf:onEquipment ?e .
  FILTER NOT EXISTS {{ ?alarm prov:endedAtTime ?ended }}
  FILTER (?started >= "{cutoff}"^^xsd:dateTime)
  ?e xf:equipmentId ?equipmentId ; rdfs:label ?equipment .
  ?l a s4inma:WorkCenter ; saref:consistsOf+ ?e ; rdfs:label ?line .
  OPTIONAL {{ ?alarm xf:message ?message }}
  OPTIONAL {{ ?alarm xf:alarmCode / rdfs:label ?code }}
}} ORDER BY ?started""")


def alarm_is_closed(store, alarm_iri):
    return run(store, f"ASK {{ <{alarm_iri}> prov:endedAtTime ?t }}") or not run(store, f"ASK {{ <{alarm_iri}> a xf:Alarm }}")


def on_duty(store, equipment_id, at):
    return run(store, load_query("cq4_on_duty", EQUIPMENT=equipment_id, AT=stamp(at)))


def line_leader(store, equipment_id, at):
    rows = run(store, load_query("cq7_role_on_date", EQUIPMENT=equipment_id, DATE=at.date().isoformat()))
    return [r for r in rows if r["role"] == "ライン責任者"]


def current_parts(store, equipment_id):
    return run(store, load_query("cq1_current_parts", EQUIPMENT=equipment_id))


def similar_cases(store, equipment_id):
    return run(store, load_query("cq5_similar_cases", EQUIPMENT=equipment_id))


def same_lot_equipment(store, lot, excluding):
    """同じロットを使う、または使っていた設備。交換済みでも、同じロットで故障した事実は重要なので含める。"""
    rows = run(store, load_query("cq2_same_lot_equipment", LOT=lot))
    return [r for r in rows if r["equipmentId"] != excluding]


def lot_impact(store, lot):
    return run(store, load_query("cq3_lot_impact", LOT=lot))


def open_orders(store, equipment_id):
    """設備のラインで実行中の指図。納期の近い順。"""
    return run(store, f"""
SELECT ?order ?product ?due ?quantity WHERE {{
  ?e xf:equipmentId "{equipment_id}" .
  ?l a s4inma:WorkCenter ; saref:consistsOf+ ?e .
  ?o xf:onLine ?l ; xf:orderState <{ID}code/JOTAI/RUN> ; xf:orderNumber ?order ; xf:dueDate ?due ;
     xf:plannedQuantity ?quantity ; xf:producesProduct / rdfs:label ?product .
}} ORDER BY ?due""")


def defective_lots(store, now, hours=48, min_rate=0.05, min_orders=2):
    """不良率の高い検査が、同じロットの複数の指図で起きているものを返す。

    期間内の検査を合算すると、正常な検査に薄められて急増を見逃すので、不良率の高い検査だけを数える。
    """
    cutoff = stamp(now - timedelta(hours=hours))
    rows = run(store, f"""
SELECT ?lot ?part ?supplier ?order ?line ?inspected ?defects ?t WHERE {{
  ?i a xf:Inspection ; xf:forOrder ?o ; prov:startedAtTime ?t ; xf:inspectedCount ?inspected ; xf:defectCount ?defects .
  FILTER (?t >= "{cutoff}"^^xsd:dateTime && ?inspected > 0 && ?defects / ?inspected >= {min_rate})
  ?use a xf:MaterialUse ; xf:forOrder ?o ; xf:usesBatch ?b .
  ?b xf:lotNumber ?lot ; xf:ofPart / rdfs:label ?part ; xf:suppliedBy / rdfs:label ?supplier .
  ?o xf:orderNumber ?order ; xf:onLine ?line .
}}""")
    lots = {}
    for row in rows:
        lot = lots.setdefault(row["lot"], {"lot": row["lot"], "part": row["part"], "supplier": row["supplier"],
                                           "order_set": set(), "line_set": set(), "total": 0, "bad": 0, "first": row["t"]})
        lot["first"] = min(lot["first"], row["t"])
        lot["order_set"].add(row["order"])
        lot["line_set"].add(row["line"])
        lot["total"] += int(row["inspected"])
        lot["bad"] += int(row["defects"])
    return [{"lot": x["lot"], "part": x["part"], "supplier": x["supplier"], "orders": len(x["order_set"]), "lines": len(x["line_set"]),
             "total": x["total"], "bad": x["bad"], "rate": x["bad"] / x["total"], "first": x["first"]}
            for x in lots.values() if len(x["order_set"]) >= min_orders]


def line_results(store, start, end):
    """ラインごとの良品数、不良数、目標の歩留り。"""
    return run(store, f"""
SELECT ?lineId ?line ?target (SUM(?good) AS ?ok) (SUM(?bad) AS ?ng) WHERE {{
  ?l a s4inma:WorkCenter ; rdfs:label ?line .
  BIND (STRAFTER(STR(?l), "/line/") AS ?lineId)
  ?t xf:onLine ?l ; xf:kpi <{ID}code/KPI/YIELD> ; xf:targetValue ?target .
  ?o xf:onLine ?l .
  ?r xf:forOrder ?o ; prov:startedAtTime ?s ; xf:goodCount ?good ; xf:defectCount ?bad .
  FILTER (?s >= "{stamp(start)}"^^xsd:dateTime && ?s < "{stamp(end)}"^^xsd:dateTime)
}} GROUP BY ?lineId ?line ?target ORDER BY ?lineId""")


def alarm_counts(store, start, end):
    rows = run(store, f"""
SELECT ?severity (COUNT(*) AS ?n) WHERE {{
  ?a a xf:Alarm ; xf:severity ?severity ; prov:startedAtTime ?s .
  FILTER (?s >= "{stamp(start)}"^^xsd:dateTime && ?s < "{stamp(end)}"^^xsd:dateTime)
}} GROUP BY ?severity""")
    return {int(r["severity"]): int(r["n"]) for r in rows}


def stoppages(store, start, end):
    """停止の理由ごとの件数と合計分。"""
    rows = run(store, f"""
SELECT ?reason ?s ?e WHERE {{
  ?x a xf:Stoppage ; xf:stopReason / rdfs:label ?reason ; prov:startedAtTime ?s ; prov:endedAtTime ?e .
  FILTER (?s >= "{stamp(start)}"^^xsd:dateTime && ?s < "{stamp(end)}"^^xsd:dateTime)
}}""")
    from datetime import datetime
    totals = {}
    for row in rows:
        minutes = (datetime.fromisoformat(row["e"]) - datetime.fromisoformat(row["s"])).total_seconds() / 60
        count, total = totals.get(row["reason"], (0, 0.0))
        totals[row["reason"]] = (count + 1, total + minutes)
    return totals


def latest_activity(store):
    rows = run(store, "SELECT (MAX(?t) AS ?latest) WHERE { ?r a xf:ProductionRun ; prov:endedAtTime ?t }")
    return rows[0]["latest"] if rows else None
