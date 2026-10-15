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

-- ============================================================ ここから店舗のシステム(ハンズオン6)
-- 入店カウンタは people_count、POS は pos_transactions テーブルです。
-- 店舗の数字は「日本時間の1日(営業日)」で見ます。dt はAWSが受け取った日(UTC)なので、
-- dt を前日と当日の2日分にしてから、時刻を日本時間に直して「今日」に絞ります。
-- (日本時間の0時〜9時は、UTCでは前日になるため)

-- 10. 1時間ごとの来店客数(入店数)
--     同じ1分の集計が2回届くことがある(QoS1の重複、取り直し)ので、センサーと時刻で重複を除いてから足す
SELECT date_format(date_trunc('hour', at_timezone(from_iso8601_timestamp(ts), 'Asia/Tokyo')), '%m/%d %H:00') AS hour_jst,
       sum(in_count) AS visitors, sum(out_count) AS exits
FROM (
  SELECT DISTINCT sensor_id, ts, in_count, out_count
  FROM people_count
  WHERE dt IN (date_format(current_date - interval '1' day, '%Y-%m-%d'), date_format(current_date, '%Y-%m-%d'))
    AND date(at_timezone(from_iso8601_timestamp(ts), 'Asia/Tokyo')) = date(at_timezone(current_timestamp, 'Asia/Tokyo'))
)
GROUP BY 1
ORDER BY 1;

-- 11. 1時間ごとの売上・客数・客単価
--     取引は「店舗コード+取引番号」で1件にしてから集計する(重複を除く)
--     取消(VOID)は金額がマイナスなので、足せば打ち消される。客数は 売上の件数 − 取消の件数
WITH txn AS (
  SELECT * FROM (
    SELECT *, row_number() OVER (PARTITION BY store_code, txn_id ORDER BY received_at_ms) AS rn
    FROM pos_transactions
    WHERE dt IN (date_format(current_date - interval '1' day, '%Y-%m-%d'), date_format(current_date, '%Y-%m-%d'))
      AND date(at_timezone(from_iso8601_timestamp(ts), 'Asia/Tokyo')) = date(at_timezone(current_timestamp, 'Asia/Tokyo'))
  ) WHERE rn = 1
)
SELECT date_format(date_trunc('hour', at_timezone(from_iso8601_timestamp(ts), 'Asia/Tokyo')), '%m/%d %H:00') AS hour_jst,
       sum(total_yen) AS sales_yen,
       count_if(txn_type = 'SALE') - count_if(txn_type = 'VOID') AS customers,
       round(sum(total_yen) * 1.0 / nullif(count_if(txn_type = 'SALE') - count_if(txn_type = 'VOID'), 0)) AS yen_per_customer
FROM txn
GROUP BY 1
ORDER BY 1;

-- 12. 買上率(会計した客数 ÷ 来店客数):来店したお客様のうち、何割が買って帰ったか
--     入店カウンタとPOSという別々のシステムのデータを、時刻(日本時間の1時間)で突き合わせる
WITH visit AS (
  SELECT date_trunc('hour', at_timezone(from_iso8601_timestamp(ts), 'Asia/Tokyo')) AS h, sum(in_count) AS visitors
  FROM (SELECT DISTINCT sensor_id, ts, in_count FROM people_count
        WHERE dt IN (date_format(current_date - interval '1' day, '%Y-%m-%d'), date_format(current_date, '%Y-%m-%d')))
  GROUP BY 1
), sale AS (
  SELECT date_trunc('hour', at_timezone(from_iso8601_timestamp(ts), 'Asia/Tokyo')) AS h,
         count(DISTINCT CASE WHEN txn_type = 'SALE' THEN txn_id END)
           - count(DISTINCT CASE WHEN txn_type = 'VOID' THEN txn_id END) AS customers
  FROM pos_transactions
  WHERE dt IN (date_format(current_date - interval '1' day, '%Y-%m-%d'), date_format(current_date, '%Y-%m-%d'))
  GROUP BY 1
)
SELECT date_format(v.h, '%m/%d %H:00') AS hour_jst, v.visitors, s.customers,
       round(100.0 * s.customers / nullif(v.visitors, 0), 1) AS buy_rate_pct
FROM visit v LEFT JOIN sale s ON v.h = s.h
WHERE date(v.h) = date(at_timezone(current_timestamp, 'Asia/Tokyo'))
ORDER BY v.h;

