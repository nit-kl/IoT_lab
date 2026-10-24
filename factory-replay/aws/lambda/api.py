"""HTTP API（Lambda Function URL、IAM 認証）。DB の中身を JSON で返す。

    GET  /status    リプレイの進み具合
    GET  /latest    設備・メトリクスごとの最新値
    GET  /readings  時系列データ（seq 順。next で続きを取る）
    GET  /summary   設備・メトリクスごとの集計と稼働状態
    POST /reset     readings を空にして最初から再生し直す（S3 は読み直さない）

時刻はすべて日本時間（+09:00）の ISO 8601 で返す。
"""

import base64
import json
import os
import re
from datetime import datetime, timedelta, timezone

from botocore.exceptions import ClientError

import schedule
from dataapi import DataApi

db = DataApi(os.environ["CLUSTER_ARN"], os.environ["SECRET_ARN"], os.environ["DATABASE"])

JST = timezone(timedelta(hours=9))
ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
DEFAULT_LIMIT, MAX_LIMIT = 1000, 5000  # Data API の応答サイズ上限（1 MB）に収まる行数
LATEST_WINDOW_ROWS = 5000  # /latest は直近この行数の中から最新値を探す（全設備が毎回計測する前提）
RESUME_WAIT_SEC = 20  # DB が一時停止中なら、この秒数まで再開を待ってから 503 を返す
STATUS_LABELS = {0: "stop", 1: "run", 2: "alarm"}


class BadRequest(Exception):
    pass


class Busy(Exception):
    pass


class Conflict(Exception):
    pass


def query(sql, params=None):
    return db.execute(sql, params, resume_wait_sec=RESUME_WAIT_SEC)


# ---------- 値の変換 ----------

def to_jst(value):
    """Data API が返す UTC の時刻文字列（タイムゾーンなし）を日本時間の ISO 8601 にする。"""
    if value is None:
        return None
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).astimezone(JST).isoformat(timespec="seconds")


def parse_utc(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc) if value else None


def param_id(q, name):
    value = q.get(name)
    if value is not None and not ID_PATTERN.fullmatch(value):
        raise BadRequest(f"{name} は英数字・ハイフン・アンダースコア 32 文字以内で指定してください")
    return value


def param_int(q, name, default, low, high):
    value = q.get(name)
    if value is None:
        return default
    try:
        number = int(value)
    except ValueError:
        raise BadRequest(f"{name} は整数で指定してください") from None
    if not low <= number <= high:
        raise BadRequest(f"{name} は {low}〜{high} で指定してください")
    return number


def param_time(q, name):
    value = q.get(name)
    if value is None:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise BadRequest(f"{name} は ISO 8601 で指定してください（例: 2026-10-01T08:00:00+09:00）") from None
    return dt if dt.tzinfo else dt.replace(tzinfo=JST)  # タイムゾーン省略時は日本時間


def replay_status():
    return query("SELECT status FROM replay_state WHERE id = 1")[0]["status"]


def build_filters(q, default_minutes=None):
    """クエリパラメータから WHERE 条件を作る。

    from / to / minutes は反映時刻（ts）、source_from / source_to は CSV 上の時刻（source_ts）で絞る。
    """
    conditions, params = [], {}
    for name, column in (("equipment", "equipment_id"), ("metric", "metric"), ("line", "line_id")):
        value = param_id(q, name)
        if value:
            conditions.append(f"{column} = :{name}")
            params[name] = value

    t_from, t_to = param_time(q, "from"), param_time(q, "to")
    s_from, s_to = param_time(q, "source_from"), param_time(q, "source_to")
    minutes = param_int(q, "minutes", None, 1, 525600)
    if minutes is None and not any((t_from, t_to, s_from, s_to)):
        minutes = default_minutes
    if minutes:
        conditions.append("ts >= now() - :minutes * interval '1 minute'")
        params["minutes"] = minutes
    if t_from:
        conditions.append("ts >= CAST(:from AS timestamptz)")
        params["from"] = t_from
    if t_to:
        conditions.append("ts <= CAST(:to AS timestamptz)")
        params["to"] = t_to

    if s_from or s_to:
        # CSV は時刻順なので、CSV 上の時刻の範囲は seq の範囲に置き換えられる（staging の ts 索引で速く引ける）。
        # 取り込み中は staging がロックされていて待たされるので受け付けない
        if replay_status() == "loading":
            raise Busy("CSV の取り込み中のため、source_from / source_to は使えません。取り込み完了後に再実行してください")
        if s_from:
            conditions.append(
                "seq >= COALESCE((SELECT seq FROM staging_readings WHERE ts >= CAST(:source_from AS timestamptz) "
                "ORDER BY ts, seq LIMIT 1), 9223372036854775807)")
            params["source_from"] = s_from
        if s_to:
            conditions.append(
                "seq <= COALESCE((SELECT seq FROM staging_readings WHERE ts <= CAST(:source_to AS timestamptz) "
                "ORDER BY ts DESC, seq DESC LIMIT 1), 0)")
            params["source_to"] = s_to

    window = {k: v for k, v in (("minutes", minutes), ("from", t_from), ("to", t_to),
                                ("source_from", s_from), ("source_to", s_to)) if v}
    return conditions, params, {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in window.items()}


