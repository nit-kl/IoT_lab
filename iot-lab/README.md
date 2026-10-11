# IoT 学習キット(空調機・PLC・iQ-R シミュレータ → AWS IoT Core → S3 → Athena)

```
simulator/device_simulator.py   空調機(デバイスID 1)とPLC(デバイスID 2)を模擬する Modbus TCP サーバー(ポート5020)
simulator/mc_plc_simulator.py   三菱電機 iQ-R を模擬する MCプロトコル(3Eフレーム/バイナリ)サーバー(ポート5010)
gateway/gateway.py              収集→変換→バッファ→MQTT送信 を行うゲートウェイ
gateway/points.json             ポイントリスト(タグ表)
gateway/gateway_config.example.json  設定の雛形(setup_aws.py が gateway_config.json を自動作成)
aws/template.yaml               CloudFormation(IoTポリシー、ルール、S3、Glueテーブル、Athena)
aws/setup_aws.py                AWS環境の作成+証明書発行
aws/teardown_aws.py             AWS環境の削除(課金を止める)
aws/queries.sql                 Athena で試すクエリ集
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
python aws/setup_aws.py
python gateway/gateway.py

# 片付け
python aws/teardown_aws.py
```

詳しい手順と解説は手順書を参照してください。

## 注意
- `gateway/certs/private.pem.key` は秘密鍵です。共有・Git への登録をしないでください(.gitignore 済み)。
- 作成されるのは従量課金のサービスのみですが、学習が終わったら `teardown_aws.py` で削除してください。