-- 13. 売れ筋(今日の商品別の売上 上位10)。明細(items)は配列なので UNNEST で1行ずつに展開する
WITH txn AS (
  SELECT * FROM (
    SELECT *, row_number() OVER (PARTITION BY store_code, txn_id ORDER BY received_at_ms) AS rn
    FROM pos_transactions
    WHERE dt IN (date_format(current_date - interval '1' day, '%Y-%m-%d'), date_format(current_date, '%Y-%m-%d'))
      AND date(at_timezone(from_iso8601_timestamp(ts), 'Asia/Tokyo')) = date(at_timezone(current_timestamp, 'Asia/Tokyo'))
  ) WHERE rn = 1
)
SELECT i.category, i.jan, i.product_name, sum(i.qty) AS qty, sum(i.amount_yen) AS sales_yen
FROM txn CROSS JOIN UNNEST(items) AS t(i)
GROUP BY 1, 2, 3
ORDER BY sales_yen DESC
LIMIT 10;

-- 14. レジごとの「会計してからAWSに届くまで」の時間。セルフレジ(4番)は店舗サーバーにまとめて書くので遅い
SELECT register_no, count(*) AS txns,
       round(avg((received_at_ms - to_unixtime(from_iso8601_timestamp(ts)) * 1000) / 1000.0)) AS avg_delay_sec,
       round(max((received_at_ms - to_unixtime(from_iso8601_timestamp(ts)) * 1000) / 1000.0)) AS max_delay_sec
FROM pos_transactions
WHERE dt = date_format(current_date, '%Y-%m-%d')
GROUP BY register_no
ORDER BY register_no;

-- 15. 取りこぼしチェック:取引番号(連番)の欠番を探す。0件なら、店舗サーバーの取引はすべて届いている
SELECT prev_txn_id, txn_id, txn_id - prev_txn_id - 1 AS missing
FROM (
  SELECT txn_id, lag(txn_id) OVER (ORDER BY txn_id) AS prev_txn_id
  FROM (SELECT DISTINCT txn_id FROM pos_transactions
        WHERE dt IN (date_format(current_date - interval '1' day, '%Y-%m-%d'), date_format(current_date, '%Y-%m-%d')))
)
WHERE txn_id - prev_txn_id > 1;

-- ============================================================ ここから在庫と突き合わせ(ハンズオン7)
-- 在庫は inventory_snapshot テーブル(在庫・発注システムが10分ごとに書き出すCSV)。
-- 在庫は「その時点の一覧(スナップショット)」なので、同じ商品が書き出しのたびに1行ずつ増えていきます。
-- 「今の在庫」は、いちばん新しい在庫日時(snapshot_at)の行だけを使います。

-- 16. 今の在庫と在庫日数(在庫数 ÷ 直近24時間の販売数)。小さい順 = 欠品が近い順
--     ※ POSのデータが24時間分たまっていないうちは販売数が少なく出るので、在庫日数は大きめに出ます
WITH latest AS (
  SELECT * FROM inventory_snapshot
  WHERE dt IN (date_format(current_date - interval '1' day, '%Y-%m-%d'), date_format(current_date, '%Y-%m-%d'))
    AND snapshot_at = (SELECT max(snapshot_at) FROM inventory_snapshot
                       WHERE dt IN (date_format(current_date - interval '1' day, '%Y-%m-%d'), date_format(current_date, '%Y-%m-%d')))
), txn AS (
  SELECT * FROM (
    SELECT *, row_number() OVER (PARTITION BY store_code, txn_id ORDER BY received_at_ms) AS rn
    FROM pos_transactions
    WHERE dt IN (date_format(current_date - interval '1' day, '%Y-%m-%d'), date_format(current_date, '%Y-%m-%d'))
      AND from_iso8601_timestamp(ts) > current_timestamp - interval '24' hour
  ) WHERE rn = 1
), sold AS (
  SELECT i.jan, sum(i.qty) AS sold_24h
  FROM txn CROSS JOIN UNNEST(items) AS t(i)
  GROUP BY 1
)
SELECT l.category, l.product_name, l.location, l.stock_qty, l.reorder_point, s.sold_24h,
       round(l.stock_qty * 1.0 / nullif(s.sold_24h, 0), 2) AS days_of_stock,
       l.arrival_due
FROM latest l LEFT JOIN sold s ON l.jan = s.jan
ORDER BY days_of_stock;

-- 17. 発注点を割っている商品と、入荷予定(今の在庫で)
SELECT category, product_name, stock_qty, reorder_point, order_qty,
       date_format(at_timezone(from_iso8601_timestamp(nullif(arrival_due, '')), 'Asia/Tokyo'), '%m/%d %H:%i') AS arrival_jst
FROM inventory_snapshot
WHERE dt IN (date_format(current_date - interval '1' day, '%Y-%m-%d'), date_format(current_date, '%Y-%m-%d'))
  AND snapshot_at = (SELECT max(snapshot_at) FROM inventory_snapshot
                     WHERE dt IN (date_format(current_date - interval '1' day, '%Y-%m-%d'), date_format(current_date, '%Y-%m-%d')))
  AND (stock_qty <= reorder_point OR arrival_due <> '')
