# IoT学習ハンズオン3:Lambda+DynamoDBで最新値を持つ

Oct 12, 2026 · @Reo Kojima

前提:[IoT学習ハンズオン PC+シミュレータ+AWS](./IoT学習ハンズオン%20PC%2Bシミュレータ%2BAWS.md)のステップ1〜3が終わっていて、ゲートウェイからAWS IoT Coreへ送信できることです。ハンズオン2(Grafana)は必須ではありません。所要時間は45〜90分です。費用はLambda・DynamoDBともに学習規模なら無料枠に収まり、時間課金のサービスは作りません。

## 全体像

これまではS3に「全部の履歴」を貯め、AthenaのSQLで後から分析しました。ただ、「AHU-01の今の室温は?」「止まっている機器はどれ?」のような**今の状態**を知るたびにAthenaでS3を走査するのは、遅いうえに読んだ量だけ課金されます。

そこで、同じMQTTメッセージを2本目のIoTルールでLambdaにも渡し、**機器1台につき1行**だけを持つDynamoDBテーブルを常に最新に保ちます。

```
                         ┌─ ルール1 ─→ S3(raw/)       履歴:全部残す → Athena / Grafana
lab/gw01/+/telemetry ──┤
                         └─ ルール2 ─→ Lambda ─→ DynamoDB   現在値:機器ごとに1行を上書き
```

| 追加するもの | 役割 | 学べること |
| --- | --- | --- |
| aws/template.yaml の LatestValueRule | 同じトピックをLambdaへ流す2本目のルール | 1つのメッセージを複数の宛先に配る(ファンアウト) |
| aws/lambda/latest_value.py | 最新値をDynamoDBに書くLambda関数 | 条件付き書き込みで「古い値で上書きしない」 |
| aws/template.yaml の LatestTable | 機器IDをキーにしたDynamoDBテーブル(オンデマンド課金) | 履歴(S3)と現在値(DynamoDB)の使い分け |
| aws/template.yaml の LatestValueRole / LatestValuePermission | Lambdaの実行ロールと呼び出し許可 | 「このテーブルにUpdateItemだけ」「このルールからだけ呼べる」という最小権限 |
| aws/show_latest.py | 最新値の一覧表示 | 最終受信時刻を使った死活監視(STALE判定) |

## なぜLambdaを挟むのか

実はIoTルールには、DynamoDBへ直接書く「DynamoDBv2アクション」があります。Lambdaを使わずに済むので一見そちらが楽です。しかし、このアクションは**届いた順にそのまま上書き**します。

IoTでは、メッセージが「読んだ順」に届くとは限りません。

- **回線断からの復旧**:ゲートウェイはバッファした古いデータを古い順に再送しますが、ルールからのLambda呼び出しは非同期で並列に実行されるため、DynamoDBに書き込まれる順番は入れ替わることがあります。
- **QoS1の重複**:同じメッセージが2回届くことがあります(ハンズオン1のクエリ6)。
- **複数ゲートウェイ・複数経路**:経路ごとに遅延が違います。

単純に上書きすると、5分前のデータが後から届いたときに「最新値」が5分前に巻き戻ってしまいます。そこで、Lambdaでは次の**条件付き書き込み**を使います。

```python
"ConditionExpression": "attribute_not_exists(#ts) OR #ts < :ts",
```

「まだ1行も無い」か「保存済みのtsより新しい」ときだけ書き込み、それ以外はDynamoDB側で拒否されます。ts はUTC・ミリ秒固定の `2026-10-10T10:39:24.711Z` という形なので、文字列のまま比較しても時刻の前後と一致します(ハンズオン1で「時刻は必ずUTCで持つ」とした理由がここでも効きます)。

`aws/lambda/latest_value.py` には、ほかにも実務で効く工夫を2つ入れています。

| 工夫 | 内容 | 理由 |
| --- | --- | --- |
| last known good | status が comm_error のときは ts・status だけ更新し、metrics は前回の正常値を残す。最後に正常に読めた時刻を last_ok_ts に持つ | 「今は通信エラー、最後に読めたのは何時で、そのとき何℃だったか」が両方分かる |
| 未来時刻の拒否 | AWSが受け取った時刻(received_at_ms)より5分以上未来の ts は捨てる | 時計が狂ったゲートウェイが「2030年」のデータを1回送ると、条件付き書き込みのせいで以後その機器の最新値が一切更新されなくなる |

