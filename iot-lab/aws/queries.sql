-- Athena(ワークグループ: iot-lab、データベース: iot_lab)で実行するサンプルクエリ
-- dt で日付を絞ると、読むデータ量(=料金)が減ります

-- 1. 届いたデータを見る
SELECT * FROM telemetry_raw
WHERE dt = date_format(current_date, '%Y-%m-%d')
ORDER BY ts DESC
LIMIT 20;

-- 2. 空調機の室温を時系列で取り出す(map から値を取り出す)
SELECT ts, metrics['room_temp_c'] AS room_temp_c, metrics['run_status'] AS run_status
FROM telemetry_raw
WHERE dt = date_format(current_date, '%Y-%m-%d')
  AND equipment_id = 'AHU-01' AND status = 'ok'
ORDER BY ts;

-- 3. 10分ごとの平均室温(ダウンサンプリング)
SELECT from_unixtime(floor(to_unixtime(from_iso8601_timestamp(ts)) / 600) * 600) AS bucket_10min,
       round(avg(metrics['room_temp_c']), 2) AS avg_room_temp_c,
       count(*) AS samples
FROM telemetry_raw
WHERE dt = date_format(current_date, '%Y-%m-%d')
  AND equipment_id = 'AHU-01' AND status = 'ok'
GROUP BY 1
ORDER BY 1;

-- 4. 送信遅延を見る(ts = ゲートウェイが読んだ時刻、received_at_ms = AWSが受け取った時刻)
--    回線断→復旧の後は、バッファから再送されたデータの遅延が大きくなるのが分かります
SELECT equipment_id, ts,
       (received_at_ms - to_unixtime(from_iso8601_timestamp(ts)) * 1000) / 1000.0 AS delay_sec
FROM telemetry_raw
WHERE dt = date_format(current_date, '%Y-%m-%d')
ORDER BY delay_sec DESC
LIMIT 20;

-- 5. 欠損チェック:seq の飛びを探す(seq はゲートウェイ単位の連番。再起動時は時刻ベースで飛ぶので、それ以外の飛びに注目)
SELECT gateway_id, prev_seq, seq, seq - prev_seq AS gap
FROM (
  SELECT gateway_id, seq, lag(seq) OVER (PARTITION BY gateway_id ORDER BY seq) AS prev_seq
  FROM telemetry_raw
  WHERE dt = date_format(current_date, '%Y-%m-%d')
)
WHERE seq - prev_seq > 1;

-- 6. 重複チェック:同じ message_id が2回以上届いていないか(QoS1 は重複があり得る)
SELECT message_id, count(*) AS n
FROM telemetry_raw
WHERE dt = date_format(current_date, '%Y-%m-%d')
GROUP BY message_id
HAVING count(*) > 1;

-- 7. 通信エラーと異常コードの発生状況
SELECT equipment_id, status, count(*) AS n,
       sum(CASE WHEN metrics['alarm_code'] > 0 THEN 1 ELSE 0 END) AS alarms
FROM telemetry_raw
WHERE dt = date_format(current_date, '%Y-%m-%d')
GROUP BY equipment_id, status;
