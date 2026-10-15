-- Athena(ワークグループ: xcp-store-iot-<owner>、データベース: xcp_store_iot_<owner>)で実行するサンプルクエリ
-- dt で日付を絞ると、読むデータ量(=料金)が減ります
-- ts と dt は UTC です。店舗の業務(HACCPの記録、電力のデマンド)は日本時間で見るので、表示のときに変換します

-- 1. 届いたデータを見る
SELECT * FROM telemetry_raw
WHERE dt = date_format(current_date, '%Y-%m-%d')
ORDER BY ts DESC
LIMIT 20;

-- 2. 冷蔵ショーケースの庫内温度を時系列で取り出す(map から値を取り出す)
SELECT at_timezone(from_iso8601_timestamp(ts), 'Asia/Tokyo') AS ts_jst,
       metrics['case_temp_c'] AS case_temp_c, metrics['defrost'] AS defrost, metrics['door_open'] AS door_open
FROM telemetry_raw
WHERE dt = date_format(current_date, '%Y-%m-%d')
  AND equipment_id = 'RC-01' AND status = 'ok'
ORDER BY ts;

-- 3. HACCPの温度記録:1時間ごとの庫内温度(最高・最低・平均)と、上限10℃を超えた回数
--    霜取り中の値は分けて数えます(霜取り中に上がるのは正常。記録表にもそう注記するのが普通)
SELECT date_format(date_trunc('hour', at_timezone(from_iso8601_timestamp(ts), 'Asia/Tokyo')), '%m/%d %H:00') AS hour_jst,
       round(max(metrics['case_temp_c']) FILTER (WHERE metrics['defrost'] = 0), 1) AS max_c,
       round(min(metrics['case_temp_c']) FILTER (WHERE metrics['defrost'] = 0), 1) AS min_c,
       round(avg(metrics['case_temp_c']) FILTER (WHERE metrics['defrost'] = 0), 1) AS avg_c,
       count_if(metrics['case_temp_c'] > 10 AND metrics['defrost'] = 0) AS over_10c,
       count_if(metrics['defrost'] = 1) AS defrost_samples,
       count(*) AS samples
FROM telemetry_raw
WHERE dt = date_format(current_date, '%Y-%m-%d')
  AND equipment_id = 'RC-01' AND status = 'ok'
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

-- 8. 電力のデマンド:30分ごとの使用電力量(kWh)と、その30分の平均電力(kW)
--    電気の基本料金は「30分の平均電力の、その月の最大値(デマンド)」で決まるので、店舗の節電はここを見ます
--    積算電力量(energy_kwh)は増え続ける値なので、30分の中の最大と最小の差がその間の使用量です
SELECT date_format(from_unixtime(floor(to_unixtime(from_iso8601_timestamp(ts)) / 1800) * 1800, 'Asia/Tokyo'),
                   '%m/%d %H:%i') AS from_jst,
       round(max(metrics['energy_kwh']) - min(metrics['energy_kwh']), 1) AS used_kwh,
       round(avg(metrics['power_kw']), 1) AS avg_kw,
       round(max(metrics['power_kw']), 1) AS peak_kw
FROM telemetry_raw
WHERE dt = date_format(current_date, '%Y-%m-%d')
  AND equipment_id = 'PM-01' AND status = 'ok'
GROUP BY 1
ORDER BY 1;

-- 9. 冷凍機の発停回数(1時間あたり)。増えすぎは故障の前触れ(ガス漏れ、霜付き、設定不良など)
SELECT date_format(date_trunc('hour', at_timezone(from_iso8601_timestamp(ts), 'Asia/Tokyo')), '%m/%d %H:00') AS hour_jst,
       max(metrics['compressor_starts']) - min(metrics['compressor_starts']) AS starts,
       round(avg(metrics['freezer_temp_c']), 1) AS avg_freezer_c
FROM telemetry_raw
WHERE dt = date_format(current_date, '%Y-%m-%d')
  AND equipment_id = 'REF-01' AND status = 'ok'
GROUP BY 1
ORDER BY 1;
