# factory-kg

[factory-replay](../factory-replay/) の工場データを標準オントロジー（SOSA/SSN・SAREF4INMA・QUDT・DCAT/Hydra・SKOS）で整理して
ナレッジグラフにし、各計測項目から replay の HTTP API（`/latest` `/readings`）を参照できるようにする。

- 方針・設計・懸念点: [docs/実現方針.md](docs/実現方針.md)
- 確認手順: [docs/作業手順.md](docs/作業手順.md)
- 標準語彙の使い分け: [ontology/README.md](ontology/README.md)

状況: ステップ 1〜4（オントロジーと TTL・SPARQL エンドポイント・ローカルビューア・仕上げ）完了。今後の拡張の候補は [docs/実現方針.md](docs/実現方針.md) の 10 章。

## クイックスタート（ローカル、AWS 不要）

```powershell
cd factory-kg
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

python tools/generate_ttl.py --csv ../factory-replay/data/factory_sample.csv   # build/instances.ttl を生成
python tools/vocab.py                                                           # 標準語彙の IRI の実在を検査（初回は取得）
python tools/validate.py                                                        # SHACL で検証
python tools/query.py --all                                                     # 代表的な SPARQL を実行
python -m unittest discover -s tests -v                                         # テスト
```

replay の環境があるときは、`python tools/generate_ttl.py --owner <owner>` で replay の API から生成する
（TTL に書く API の URL も replay のスタックから自動で埋める）。

## AWS（SPARQL エンドポイント）

```powershell
python aws/setup_aws.py --owner <owner>                 # 作成・更新 + グラフの生成・公開（数分）
python tools/sparql.py --owner <owner> --status         # 公開中のグラフの情報
python tools/sparql.py --owner <owner> --all            # queries/*.rq を AWS 上で実行
python tools/rest.py --owner <owner> /v1/equipment/CNC-01   # REST API（構造 + replay の値。仕様は aws/openapi.yaml）
python tools/publish_ttl.py --owner <owner>             # グラフの更新（SHACL 検証 → S3 へ公開）
python tools/publish_ttl.py --owner <owner> --rollback  # 1 つ前の版に戻す
python aws/teardown_aws.py --owner <owner>              # 削除（replay には触れない）
```

| 作られるもの | 名前 | 備考 |
|---|---|---|
| CloudFormation スタック | `xcp-factorykg-<owner>` | replay（`xcp-factory-<owner>`）とは別。タグ `Project=xcp-factorykg` |
| S3 バケット（グラフ） | `xcp-factorykg-<owner>-graph-<アカウントID>` | 公開中のグラフ `kg/current.nq`（N-Quads、名前付きグラフ入り）。バージョニングあり・古い版は 30 日で削除 |
| S3 バケット（Lambda コード用） | `xcp-factorykg-<owner>-artifacts-<アカウントID>` | `setup_aws.py` がスタック外で作成 |
| Lambda | `xcp-factorykg-<owner>-sparql` | SPARQL エンドポイント。Python 3.13・arm64・512 MB。pyoxigraph を同梱。S3 の ETag を 30 秒ごとに確認して読み直す |
| Lambda | `xcp-factorykg-<owner>-api` | REST API（`/v1/…`、仕様は `aws/openapi.yaml`）。グラフを同じ方法で読み込み、値はグラフに書かれた replay の API（`xcp-factory-<owner>-api` だけ）を呼ぶ |
| Lambda Function URL | 上記それぞれ | IAM 認証（SigV4、サービス名 `lambda`）。SPARQL は `GET/POST /sparql`（読み取りのみ）・`GET /status`、REST は `GET /v1/…` |

Postman: `python tools/postman.py env --owner <owner>` で Environment を作り、`postman/xcp-factorykg.postman_collection.json`（SPARQL）・
`postman/xcp-factorykg-rest.postman_collection.json`（REST）と一緒にインポートする。

## REST API

F/E アプリや MCP サーバー向けの窓口。SPARQL を見せずに、構造（グラフ）と値（replay）を 1 つの JSON で返す。

