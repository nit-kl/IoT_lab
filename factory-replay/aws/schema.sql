-- テーブル定義。setup_aws.py が Data API で 1 文ずつ実行する（何度実行しても同じ結果になる）。
-- Data API は 1 回に 1 文しか実行できないため、setup_aws.py は ; で文を区切る（$$ 〜 $$ の中は区切らない）。
-- 文字列リテラルの中には ; を書かないこと。

-- S3 から CSV を直接取り込むための拡張（aws_commons も一緒に入る）
CREATE EXTENSION IF NOT EXISTS aws_s3 CASCADE;

-- CSV をそのまま全部入れる（リプレイの元データ）。seq は取り込み順 = CSV の行順
CREATE TABLE IF NOT EXISTS staging_readings (
  seq          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ts           TIMESTAMPTZ NOT NULL,
  line_id      TEXT NOT NULL,
  equipment_id TEXT NOT NULL,
  metric       TEXT NOT NULL,
  value        DOUBLE PRECISION,
  unit         TEXT
);

CREATE INDEX IF NOT EXISTS staging_readings_ts_idx ON staging_readings (ts);

-- 利用者が参照する本テーブル（リプレイで少しずつ増える）
CREATE TABLE IF NOT EXISTS readings (
  seq          BIGINT PRIMARY KEY,
  ts           TIMESTAMPTZ NOT NULL,
  source_ts    TIMESTAMPTZ NOT NULL,
  line_id      TEXT NOT NULL,
  equipment_id TEXT NOT NULL,
  metric       TEXT NOT NULL,
  value        DOUBLE PRECISION,
  unit         TEXT
);

CREATE INDEX IF NOT EXISTS readings_equipment_metric_ts_idx ON readings (equipment_id, metric, ts);

CREATE INDEX IF NOT EXISTS readings_ts_idx ON readings (ts);

-- リプレイの進捗（常に 1 行）
CREATE TABLE IF NOT EXISTS replay_state (
  id           INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  status       TEXT NOT NULL DEFAULT 'idle'
               CHECK (status IN ('idle', 'loading', 'running', 'done', 'error')),
  source_key   TEXT,
  started_at   TIMESTAMPTZ,
  base_ts      TIMESTAMPTZ,
  total_rows   BIGINT NOT NULL DEFAULT 0,
  speed        REAL NOT NULL DEFAULT 60 CHECK (speed > 0),
  cursor_seq   BIGINT NOT NULL DEFAULT 0,
  message      TEXT,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO replay_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- CSV を staging_readings に取り込み、readings を空にしてリプレイを開始する（loader Lambda が呼ぶ）。
-- 1 トランザクションで行うので、失敗したら元の状態のまま status = 'error' になる。
-- readings の TRUNCATE は取り込み完了後に行い、取り込み中も readings を参照できるようにしている。
-- source_key は常に「いま staging_readings に入っている CSV」を指す（失敗した CSV の名前は message に入れる）。
CREATE OR REPLACE FUNCTION start_replay(p_bucket TEXT, p_key TEXT, p_region TEXT)
RETURNS TEXT
LANGUAGE plpgsql
AS $$
DECLARE
  v_rows BIGINT;
  v_base TIMESTAMPTZ;
BEGIN
  TRUNCATE staging_readings RESTART IDENTITY;
  PERFORM aws_s3.table_import_from_s3(
    'staging_readings',
    'ts,line_id,equipment_id,metric,value,unit',
    '(FORMAT csv, HEADER true)',
    aws_commons.create_s3_uri(p_bucket, p_key, p_region));

  SELECT count(*), min(ts) INTO v_rows, v_base FROM staging_readings;
  IF v_rows = 0 THEN
    RAISE EXCEPTION 'CSV にデータ行がありません';
  END IF;

  TRUNCATE readings;
  -- 取り込みに時間がかかっても、リプレイの起点は取り込み完了時刻にする（now() はトランザクション開始時刻）
  UPDATE replay_state
     SET status = 'running', source_key = p_key, started_at = clock_timestamp(),
         base_ts = v_base, total_rows = v_rows, cursor_seq = 0, message = NULL,
         updated_at = clock_timestamp()
   WHERE id = 1;
  RETURN 'running';
EXCEPTION WHEN OTHERS THEN
  UPDATE replay_state
     SET status = 'error', message = left(p_key || ': ' || SQLERRM, 500), updated_at = clock_timestamp()
   WHERE id = 1;
  RETURN 'error';
END;
$$;

-- readings を空にして、staging_readings の先頭から再生し直す（HTTP API の POST /reset が呼ぶ）。
-- S3 は読み直さない。p_speed が NULL なら速度は変えない。p_stop が true なら空にするだけで再生しない（idle）。
-- 戻り値: running / idle、または受け付けられない理由（loading = 取り込み中、empty = CSV 未取り込み）
CREATE OR REPLACE FUNCTION reset_replay(p_speed REAL, p_stop BOOLEAN)
RETURNS TEXT
LANGUAGE plpgsql
AS $$
DECLARE
  v_status TEXT;
  v_total  BIGINT;
BEGIN
  -- replayer と同じく replay_state → readings の順にロックする（デッドロックを避ける）
  SELECT status, total_rows INTO v_status, v_total FROM replay_state WHERE id = 1 FOR UPDATE;
  IF v_status = 'loading' THEN
    RETURN 'loading';
  END IF;
  IF v_total = 0 AND NOT p_stop THEN
    RETURN 'empty';
  END IF;

  TRUNCATE readings;
  UPDATE replay_state
     SET status = CASE WHEN p_stop THEN 'idle' ELSE 'running' END,
         started_at = clock_timestamp(), cursor_seq = 0, speed = COALESCE(p_speed, speed),
         message = NULL, updated_at = clock_timestamp()
   WHERE id = 1;
  RETURN CASE WHEN p_stop THEN 'idle' ELSE 'running' END;
END;
$$;
