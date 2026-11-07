# factory-ontology

工場の Oracle データをクラウドへ集め、ナレッジグラフにして、役割ごとに適切な情報を適切なタイミングでメール通知する仕組みの検証キットです。AI は次の段階とし、今は台本どおりのモックを使います。

| 文書 | 内容 |
|---|---|
| [docs/実現方針.md](docs/実現方針.md) | 目的、構成、決定事項、進め方 |
| [docs/デモ環境準備手順.md](docs/デモ環境準備手順.md) | Oracle と模擬データの準備 |

| フォルダ | 内容 |
|---|---|
| `onprem/db/` | Oracle の表定義 |
| `onprem/simulator/` | 模擬データの生成と投入、動いている工場の再現 |
| `onprem/extractor/` | Oracle の差分を S3 へ送る抽出プログラムと、許可リスト |
| `onprem/tests/` | テスト(Oracle に接続できないときは、接続が必要なものだけ飛ばす) |
| `cloud/` | AWS の環境の作成と撤去 |
| `graph/` | ナレッジグラフの語彙、変換定義、問い合わせ、ツール([説明](graph/README.md)) |
| `viewer/` | 手元で動くグラフの画面 |

テストの実行(`onprem`、`graph` の各フォルダで):

```powershell
.venv\Scripts\python -m pytest tests
```

現在の状況:ステップ 3(クラウドへの吸い上げ)まで完了しました。次はステップ 4(グラフの自動更新)です。
