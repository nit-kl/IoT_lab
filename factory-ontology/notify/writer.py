"""レポートの文章を作る。今は台本どおりのモックで、次の段階でここだけを本物の LLM に置き換える。

入力は「役割、種類、事実」、出力は「タグ、件名、本文」。宛先や数値は決めない。
"""

import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent / "scripts.toml"
NOTE = "このレポートの分析と提案はデモ用の台本です。事実はナレッジグラフから取得しています。"
MISSING = "(なし)"
MAX_ROWS = 8


@dataclass
class Report:
    tag: str
    subject: str
    body: str


class Blank(dict):
    """台本にある項目の事実が欠けていても、送信を止めない。"""

    def __missing__(self, key):
        return MISSING


def clock(value):
    return datetime.fromisoformat(value).strftime("%m/%d %H:%M") if value else MISSING


def rows(items, line):
    if not items:
        return MISSING
    text = [line(item) for item in items[:MAX_ROWS]]
    if len(items) > MAX_ROWS:
        text.append(f"ほか {len(items) - MAX_ROWS} 件")
    return "\n".join(text)


def percent(value):
    return f"{value * 100:.1f}%"


def alarm_slots(data):
    shared = data["shared_lots"]
    duty = data["on_duty"]
    return {
        **data,
        "started": clock(data["started"]),
        "message": data.get("message") or MISSING,
        "duty": f"社員 {duty[0]['employeeId']}" if duty else "未登録(ライン責任者が対応してください)",
        "parts": rows(data["parts"], lambda p: f"・{p['part']} ロット {p['lot']}({p['supplier']}) 取付 {clock(p['installed'])}"),
        "cases": rows(data["cases"], lambda c: f"・{clock(c['started'])} {c['equipment']}: {c['memo']}"),
        "orders": rows(data["orders"], lambda o: f"・{o['order']} {o['product']} {o['quantity']} 個 納期 {o['due']}"),
        "due_count": len(data["orders"]),
        "lot_note": "同じロットの部品を使う設備があり、品質保証が確認します。" if shared else "",
        "shared": rows([(lot, info, e) for lot, info in shared.items() for e in info["equipment"]],
                       lambda x: f"・{x[2]['equipment']}({x[2]['line']}) {x[1]['part']} ロット {x[0]}({x[1]['supplier']}) "
                                 + (f"{clock(x[2]['removed'])} に交換済み" if x[2]["removed"] else "使用中")),
    }


def lot_slots(data):
    return {
        **data,
        "rate": percent(data["rate"]),
        "impact": rows(data["impact"], lambda r: f"・{r['order']} {r['line']} {r['product']} 納期 {r['due']} 不良 {r['defects'] or '-'} / {r['inspected'] or '-'}"),
        "products": "、".join(sorted({r["product"] for r in data["impact"]})),
    }


SLOTS = {"critical_alarm": alarm_slots, "lot_quality": lot_slots}


class ScriptedWriter:
    """台本どおりに文章を作るモック。"""

    def __init__(self, path=SCRIPTS):
        with open(path, "rb") as f:
            self.scripts = tomllib.load(f)

    def write(self, role, kind, data, target=""):
        script = self.scripts[kind][role]
        slots = Blank(SLOTS.get(kind, dict)(data))
        slots["target"] = target
        return Report(script["tag"], script["subject"].format_map(slots), script["body"].strip().format_map(slots))
