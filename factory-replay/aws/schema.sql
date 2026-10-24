-- テーブル定義。setup_aws.py が Data API で 1 文ずつ実行する（何度実行しても同じ結果になる）。
-- Data API は 1 回に 1 文しか実行できないため、文の区切りは行末の ; とし、文字列中に ; を書かないこと。

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
