"""工場ラインのサンプル計測データ（縦持ち CSV）を生成する。

1 ラインあたり 5 設備・15 メトリクスを一定間隔で出力する。乱数シードを固定しているので、
同じ引数なら毎回同じ CSV になる。

    python generate_csv.py                                   # 1 日分 → data/factory_sample.csv
    python generate_csv.py --days 7 --out ../data/week.large.csv
    python generate_csv.py --days 30 --interval 10s --lines 2 --out ../data/month.large.csv
"""

import argparse
import csv
import math
import random
import re
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "data" / "factory_sample.csv"
HEADER = ["ts", "line_id", "equipment_id", "metric", "value", "unit"]

# 稼働状態コード
STOP, RUN, ALARM = 0, 1, 2

# 稼働時間帯（2 交代）と昼休み。土日は終日停止
SHIFT_START, SHIFT_END = time(6, 0), time(22, 0)
LUNCH_START, LUNCH_END = time(12, 0), time(13, 0)


def parse_interval(text):
    m = re.fullmatch(r"(\d+)([sm])", text)
    if not m or int(m.group(1)) == 0:
        raise argparse.ArgumentTypeError("間隔は 10s / 30s / 1m のように指定してください")
    return int(m.group(1)) * (60 if m.group(2) == "m" else 1)


def parse_start(text):
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=JST)


def minutes_of(dt):
    return dt.hour * 60 + dt.minute + dt.second / 60


def plan_day(day_index, line_index, seed):
    """その日・そのラインで起きる異常イベントの時刻（0 時からの分）を決める。

    1 日目はすべてのイベントを必ず起こし、2 日目以降は確率で発生させる。
    """
    rng = random.Random(f"{seed}-{day_index}-{line_index}")
    first = day_index == 0
    events = {}
    if first or rng.random() < 0.6:
        # CNC 主軸負荷が 30 分かけて上がり、5〜10 分のチョコ停（異常停止）に至る
        start = rng.uniform(13 * 60 + 30, 15 * 60 + 30) if not first else 14 * 60
        events["cnc_ramp"] = (start, start + 30)
        events["cnc_alarm"] = (start + 30, start + 30 + rng.uniform(5, 10))
    if first or rng.random() < 0.5:
        # コンプレッサのエア漏れ：吐出圧力が徐々に下がり、消費電力が上がる
        start = rng.uniform(9 * 60, 11 * 60) if not first else 10 * 60
        events["air_leak"] = (start, start + rng.uniform(60, 120))
    if first or rng.random() < 0.4:
        # 夕方の室温上昇（30 ℃ 超え）
        events["hot"] = (14 * 60, 16 * 60 + 30)
    return events


def within(events, name, minute):
    span = events.get(name)
    return span is not None and span[0] <= minute < span[1]


def progress(events, name, minute):
    """イベント期間内での進み具合（0〜1）。"""
    start, end = events[name]
    return (minute - start) / (end - start)


