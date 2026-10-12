# IoT 学習キット(空調機・PLC・iQ-R シミュレータ → AWS IoT Core → S3 → Athena → Grafana / Lambda → DynamoDB → アラーム通知 / 遠隔操作)

```
simulator/device_simulator.py   空調機(デバイスID 1)とPLC(デバイスID 2)を模擬する Modbus TCP サーバー(ポート5020)
simulator/mc_plc_simulator.py   三菱電機 iQ-R を模擬する MCプロトコル(3Eフレーム/バイナリ)サーバー(ポート5010)
simulator/force_alarm.py        空調機に異常コードを強制発生・解除するテスト用ツール
gateway/gateway.py              収集→変換→バッファ→MQTT送信、命令の受信→安全確認→書き込み を行うゲートウェイ
gateway/points.json             ポイントリスト(タグ表)
gateway/gateway_config.example.json  設定の雛形(setup_aws.py が gateway_config.json を自動作成)
aws/template.yaml               CloudFormation(IoTポリシー、ルール、S3、Glueテーブル、Athena、Grafana用IAMユーザー、最新値・通知・命令の各Lambda/DynamoDB/SNS)
aws/lambda/latest_value.py      機器ごとの最新値をDynamoDBに条件付きで書くLambda(setup_aws.py がテンプレートに埋め込む)
aws/lambda/alarm_notifier.py    異常・通信エラー・データ途絶の発生/復旧をSNSでメール通知するLambda
aws/send_command.py             ゲートウェイに命令(設定温度の変更など)を送り、結果を待つ
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

# ターミナル1: 機器シミュレータ(Modbus)
python simulator/device_simulator.py

# ターミナル2: iQ-R シミュレータ(MCプロトコル)
python simulator/mc_plc_simulator.py --verbose

# ターミナル3: ゲートウェイ(まずは画面表示だけ)
python gateway/gateway.py --console --dump-raw

# AWS へ(AWS CLI の認証設定が済んでいること)
python aws/setup_aws.py --email you@example.com   # --email はアラーム通知を受け取る場合(ハンズオン4)
python gateway/gateway.py

# 最新値を見る(ハンズオン3)
python aws/show_latest.py --watch 5

# 異常を起こして通知を受け取る(ハンズオン4)
python simulator/force_alarm.py --code 201   # 解除は --code 0

# 設定温度を遠隔で変える(ハンズオン5)
python aws/send_command.py --value 24.5

# Grafanaで見る(任意、Docker Desktopが必要)
cd grafana && docker compose up -d   # http://localhost:3000 (admin / admin)

# 片付け
python aws/teardown_aws.py   # Grafana用アクセスキーも含めて削除
cd grafana && docker compose down -v   # Grafanaのコンテナ自体を消す場合
```

詳しい手順と解説は手順書を参照してください。全体の構成図と費用の目安は、手順書と同じ docs フォルダの「アーキテクチャとコスト」にまとめています。

## 注意
- `gateway/certs/private.pem.key` と `grafana/.env` は秘密鍵と同じ扱いです。共有・Git への登録をしないでください(.gitignore 済み)。
- 作成されるのは従量課金のサービスのみですが、学習が終わったら `teardown_aws.py` で削除してください。
