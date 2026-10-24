"""1 分ごとに起動し、リプレイ開始からの経過時間 × 速度ぶんの行を staging_readings から readings へ写す。

全行写し終えたらスケジュールを無効化する（DB はその後、自動一時停止する）。
"""

import json
import os
import time

import schedule
from dataapi import DataApi

db = DataApi(os.environ["CLUSTER_ARN"], os.environ["SECRET_ARN"], os.environ["DATABASE"])
BATCH_ROWS = int(os.environ.get("BATCH_ROWS", "50000"))
# 次の起動（1 分後）と重ならないよう、1 回の起動でバッチを繰り返すのはこの秒数まで
RUN_SECONDS = 45
# これより長く loading のままなら、取り込みが失敗したとみなす
LOADING_TIMEOUT_MIN = 60

# 1 文で「写す → カーソルを進める → 終わったら done」を行う。
# replay_state を FOR UPDATE でロックするので、起動が重なっても同じ行を二重に進めない
REPLAY_SQL = """
WITH st AS (
  SELECT * FROM replay_state WHERE id = 1 AND status = 'running' FOR UPDATE
), batch AS (
  SELECT s.seq,
         st.started_at + (s.ts - st.base_ts) / st.speed AS ts,
         s.ts AS source_ts, s.line_id, s.equipment_id, s.metric, s.value, s.unit
    FROM staging_readings s, st
   WHERE s.seq > st.cursor_seq
     AND s.ts <= st.base_ts + (clock_timestamp() - st.started_at) * st.speed
   ORDER BY s.seq
   LIMIT :batch
), ins AS (
  INSERT INTO readings (seq, ts, source_ts, line_id, equipment_id, metric, value, unit)
  SELECT * FROM batch
  ON CONFLICT (seq) DO NOTHING
)
UPDATE replay_state r
   SET cursor_seq = COALESCE((SELECT max(seq) FROM batch), r.cursor_seq),
       status = CASE WHEN COALESCE((SELECT max(seq) FROM batch), r.cursor_seq) >= r.total_rows
                     THEN 'done' ELSE 'running' END,
       updated_at = clock_timestamp()
  FROM st
 WHERE r.id = st.id
RETURNING r.status, r.cursor_seq, r.total_rows, (SELECT count(*) FROM batch) AS moved
"""


def log(**fields):
    print(json.dumps(fields, ensure_ascii=False, default=str))


def handler(event, context):
    # 取り込み中（loading）は start_replay() が staging_readings をロックしているので、
    # REPLAY_SQL を投げる前に状態だけ確認する（投げると取り込み完了まで待たされる）
    status = db.execute("SELECT status FROM replay_state WHERE id = 1")[0]["status"]
    if status == "loading":
        # loader が途中で落ちて loading のまま残ると、毎分の起動で DB が一時停止できず課金が続く。
        # 長時間 loading のままなら error にしてスケジュールを止める
        stuck = db.execute(
            "UPDATE replay_state SET status = 'error', message = :msg, updated_at = now() "
            "WHERE id = 1 AND status = 'loading' AND updated_at < now() - :mins * interval '1 minute' "
            "RETURNING status",
            {"msg": f"取り込みが {LOADING_TIMEOUT_MIN} 分以上終わらないため中断扱いにしました。CSV を置き直してください",
             "mins": LOADING_TIMEOUT_MIN})
        if stuck and schedule.set_enabled(False):
            log(msg="loading のまま長時間経過したためスケジュールを停止", minutes=LOADING_TIMEOUT_MIN)
        return
    if status != "running":
        if schedule.set_enabled(False):
            log(msg="リプレイ中ではないためスケジュールを停止", status=status)
        return

    started = time.monotonic()
    moved_total = 0
    while True:
        rows = db.execute(REPLAY_SQL, {"batch": BATCH_ROWS})
        if not rows:
            return  # 直前に running でなくなった（CSV の置き直しなど）
        result = rows[0]
        moved_total += result["moved"]
        if result["moved"] < BATCH_ROWS or time.monotonic() - started > RUN_SECONDS:
            break

    log(msg="反映", moved=moved_total, cursor=result["cursor_seq"], total=result["total_rows"],
        status=result["status"])
    if result["status"] == "done":
        schedule.set_enabled(False)
        log(msg="リプレイ完了。スケジュールを停止", total=result["total_rows"])