def where(conditions):
    return ("WHERE " + " AND ".join(conditions)) if conditions else ""


def reading(row):
    return {
        "seq": row["seq"],
        "ts": to_jst(row["ts"]),
        "source_ts": to_jst(row["source_ts"]),
        "line_id": row["line_id"],
        "equipment_id": row["equipment_id"],
        "metric": row["metric"],
        "value": row["value"],
        "unit": row["unit"] or "",
    }


# ---------- エンドポイント ----------

def get_status(q):
    state = query(
        "SELECT status, source_key, speed, total_rows, cursor_seq, started_at, base_ts, updated_at, message, "
        "clock_timestamp() AS now FROM replay_state WHERE id = 1")[0]
    result = {
        "status": state["status"],
        "source_key": state["source_key"],
        "speed": state["speed"],
        "total_rows": state["total_rows"],
        "reflected_rows": state["cursor_seq"],
        "percent": round(state["cursor_seq"] * 100 / state["total_rows"], 1) if state["total_rows"] else 0,
        "started_at": to_jst(state["started_at"]),
        "updated_at": to_jst(state["updated_at"]),
        "message": state["message"],
    }
    if state["status"] in ("running", "done") and state["total_rows"]:
        # 取り込み中（loading）以外は staging を参照できる。CSV 上の現在位置・終端と完了見込みを出す
        edge = query(
            "SELECT (SELECT ts FROM staging_readings WHERE seq = :cursor) AS csv_position, "
            "(SELECT ts FROM staging_readings WHERE seq = :total) AS csv_end",
            {"cursor": state["cursor_seq"], "total": state["total_rows"]})[0]
        result["csv_position"] = to_jst(edge["csv_position"])
        result["csv_end"] = to_jst(edge["csv_end"])
        if state["status"] == "running":
            now, started = parse_utc(state["now"]), parse_utc(state["started_at"])
            base, end = parse_utc(state["base_ts"]), parse_utc(edge["csv_end"])
            remaining = max(0.0, ((end - base) / state["speed"] - (now - started)).total_seconds())
            result["eta_seconds"] = round(remaining)
            result["estimated_finish"] = (now + timedelta(seconds=remaining)).astimezone(JST).isoformat(timespec="seconds")
    return result


def get_latest(q):
    conditions = ["seq > (SELECT COALESCE(max(seq), 0) FROM readings) - :window"]
    params = {"window": LATEST_WINDOW_ROWS}
    for name, column in (("equipment", "equipment_id"), ("metric", "metric"), ("line", "line_id")):
        value = param_id(q, name)
        if value:
            conditions.append(f"{column} = :{name}")
            params[name] = value
    rows = query(
        "SELECT DISTINCT ON (equipment_id, metric) seq, ts, source_ts, line_id, equipment_id, metric, value, unit "
        f"FROM readings {where(conditions)} ORDER BY equipment_id, metric, seq DESC", params)
    return {"items": [reading(r) for r in rows], "count": len(rows)}


def get_readings(q):
    conditions, params, window = build_filters(q)
    limit = param_int(q, "limit", DEFAULT_LIMIT, 1, MAX_LIMIT)
    after = param_int(q, "next", 0, 0, 2**62)
    conditions.insert(0, "seq > :after")
    params.update(after=after, fetch=limit + 1)
    rows = query(
        "SELECT seq, ts, source_ts, line_id, equipment_id, metric, value, unit "
        f"FROM readings {where(conditions)} ORDER BY seq LIMIT :fetch", params)
    more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": [reading(r) for r in rows],
        "count": len(rows),
        "next": str(rows[-1]["seq"]) if more else None,
        "window": window,
    }


