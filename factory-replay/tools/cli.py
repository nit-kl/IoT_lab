"""HTTP API を呼んで DB の中身を見る CLI。署名は自動で付ける（aws login の認証情報を使う）。

    python tools/cli.py --owner kojima status
    python tools/cli.py --owner kojima latest --equipment CNC-01
    python tools/cli.py --owner kojima readings --equipment CNC-01 --metric spindle_load --minutes 30
    python tools/cli.py --owner kojima readings --source-from 2026-10-01T14:00 --source-to 2026-10-01T15:00 --all --csv > out.csv
    python tools/cli.py --owner kojima summary --minutes 60
    python tools/cli.py --owner kojima reset                 # 同じ速度で最初から（S3 は読み直さない）
    python tools/cli.py --owner kojima reset --speed 720     # 速度を変えて最初から
    python tools/cli.py --owner kojima reset --stop          # 空にするだけ（再生しない）

--json を付けると API の応答をそのまま表示する。
"""

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "aws"))
from apiclient import ApiError, Client  # noqa: E402
from naming import add_common_args  # noqa: E402

READING_COLUMNS = ["seq", "ts", "source_ts", "line_id", "equipment_id", "metric", "value", "unit"]


def print_table(rows, columns):
    if not rows:
        print("（データなし）")
        return
    cells = [[("" if r.get(c) is None else str(r.get(c))) for c in columns] for r in rows]
    widths = [max(len(c), *(len(row[i]) for row in cells)) for i, c in enumerate(columns)]
    print("  ".join(c.ljust(w) for c, w in zip(columns, widths)))
    print("  ".join("-" * w for w in widths))
    for row in cells:
        print("  ".join(v.ljust(w) for v, w in zip(row, widths)))


def filter_params(args):
    return {
        "equipment": args.equipment, "metric": getattr(args, "metric", None), "line": args.line,
        "minutes": args.minutes, "from": args.from_, "to": args.to,
        "source_from": args.source_from, "source_to": args.source_to,
    }


def cmd_status(client, args):
    s = client.get("/status")
    if args.json:
        return s
    return cmd_status_print(s)


def cmd_latest(client, args):
    r = client.get("/latest", equipment=args.equipment, metric=args.metric, line=args.line)
    if args.json:
        return r
    print_table(r["items"], ["equipment_id", "metric", "value", "unit", "ts", "source_ts"])


def cmd_readings(client, args):
    params = dict(filter_params(args), limit=args.limit)
    items, page = [], client.get("/readings", **params, next=args.next)
    while True:
        items.extend(page["items"])
        if not (args.all and page["next"]):
            break
        print(f"{len(items):,} 行取得…", file=sys.stderr)
        page = client.get("/readings", **params, next=page["next"])
    if args.json:
        return {"items": items, "count": len(items), "next": page["next"]}
    if args.csv:
        w = csv.DictWriter(sys.stdout, READING_COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(items)
        return None
    print_table(items, READING_COLUMNS)
    if page["next"]:
        print(f"\n続きがあります（--all で全件、または --next {page['next']}）", file=sys.stderr)


def cmd_summary(client, args):
    r = client.get("/summary", **filter_params(args))
    if args.json:
        return r
    print(f"集計範囲: {r['window']}")
    for e in r["equipment"]:
        print(f"\n■ {e['equipment_id']}（CSV 上 {e['source_from']} 〜 {e['source_to']}）")
        if "status" in e:
            st = e["status"]
            print(f"  稼働率 {st['run_ratio'] * 100:.1f}%  停止 {st['stop_minutes']} 分  異常停止 {st['alarm_minutes']} 分")
        print_table(e["metrics"], ["metric", "unit", "count", "avg", "min", "max"])


def cmd_reset(client, args):
    body = {"stop": args.stop}
    if args.speed is not None:
        body["speed"] = args.speed
    s = client.request("POST", "/reset", body=body)
    if args.json:
        return s
    if s["status"] == "idle":
        print("readings を空にしました（再生はしていません。再生するには reset を --stop なしで実行）")
    else:
        print(f"最初から再生し直します（速度 {s['speed']:g} 倍）")
    return cmd_status_print(s)


def cmd_status_print(s):
    labels = [("status", "状態"), ("source_key", "CSV"), ("speed", "速度"), ("reflected_rows", "反映済み"),
              ("total_rows", "全行数"), ("percent", "進捗 %"), ("csv_position", "CSV 上の現在位置"),
              ("csv_end", "CSV の終端"), ("started_at", "開始"), ("estimated_finish", "完了見込み"),
              ("updated_at", "最終更新"), ("message", "メッセージ")]
    for key, label in labels:
        if s.get(key) is not None:
            print(f"{label:<12} {s[key]}")


def add_filters(p, metric=True):
    p.add_argument("--equipment", help="設備 ID（例: CNC-01）")
    if metric:
        p.add_argument("--metric", help="メトリクス（例: spindle_load）")
    p.add_argument("--line", help="ライン ID（例: LINE-A）")
    p.add_argument("--minutes", type=int, help="反映時刻で直近 N 分")
    p.add_argument("--from", dest="from_", help="反映時刻の開始（ISO 8601。タイムゾーン省略時は日本時間）")
    p.add_argument("--to", help="反映時刻の終了")
    p.add_argument("--source-from", help="CSV 上の時刻の開始（例: 2026-10-01T14:00）")
    p.add_argument("--source-to", help="CSV 上の時刻の終了")


def main():
    ap = argparse.ArgumentParser(description="xcp-factory の HTTP API を呼ぶ")
    add_common_args(ap)
    ap.add_argument("--json", action="store_true", help="API の応答をそのまま JSON で表示する")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="リプレイの進み具合")
    p = sub.add_parser("latest", help="設備・メトリクスごとの最新値")
    p.add_argument("--equipment")
    p.add_argument("--metric")
    p.add_argument("--line")
    p = sub.add_parser("readings", help="時系列データ")
    add_filters(p)
    p.add_argument("--limit", type=int, default=1000, help="1 回に取る行数（1〜5000、既定 1000）")
    p.add_argument("--next", help="前回の応答の next（続きのページから取る）")
    p.add_argument("--all", action="store_true", help="続きのページもすべて取る")
    p.add_argument("--csv", action="store_true", help="CSV で出力する")
    p = sub.add_parser("summary", help="設備ごとの集計（既定は反映時刻で直近 60 分）")
    add_filters(p, metric=False)
    p.set_defaults(metric=None)
    p = sub.add_parser("reset", help="readings を空にして最初から再生し直す（S3 は読み直さない）")
    p.add_argument("--speed", type=float, help="再生速度（0.1〜100000。省略時は今の速度のまま）")
    p.add_argument("--stop", action="store_true", help="空にするだけで再生しない")

    args = ap.parse_args()
    client = Client(args.owner, args.region)
    command = {"status": cmd_status, "latest": cmd_latest, "readings": cmd_readings, "summary": cmd_summary,
               "reset": cmd_reset}
    try:
        result = command[args.command](client, args)
    except ApiError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
    if result is not None:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