ORDER BY stock_qty * 1.0 / reorder_point;

-- 18. 温度逸脱 × 在庫:冷蔵ショーケース RC-01 が上限10℃を超えていた(霜取り中を除く)10分間ごとに、
--     そのケースに並んでいた商品(陳列場所 RC-01)の在庫数と在庫金額。廃棄になるかもしれない金額の目安
--     設備(Modbus)と基幹システム(CSV)という、まったく別の経路のデータを10分単位の時刻で突き合わせる
WITH over AS (
  SELECT from_unixtime(floor(to_unixtime(from_iso8601_timestamp(ts)) / 600) * 600) AS slot,
         max(metrics['case_temp_c']) AS max_temp_c, count(*) AS samples_over_10c
  FROM telemetry_raw
  WHERE dt IN (date_format(current_date - interval '1' day, '%Y-%m-%d'), date_format(current_date, '%Y-%m-%d'))
    AND equipment_id = 'RC-01' AND status = 'ok'
    AND metrics['case_temp_c'] > 10 AND metrics['defrost'] = 0
  GROUP BY 1
), stock AS (
  SELECT from_unixtime(floor(to_unixtime(from_iso8601_timestamp(snapshot_at)) / 600) * 600) AS slot,
         sum(stock_qty) AS stock_qty, sum(stock_qty * price_yen) AS stock_value_yen
  FROM inventory_snapshot
  WHERE dt IN (date_format(current_date - interval '1' day, '%Y-%m-%d'), date_format(current_date, '%Y-%m-%d'))
    AND location = 'RC-01'
  GROUP BY 1
)
SELECT date_format(at_timezone(o.slot, 'Asia/Tokyo'), '%m/%d %H:%i') AS from_jst,
       o.max_temp_c, o.samples_over_10c, s.stock_qty, s.stock_value_yen
FROM over o LEFT JOIN stock s ON o.slot = s.slot
ORDER BY o.slot;

-- 19. 店舗の1日(日本時間の1時間ごと):来店客数・客数・売上・電力・室温・ショーケースの温度逸脱を1つの表にする
--     入店カウンタ(API)・POS(DB)・設備(Modbus)の3つの経路のデータを、時刻で突き合わせる
WITH visit AS (
  SELECT date_trunc('hour', at_timezone(from_iso8601_timestamp(ts), 'Asia/Tokyo')) AS h, sum(in_count) AS visitors
  FROM (SELECT DISTINCT sensor_id, ts, in_count FROM people_count
        WHERE dt IN (date_format(current_date - interval '1' day, '%Y-%m-%d'), date_format(current_date, '%Y-%m-%d')))
  GROUP BY 1
), sale AS (
  SELECT date_trunc('hour', at_timezone(from_iso8601_timestamp(ts), 'Asia/Tokyo')) AS h,
         count_if(txn_type = 'SALE') - count_if(txn_type = 'VOID') AS customers, sum(total_yen) AS sales_yen
  FROM (SELECT *, row_number() OVER (PARTITION BY store_code, txn_id ORDER BY received_at_ms) AS rn
        FROM pos_transactions
        WHERE dt IN (date_format(current_date - interval '1' day, '%Y-%m-%d'), date_format(current_date, '%Y-%m-%d')))
  WHERE rn = 1
  GROUP BY 1
), equip AS (
  SELECT date_trunc('hour', at_timezone(from_iso8601_timestamp(ts), 'Asia/Tokyo')) AS h,
         round(avg(CASE WHEN equipment_id = 'PM-01' THEN metrics['power_kw'] END), 1) AS avg_power_kw,
         round(max(CASE WHEN equipment_id = 'PM-01' THEN metrics['energy_kwh'] END)
               - min(CASE WHEN equipment_id = 'PM-01' THEN metrics['energy_kwh'] END), 1) AS used_kwh,
         round(avg(CASE WHEN equipment_id = 'AC-01' THEN metrics['room_temp_c'] END), 1) AS avg_room_temp_c,
         count_if(equipment_id = 'RC-01' AND metrics['case_temp_c'] > 10 AND metrics['defrost'] = 0) AS rc_over_10c
  FROM telemetry_raw
  WHERE dt IN (date_format(current_date - interval '1' day, '%Y-%m-%d'), date_format(current_date, '%Y-%m-%d'))
    AND status = 'ok'
  GROUP BY 1
)
SELECT date_format(e.h, '%m/%d %H:00') AS hour_jst, v.visitors, s.customers, s.sales_yen,
       e.avg_power_kw, e.used_kwh, e.avg_room_temp_c, e.rc_over_10c
FROM equip e
LEFT JOIN visit v ON e.h = v.h
LEFT JOIN sale s ON e.h = s.h
WHERE date(e.h) = date(at_timezone(current_timestamp, 'Asia/Tokyo'))
ORDER BY e.h;