class Line:
    """1 ライン分の設備状態。温度など前回値に依存する量をここで保持する。"""

    def __init__(self, index, seed, interval_sec):
        self.index = index
        self.line_id = f"LINE-{chr(ord('A') + index)}"
        self.no = f"{index + 1:02d}"
        self.seed = seed
        self.interval_sec = interval_sec
        self.rng = random.Random(f"{seed}-noise-{index}")
        self.conveyor_temp = 25.0
        self.press_shots = 0.0
        self.cnc_works = 0.0
        self.day_index = None
        self.events = {}

    def rows(self, ts, day_index):
        if day_index != self.day_index:
            self.day_index = day_index
            self.events = plan_day(day_index, self.index, self.seed)

        g = self.rng.gauss
        minute = minutes_of(ts)
        clock = ts.timetz().replace(tzinfo=None)
        weekday = ts.weekday() < 5
        in_shift = weekday and SHIFT_START <= clock < SHIFT_END
        planned_run = in_shift and not (LUNCH_START <= clock < LUNCH_END)
        per_min = self.interval_sec / 60

        # 工場内環境：15 時頃が最も暑い日変化 + イベント
        temp = 23 + 3 * math.sin((minute - 9 * 60) / (24 * 60) * 2 * math.pi) + g(0, 0.2)
        if within(self.events, "hot", minute):
            temp += 6 * math.sin(progress(self.events, "hot", minute) * math.pi)
        humidity = 55 - (temp - 23) * 1.8 + g(0, 0.8)
        co2 = (750 if in_shift else 450) + g(0, 15)

        # プレス機
        press_status = RUN if planned_run else STOP
        if press_status == RUN:
            self.press_shots += 20 * per_min
            shots, self.press_shots = int(self.press_shots), self.press_shots % 1
            hydraulic, current = 15.0 + g(0, 0.15), 45 + g(0, 2.5)
        else:
            shots, hydraulic, current = 0, 1.5 + g(0, 0.05), 3 + g(0, 0.3)

        # CNC 加工機：負荷上昇 → チョコ停
        cnc_status = RUN if planned_run else STOP
        load = 55 + g(0, 3)
        if planned_run and within(self.events, "cnc_ramp", minute):
            load += 40 * progress(self.events, "cnc_ramp", minute)
        if planned_run and within(self.events, "cnc_alarm", minute):
            cnc_status = ALARM
        if cnc_status == RUN:
            self.cnc_works += 0.5 * per_min
            works, self.cnc_works = int(self.cnc_works), self.cnc_works % 1
            speed = 8000 + g(0, 40)
        else:
            works, speed, load = 0, 0, 0

        # 搬送コンベア：モータ温度は一次遅れで追従
        conv_speed = 12.0 + g(0, 0.08) if planned_run else 0
        target = 45 if planned_run else temp
        self.conveyor_temp += (target - self.conveyor_temp) * min(1, per_min / 15)
        conv_temp = self.conveyor_temp + g(0, 0.2)

        # エアコンプレッサ：エア漏れで圧力低下・電力増加
        if in_shift:
            pressure, power = 0.70 + g(0, 0.005), 37 + g(0, 1.2)
        else:
            pressure, power = 0.68 + g(0, 0.003), 6 + g(0, 0.4)
        if within(self.events, "air_leak", minute):
            p = progress(self.events, "air_leak", minute)
            pressure -= 0.12 * p
            power += 6 * p

        n = self.no
        return [
            (f"PRESS-{n}", "status", press_status, ""),
            (f"PRESS-{n}", "shot_count", shots, "count"),
            (f"PRESS-{n}", "hydraulic_pressure", round(hydraulic, 2), "MPa"),
            (f"PRESS-{n}", "motor_current", round(current, 1), "A"),
            (f"CNC-{n}", "status", cnc_status, ""),
            (f"CNC-{n}", "spindle_speed", round(speed), "rpm"),
            (f"CNC-{n}", "spindle_load", round(load, 1), "%"),
            (f"CNC-{n}", "work_count", works, "count"),
            (f"CONV-{n}", "speed", round(conv_speed, 2), "m/min"),
            (f"CONV-{n}", "motor_temp", round(conv_temp, 1), "degC"),
            (f"COMP-{n}", "discharge_pressure", round(pressure, 3), "MPa"),
            (f"COMP-{n}", "power", round(power, 1), "kW"),
            (f"ENV-{n}", "temperature", round(temp, 1), "degC"),
            (f"ENV-{n}", "humidity", round(humidity, 1), "%"),
            (f"ENV-{n}", "co2", round(co2), "ppm"),
        ]


def main():
    ap = argparse.ArgumentParser(description="工場ラインのサンプル CSV を生成する")
    ap.add_argument("--days", type=int, default=1, help="生成する日数（既定 1）")
    ap.add_argument("--interval", type=parse_interval, default=60, help="計測間隔 例: 10s, 1m（既定 1m）")
    ap.add_argument("--lines", type=int, default=1, help="ライン数。設備 ID は PRESS-01, PRESS-02 ... と増える（既定 1）")
    ap.add_argument("--start", type=parse_start, default=datetime(2026, 10, 1, tzinfo=JST),
                    help="開始時刻 ISO 8601。タイムゾーン省略時は JST（既定 2026-10-01T00:00）")
    ap.add_argument("--seed", type=int, default=42, help="乱数シード（既定 42）")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"出力先（既定 {DEFAULT_OUT.name}）")
    args = ap.parse_args()
    if not 1 <= args.lines <= 26:
        ap.error("--lines は 1〜26 で指定してください")
    if args.days < 1:
        ap.error("--days は 1 以上で指定してください")

    lines = [Line(i, args.seed, args.interval) for i in range(args.lines)]
    steps = args.days * 86400 // args.interval
    step = timedelta(seconds=args.interval)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with args.out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(HEADER)
        for i in range(steps):
            ts = args.start + step * i
            day_index = (ts.date() - args.start.date()).days
            stamp = ts.isoformat()
            for line in lines:
                for equipment_id, metric, value, unit in line.rows(ts, day_index):
                    w.writerow((stamp, line.line_id, equipment_id, metric, value, unit))
                    count += 1

    size_mb = args.out.stat().st_size / 1_000_000
    print(f"{args.out}: {count:,} 行 / {size_mb:.1f} MB", file=sys.stderr)


if __name__ == "__main__":
    main()