def get_summary(q):
    conditions, params, window = build_filters(q, default_minutes=60)
    rows = query(
        "SELECT equipment_id, metric, unit, count(*) AS n, avg(value) AS avg, min(value) AS min, max(value) AS max, "
        "min(source_ts) AS source_from, max(source_ts) AS source_to "
        f"FROM readings {where(conditions)} GROUP BY equipment_id, metric, unit ORDER BY equipment_id, metric",
        params)
    status_conditions = conditions + ["metric = 'status'"]
    status_rows = query(
        f"SELECT equipment_id, value, count(*) AS n FROM readings {where(status_conditions)} "
        "GROUP BY equipment_id, value", params)

    equipment = {}
    for r in rows:
        item = equipment.setdefault(r["equipment_id"], {"equipment_id": r["equipment_id"], "metrics": []})
        if r["metric"] == "status":
            # 1 計測あたりの時間（CSV 上）から、停止・異常停止の時間を見積もる
            span = parse_utc(r["source_to"]) - parse_utc(r["source_from"])
            step_min = span.total_seconds() / 60 / (r["n"] - 1) if r["n"] > 1 else 0
            counts = {label: 0 for label in STATUS_LABELS.values()}
            for s in status_rows:
                if s["equipment_id"] == r["equipment_id"]:
                    counts[STATUS_LABELS.get(int(s["value"]), "unknown")] = s["n"]
            item["status"] = {
                "samples": r["n"],
                **counts,
                "run_ratio": round(counts["run"] / r["n"], 3),
                "stop_minutes": round(counts["stop"] * step_min),
                "alarm_minutes": round(counts["alarm"] * step_min),
            }
        else:
            item["metrics"].append({
                "metric": r["metric"], "unit": r["unit"] or "", "count": r["n"],
                "avg": round(r["avg"], 3), "min": r["min"], "max": r["max"],
            })
        item["source_from"] = min(filter(None, [item.get("source_from"), to_jst(r["source_from"])]))
        item["source_to"] = max(filter(None, [item.get("source_to"), to_jst(r["source_to"])]))
    return {"window": window, "equipment": list(equipment.values())}


MIN_SPEED, MAX_SPEED = 0.1, 100000


def post_reset(q, body):
    speed = body.get("speed")
    stop = body.get("stop", False)
    if speed is not None:
        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not MIN_SPEED <= speed <= MAX_SPEED:
            raise BadRequest(f"speed は {MIN_SPEED}〜{MAX_SPEED} の数値で指定してください")
        speed = float(speed)
    if not isinstance(stop, bool):
        raise BadRequest("stop は true / false で指定してください")

    # 実行中の replayer と競合させないよう、先にスケジュールを止めてから DB を戻す
    schedule.set_enabled(False)
    result = query("SELECT reset_replay(CAST(:speed AS real), :stop) AS result", {"speed": speed, "stop": stop})[0]["result"]
    if result == "loading":
        schedule.set_enabled(True)  # 取り込み中は loader がスケジュールを動かしているので戻す
        raise Conflict("CSV の取り込み中のためリセットできません。取り込み完了後に再実行してください")
    if result == "empty":
        raise Conflict("CSV がまだ取り込まれていません。先に incoming/ に CSV をアップロードしてください")
    if result == "running":
        schedule.set_enabled(True)
    return get_status(q)


ROUTES = {
    ("GET", "/status"): get_status,
    ("GET", "/latest"): get_latest,
    ("GET", "/readings"): get_readings,
    ("GET", "/summary"): get_summary,
    ("POST", "/reset"): post_reset,
}


def parse_body(event):
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    if not raw.strip():
        return {}
    try:
        body = json.loads(raw)
    except ValueError:
        raise BadRequest("本文は JSON で送ってください（例: {\"speed\": 60}）") from None
    if not isinstance(body, dict):
        raise BadRequest("本文は JSON オブジェクトで送ってください（例: {\"speed\": 60}）")
    return body


def respond(code, body, headers=None):
    return {
        "statusCode": code,
        "headers": {"Content-Type": "application/json; charset=utf-8", **(headers or {})},
        "body": json.dumps(body, ensure_ascii=False, default=str),
    }


def handler(event, context):
    method = event["requestContext"]["http"]["method"]
    path = event.get("rawPath", "/").rstrip("/") or "/"
    route = ROUTES.get((method, path))
    if route is None:
        return respond(404, {"error": f"{method} {path} はありません",
                             "endpoints": [f"{m} {p}" for m, p in ROUTES]})
    try:
        q = event.get("queryStringParameters") or {}
        if method == "POST":
            return respond(200, route(q, parse_body(event)))
        return respond(200, route(q))
    except BadRequest as e:
        return respond(400, {"error": str(e)})
    except Conflict as e:
        return respond(409, {"error": str(e)})
    except Busy as e:
        return respond(503, {"error": str(e)}, {"Retry-After": "60"})
    except ClientError as e:
        if e.response["Error"]["Code"] == "DatabaseResumingException":
            return respond(503, {"error": "DB が一時停止から再開中です。30 秒ほどしてから再実行してください"},
                           {"Retry-After": "30"})
        raise
