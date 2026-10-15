# 店舗IoT 学習キット(スーパーの店舗設備・システムのシミュレータ → AWS IoT Core → S3 → Athena → Grafana / Lambda → DynamoDB → アラーム通知 / 遠隔操作)

XCPチームの検証用です。スーパー1店舗の設備(売場空調・冷蔵ショーケース・電力計・バックヤードの冷凍機盤)と店舗のシステム(入店カウンタ・POS)を模擬し、店舗ゲートウェイからAWSに送ります。

```
simulator/run_store.py          店舗の設備・システムのシミュレータ(下の6つ)をまとめて起動。--except RC-01 で特定のものを除外
simulator/aircon.py             売場空調 AC-01 を模擬する Modbus TCP サーバー(ポート5021)
simulator/showcase.py           冷蔵ショーケース RC-01 を模擬する Modbus TCP サーバー(ポート5022)
simulator/power_meter.py        電力計 PM-01 を模擬する Modbus TCP サーバー(ポート5023)
simulator/refrigerator_plc.py   冷凍機盤 REF-01 の三菱電機 iQ-R を模擬する MCプロトコル(3Eフレーム/バイナリ)サーバー(ポート5010)
simulator/people_counter.py     入店カウンタ PC-01 のREST APIを模擬する HTTP サーバー(ポート8081)
simulator/pos_server.py         店舗サーバー(POS)のDB simulator/data/pos.db に取引を書き続ける
simulator/store_model.py        来店・会計のモデルと商品マスタ(入店カウンタとPOSで共通)
simulator/common.py             シミュレータの共通部品(ポート番号、運転スケジュール)
simulator/force_alarm.py        異常コードの強制発生・解除、ショーケースの扉の開けっ放しを起こすテスト用ツール
gateway/gateway.py              収集→変換→バッファ→MQTT送信、命令の受信→安全確認→書き込み を行う店舗ゲートウェイ
gateway/points.json             ポイントリスト(タグ表)。機器ごとの接続先(host・port)もここに書く
gateway/sources.py              入店カウンタ(API)とPOS(DB)から前回の続きの差分を取る(ハンズオン6)
gateway/sources.json            店舗のシステムの接続先(enabled を true にすると取り込む)
gateway/gateway_config.example.json  設定の雛形(setup_aws.py が gateway_config.json を自動作成)
aws/template.yaml               CloudFormation(IoTポリシー、ルール、S3、Glueテーブル、Athena、Grafana用IAMユーザー、最新値・通知・命令の各Lambda/DynamoDB/SNS)
aws/lambda/latest_value.py      機器ごとの最新値をDynamoDBに条件付きで書くLambda(setup_aws.py がテンプレートに埋め込む)
aws/lambda/alarm_notifier.py    ショーケースの温度逸脱・異常・通信エラー・データ途絶の発生/復旧をSNSでメール通知するLambda
aws/naming.py                   リソースの命名(xcp-store-iot-<owner>-…)とタグ
aws/send_command.py             店舗ゲートウェイに命令(売場空調の設定温度の変更など)を送り、結果を待つ
aws/show_latest.py              DynamoDBの最新値を一覧表示(STALE判定つき)
aws/setup_aws.py                AWS環境の作成+証明書発行+Grafana用アクセスキー発行
aws/teardown_aws.py             AWS環境の削除(課金を止める)
aws/queries.sql                 Athena で試すクエリ集
grafana/docker-compose.yml      ローカルDockerでGrafanaを起動(Athenaデータソース・ダッシュボードはプロビジョニング済み)
grafana/.env.example            設定の雛形(setup_aws.py が .env を自動作成)
```

## 最短手順

```bash
python -m venv .venv
# Windows(PowerShell): .venv\Scripts\Activate.ps1   Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt

# ターミナル1: 店舗の設備・システムのシミュレータ(1機器=1プロセス。まとめて起動)
python simulator/run_store.py --verbose

# ターミナル2: ゲートウェイ(まずは画面表示だけ)
python gateway/gateway.py --console --dump-raw

# AWS へ(AWS CLI の認証設定が済んでいること)
# --owner は自分を表す名前(英小文字・数字)。チームの共用アカウントでリソースを見分けるために名前に入る
# 2回目以降は gateway/gateway_config.json に保存された owner を使うので省略できる
python aws/setup_aws.py --owner kojima --email you@example.com   # --email はアラーム通知を受け取る場合(ハンズオン4)
python gateway/gateway.py

# 最新値を見る(ハンズオン3)
python aws/show_latest.py --watch 5

# ショーケースの扉を開けっ放しにして温度逸脱の通知を受け取る(ハンズオン4)
python simulator/force_alarm.py --door open   # 閉めるのは --door close
python simulator/force_alarm.py --code 201    # 売場空調の異常。解除は --code 0

# 売場空調の設定温度を本部から変える(ハンズオン5)
python aws/send_command.py --value 28.0

# 入店カウンタとPOSを取り込む(ハンズオン6): gateway/sources.json の enabled を true にしてからゲートウェイを再起動
# Athena で aws/queries.sql の10番以降(来店客数・売上・買上率・売れ筋)

# Grafanaで見る(任意、Docker Desktopが必要)
cd grafana && docker compose up -d   # http://localhost:3000 (admin / admin)

# 片付け
python aws/teardown_aws.py   # 自分の owner のスタックを、Grafana用アクセスキーも含めて削除
cd grafana && docker compose down -v   # Grafanaのコンテナ自体を消す場合
```

詳しい手順と解説は手順書を参照してください。全体の構成図・命名のきまり・費用の目安は、手順書と同じ docs フォルダの「アーキテクチャとコスト」にまとめています。

## 命名のきまり(チームの共用アカウント)

| もの | 名前(owner が kojima の場合) |
| --- | --- |
| CloudFormationスタック | xcp-store-iot-kojima |
| 各リソース | xcp-store-iot-kojima-…(Glueデータベース・IoTルールは xcp_store_iot_kojima…) |
| MQTTトピック | store-kojima/store01/機器ID/telemetry |
| タグ | Team=xcp、Project=store-iot、Env=poc、Owner=kojima |

## 注意
- `gateway/certs/private.pem.key` と `grafana/.env` は秘密鍵と同じ扱いです。共有・Git への登録をしないでください(.gitignore 済み)。
- 作成されるのは従量課金のサービスのみですが、学習が終わったら `teardown_aws.py` で削除してください。
- 店舗版より前のキット(スタック名 iot-lab)を使っていた場合は、新しいキットで setup する前に、古い gateway_config.json を残したまま `python aws/teardown_aws.py` を実行すると、旧スタックが削除されます。
