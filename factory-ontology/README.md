# factory-ontology

工場の Oracle データをクラウドへ集め、ナレッジグラフにして、役割ごとに適切な情報を適切なタイミングでメール通知する仕組みの検証キットです。AI は次の段階とし、今は台本どおりのモックを使います。

| 文書 | 内容 |
|---|---|
| [docs/実現方針.md](docs/実現方針.md) | 目的、構成、決定事項、進め方 |
| [docs/デモ環境準備手順.md](docs/デモ環境準備手順.md) | Oracle と模擬データの準備 |

| フォルダ | 内容 |
|---|---|
| `onprem/db/` | Oracle の表定義 |
| `onprem/simulator/` | 模擬データの生成と投入、動いている工場の再現、デモのトラブルの発生 |
| `onprem/extractor/` | Oracle の差分を S3 へ送る抽出プログラムと、許可リスト |
| `onprem/tests/` | テスト(Oracle に接続できないときは、接続が必要なものだけ飛ばす) |
| `notify/` | 検知、宛先の決定、台本モック、メールの組み立て |
| `cloud/` | AWS の環境の作成と撤去、Lambda |
| `graph/` | ナレッジグラフの語彙、変換定義、問い合わせ、ツール([説明](graph/README.md)) |
| `viewer/` | 手元で動くグラフの画面 |

テストの実行(`onprem`、`graph` の各フォルダで。通知のテストは `factory-ontology` フォルダで `graph\.venv\Scripts\python -m pytest notify\tests`):

```powershell
.venv\Scripts\python -m pytest tests
```

現在の状況:ステップ 6 まで完了しました。AWS の環境は撤去済みです。手順書の「クラウドへの吸い上げ」から作り直せます。デモの進め方は手順書の「デモの進め方」を参照してください。AI を本物にするときは、`notify/writer.py` の差し替えから始めます。