| エンドポイント | 内容 |
|---|---|
| `GET /v1/factory` | 工場 → サイト → エリア → ライン → 設備 の木 |
| `GET /v1/equipment?line=&category=` | 設備の一覧（稼働状態つき） |
| `GET /v1/equipment/{id}` | 設備の詳細（計測項目と最新値） |
| `GET /v1/equipment/{id}/properties/{metric}` | 計測項目（単位・説明・コード表・取得 URL）と最新値 |
| `GET /v1/equipment/{id}/properties/{metric}/readings` | 時系列（`minutes` / `source_from`・`source_to`、`limit`・`next`） |
| `GET /v1/equipment/{id}/properties/{metric}/readings/summary` | 時系列の要約（最小・最大・平均、状態ごとの時間と変化の時刻） |
| `GET /v1/codes/{key}`・`/v1/search?q=`・`/v1/status` | コード表・検索・状態 |

コード値は意味を添えて返し（`{"value": 2, "label": "異常停止"}`）、replay が無いときも構造は返す（値は `{"unavailable": "理由"}`）。

## ローカルビューア

```powershell
python tools/kg_viewer.py --owner <owner>      # http://127.0.0.1:8765/ が開く。終わったら Ctrl+C
```

- 工場 → サイト → エリア → ライン → 設備 → 計測項目 のグラフ。設備の縁の色は稼働状態（灰 = 停止、緑 = 稼働、赤 = 異常停止）
- ノードを選ぶと、日本語名・標準語彙のクラス・関係（どの名前付きグラフの記述か）を表示。計測項目は、グラフに書かれた
  replay の API から最新値と時系列を取って表示する
- 検索・ライン／分類の絞り込み・SPARQL の直接実行。`#q=CNC-01` のような URL で、その設備を選んだ状態で開ける
- 自動更新（1 分ごと）は画面を表示している間だけ。開きっぱなしにすると replay の DB が止まらないので、使い終わったら閉じて Ctrl+C
- 仕組み: この PC だけで動く小さなサーバー（127.0.0.1）が画面を配信し、aws login の認証情報で KG・replay に署名して中継する。
  中継するのは KG の SPARQL と、グラフに書かれた replay の API（latest / readings / status）だけ

## ファイル構成

```
factory-kg/
├── ontology/            手書きのスキーマ（xf.ttl / codes.ttl / labels-ja.ttl / shapes.ttl）と標準語彙の抜粋（imports.ttl / units.ttl）
├── tools/
│   ├── generate_ttl.py  インスタンス TTL の生成（replay の API か CSV から。カタログ = 設備の種類・計測項目・単位の対応表）
│   ├── vocab.py         標準語彙の取得・使っている IRI の実在検査・imports.ttl / units.ttl の書き出し
│   ├── validate.py      SHACL 検証
│   ├── query.py         Oxigraph（メモリ上）で SPARQL を実行（ローカル）
│   ├── publish_ttl.py   SHACL 検証 → N-Quads にまとめて S3 へ公開（--rollback で 1 つ前の版へ）
│   ├── sparql.py        AWS 上の SPARQL エンドポイントに問い合わせる CLI
│   ├── postman.py       Postman のコレクション（queries から生成）・Environment を作る
│   ├── bench_load.py    グラフの大きさごとの読み込み時間を測る（ローカル / Lambda）
│   ├── kg_viewer.py     ローカルビューアのサーバー（画面の配信と、署名付きの中継）
│   ├── rest.py          REST API を呼ぶ CLI
│   ├── kgapi.py         SigV4 署名付きの呼び出し
│   └── kgcommon.py      名前空間・ファイル・名前付きグラフの共通定義
├── aws/                 CloudFormation（template.yaml）、setup / teardown、openapi.yaml（REST API の仕様）、
│                        lambda/（sparql.py・api.py・domain.py＝REST のドメイン層・graphstore.py＝グラフの読み込み）
├── queries/*.rq         代表的な SPARQL
├── postman/             Postman コレクション
├── viewer/              ローカルビューアの画面（index.html / app.js / style.css、画面が使う SPARQL は viewer/queries/）
├── examples/instances.sample.ttl   同梱サンプル CSV から生成した例（API の URL は仮）
├── tests/               生成・検証（わざと壊したグラフの検出）・検索・SPARQL Lambda・ビューア（中継の制限など）のテスト
└── docs/
```

## 名前付きグラフ

| グラフ | 中身 |
|---|---|
| `https://xcp-factory.example/graph/ontology` | 手書きのスキーマ（xf.ttl・codes.ttl・labels-ja.ttl） |
| `https://xcp-factory.example/graph/imports` | 標準語彙からの抜粋（imports.ttl・units.ttl） |
| `https://xcp-factory.example/graph/instances` | 生成したインスタンス（工場〜計測項目・API 参照） |

SPARQL は 3 つの和（既定のグラフ）に対して実行する。
