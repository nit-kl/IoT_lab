"""グラフに問い合わせる。

    python tools/ask.py                                     # 問いの一覧
    python tools/ask.py cq1_current_parts EQUIPMENT=PRS-03
    python tools/ask.py --all                               # 全部の問いを、模擬データの正解を使って実行
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kg.common import ONPREM, QUERIES_DIR
from kg.store import load_query, open_store, query_params, query_title, run


def example_params():
    truth = json.loads((ONPREM / ".generated" / "ground_truth.json").read_text(encoding="utf-8"))
    return {
        "EQUIPMENT": truth["bearing"]["equipment"], "LOT": truth["bearing"]["lot"], "ID": "PRESS3",
        "AT": truth["failure_at"] + "+09:00", "DATE": truth["failure_at"][:10],
        ("cq3_lot_impact", "LOT"): truth["coil_lot"]["lot"],
    }


def show(store, name, params):
    started = time.perf_counter()
    rows = run(store, load_query(name, **params))
    elapsed = (time.perf_counter() - started) * 1000
    print(f"\n{query_title(name)}\n  条件 {params} / {len(rows)} 行 / {elapsed:.1f} ms")
    for row in rows[:8]:
        print("  " + " | ".join(str(v) for v in row.values()))
    if len(rows) > 8:
        print(f"  ...ほか {len(rows) - 8} 行")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", nargs="?")
    parser.add_argument("params", nargs="*", help="名前=値")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    names = sorted(p.stem for p in QUERIES_DIR.glob("*.rq"))
    if not args.name and not args.all:
        for name in names:
            print(f"{name:26} {query_title(name)}  引数: {' '.join(query_params(name))}")
        return
    store = open_store()
    if args.all:
        examples = example_params()
        for name in names:
            show(store, name, {k: examples.get((name, k), examples.get(k)) for k in query_params(name)})
    else:
        show(store, args.name, dict(p.split("=", 1) for p in args.params))


if __name__ == "__main__":
    main()