## ステップ1:AWS環境を更新する

1. 前回と同じくiot-labフォルダで仮想環境を有効にし、`setup_aws.py` を再実行します。スタックが更新され、Lambda・DynamoDB・ルールが追加されます(1〜3分)。証明書や `grafana/.env` は再利用されます。

   ```
   python aws/setup_aws.py
   ```

   最後に次の2行が出れば完了です。

   ```
   最新値テーブル: iot-lab-LatestTable-XXXXXXXX   確認: python aws/show_latest.py
   Lambdaのログ: aws logs tail iot-lab-LatestValueLogGroup-XXXXXXXX --follow
   ```

2. Lambdaのコードは `aws/lambda/latest_value.py` が元になっています。`setup_aws.py` がこのファイルの中身を `template.yaml` の `# __LATEST_VALUE_CODE__` の行に埋め込んでからデプロイします。コードを書き換えたときは、`setup_aws.py` を再実行すれば反映されます(CloudFormationのインラインコードは4096文字までなので、それを超えると止まるようにしてあります)。

## ステップ2:最新値を見る

1. ターミナル1・2でシミュレータを、ターミナル3でゲートウェイを動かします(ハンズオン1と同じ)。

   ```
   python simulator/device_simulator.py
   python simulator/mc_plc_simulator.py
   python gateway/gateway.py
   ```
2. ターミナル4で最新値を表示します。`--watch 5` で5秒ごとに更新されます。

   ```
   python aws/show_latest.py --watch 5
   ```

   次のように、機器ごとに1行ずつ表示されます(値は一例)。

   ```
   --- 2026-10-12 10:40:24 JST  3 台
   AHU-01       ok         fresh      4秒前  ts=10:40:20  last_ok=10:40:20  seq=1791628801
                alarm_code=0, humidity_pct=55.4, mode=1, room_temp_c=27.2, run_status=1, setpoint_c=26.0
   ...
   ```

3. ターミナル5でLambdaのログを流します(コマンドは `setup_aws.py` の最後に表示されたものをコピー)。

   ```
   aws logs tail iot-lab-LatestValueLogGroup-XXXXXXXX --follow
   ```

   10秒ごとに機器の台数分 `UPDATED AHU-01 ts=… status=ok` が出ます。

4. AWSコンソールの **DynamoDB → テーブル → iot-lab-LatestTable-… → 項目を探索** でも同じ内容が見られます。行数が機器の台数から増えないこと(履歴ではなく現在値であること)を確認してください。

## ステップ3:やってみる

| 実験 | 操作 | 期待される結果 | どこで確認 |
| --- | --- | --- | --- |
| 通信エラー | Modbusシミュレータ(ターミナル1)をCtrl+Cで止める | AHU-01 の status が comm_error に変わる。metrics と last_ok は止める直前の値のまま | show_latest.py |
| 死活監視 | ゲートウェイ(ターミナル3)をCtrl+Cで止め、1分以上待つ | 全機器が STALE になる。DynamoDBに「来なくなった」という書き込みは無いので、**受け取る側が時刻で判断する**必要がある | show_latest.py |
| 遅れて届いたデータ | 下の手順で過去の ts のメッセージを手で送る | ログに `SKIP stale` が出て、最新値は変わらない | Lambdaのログ |
| 未来のデータ | 同じ手順で ts を `2030-01-01T00:00:00.000Z` にして送る | ログに `SKIP future` が出て、最新値は変わらない | Lambdaのログ |
| 回線断からの復旧 | ゲートウェイ起動中にPCのWi‑Fiを1〜2分切り、戻す | 復旧後にバッファ分がまとめて届く。並列実行で順番が入れ替わった場合は `SKIP stale` が出るが、最終的な最新値は必ず一番新しい ts の値になる | Lambdaのログ・show_latest.py |

**遅れて届いたデータを手で送る手順**:AWSコンソールの **AWS IoT → MQTTテストクライアント → トピックに公開する** で、トピック名を `lab/gw01/AHU-01/telemetry` にし、次のメッセージを公開します。ts は今より数分前のUTC時刻にしてください(日本時間から9時間引きます)。

```json
{"message_id": "manual-1", "gateway_id": "gw01", "equipment_id": "AHU-01", "kind": "hvac",
 "ts": "2026-10-12T01:00:00.000Z", "seq": 1, "status": "ok", "metrics": {"room_temp_c": 99.9}}
```

ログに `SKIP stale` が出て、show_latest.py の室温が99.9に**ならない**ことを確認します。次に ts を今より新しいUTC時刻(ただし5分以内)に変えて送ると、今度は99.9に変わります。次の周期でゲートウェイの値に戻ります。

なお、このメッセージはルール1でS3にも保存されるため、Athenaで見ると99.9の行が1件残ります。「履歴には事実をすべて残し、現在値は正しいものだけ」という使い分けがここでも見えます。

**やってみる(コード変更)**:`aws/lambda/latest_value.py` の ConditionExpression の `#ts < :ts` を `#ts <> :ts`(「違う時刻なら書く」)に書き換えて `setup_aws.py` を再実行します。過去の ts を手で送ると、今度は最新値が99.9に**巻き戻る**ことを確認してから元に戻してください。これがDynamoDBv2アクションで直接書いた場合に近い挙動です。

## S3(履歴)とDynamoDB(現在値)の使い分け

| 問い | 向いている方 | 理由 |
| --- | --- | --- |
| 今の室温は? 今止まっている機器は? | DynamoDB | キー1つで数ミリ秒。読んだ量でなくリクエスト数で課金 |
| 昨日1日の平均室温は? 欠損はあった? | S3+Athena | 全履歴を残しているので集計・調査ができる |
| 画面やアプリに一覧を出したい | DynamoDB | 機器数ぶんの行を読むだけ。何度読んでも安い |
| 後から新しい分析をしたい | S3+Athena | 生データがあれば、後から何度でもやり直せる |

現在値テーブルは「S3の履歴から作り直せる派生データ」です。万一壊れても、ゲートウェイを動かせば次の周期で埋まります。逆に、DynamoDBにだけ書いてS3に残さない設計にすると、後から分析ができなくなります。

## 片付け

これまでと同じく1コマンドです。Lambda・DynamoDBテーブル・ロググループもスタックごと削除されます。

```
python aws/teardown_aws.py
```

## よくあるつまずき

| 症状 | 原因と対処 |
| --- | --- |
| `setup_aws.py` が「上限 4096 文字を超えています」で止まる | latest_value.py を書き足しすぎ。CloudFormationのインラインコードの上限です。コメントを削るなどして短くします |
| `setup_aws.py` が `LoginRefreshRequired` などの認証エラー | AWSの認証が切れています。`aws login` または `aws sso login` をやり直します |
| show_latest.py が「まだデータがありません」 | ゲートウェイが `--console` で動いている(MQTT送信していない)か、まだ1周期経っていない。Lambdaのログに何か出ているか確認 |
| ログに `KeyError` が出る | MQTTテストクライアントで手送りしたメッセージに必須項目(gateway_id・kind・seq・status・ts)が抜けている。ゲートウェイからのメッセージでは起きません |
| ログに `AccessDeniedException` | LatestValueRole の権限不足。template.yaml を書き換えた場合は UpdateItem と対象テーブルが合っているか確認 |
| 手で送ったのに何も起きない | トピック名が `lab/+/+/telemetry` の形になっていない(例:末尾の telemetry 抜け)とルールが拾いません |
| 全機器が STALE のまま | PCの時計がずれている可能性。Windowsの「設定 → 時刻と言語 → 今すぐ同期」 |

## 次のステップ

- [ ] 最新値テーブルの変化(DynamoDB Streams)で、アラームコードが0以外になったらSNSでメール通知する
- [ ] BACnetシミュレータを追加し、空調機をBACnet/IPで読む
- [ ] OPC UAのサンプルサーバーを立て、PLC役をOPC UAに置き換える
- [ ] ゲートウェイをAWS IoT Greengrassのコンポーネントにする
