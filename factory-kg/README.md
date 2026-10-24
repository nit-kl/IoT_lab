# factory-kg

[factory-replay](../factory-replay/) の工場データを標準オントロジー（SOSA/SSN・SAREF4INMA・QUDT・DCAT/Hydra・SKOS）で整理して
ナレッジグラフにし、各計測項目から replay の HTTP API（`/latest` `/readings`）を参照できるようにする。

- 方針・設計・懸念点: [docs/実現方針.md](docs/実現方針.md)
- 確認手順: [docs/作業手順.md](docs/作業手順.md)
- 標準語彙の使い分け: [ontology/README.md](ontology/README.md)

現在の状況: **ステップ 2（AWS 上の SPARQL エンドポイント）まで**。ステップ 3（ローカルビューア）はこれから。

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
python tools/publish_ttl.py --owner <owner>             # グラフの更新（SHACL 検証 → S3 へ公開）
python tools/publish_ttl.py --owner <owner> --rollback  # 1 つ前の版に戻す
python aws/teardown_aws.py --owner <owner>              # 削除（replay には触れない）
```

| 作られるもの | 名前 | 備考 |
|---|---|---|
| CloudFormation スタック | `xcp-factorykg-<owner>` | replay（`xcp-factory-<owner>`）とは別。タグ `Project=xcp-factorykg` |
| S3 バケット（グラフ） | `xcp-factorykg-<owner>-graph-<アカウントID>` | 公開中のグラフ `kg/current.nq`（N-Quads、名前付きグラフ入り）。バージョニングあり・古い版は 30 日で削除 |
| S3 バケット（Lambda コード用） | `xcp-factorykg-<owner>-artifacts-<アカウントID>` | `setup_aws.py` がスタック外で作成 |
| Lambda | `xcp-factorykg-<owner>-sparql` | Python 3.13・arm64・512 MB。pyoxigraph を同梱。S3 の ETag を 30 秒ごとに確認して読み直す |
| Lambda Function URL | 上記の URL | IAM 認証（SigV4、サービス名 `lambda`）。`GET/POST /sparql`（読み取りのみ）、`GET /status` |

Postman: `python tools/postman.py env --owner <owner>` で Environment を作り、`postman/xcp-factorykg.postman_collection.json` と一緒にインポートする。

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
│   ├── kgapi.py         SigV4 署名付きの呼び出し
│   └── kgcommon.py      名前空間・ファイル・名前付きグラフの共通定義
├── aws/                 CloudFormation（template.yaml）、setup / teardown、lambda/sparql.py（SPARQL エンドポイント）
├── queries/*.rq         代表的な SPARQL
├── postman/             Postman コレクション
├── examples/instances.sample.ttl   同梱サンプル CSV から生成した例（API の URL は仮）
├── tests/               生成・検証（わざと壊したグラフの検出）・検索・SPARQL Lambda のテスト
└── docs/
```

## 名前付きグラフ

| グラフ | 中身 |
|---|---|
| `https://xcp-factory.example/graph/ontology` | 手書きのスキーマ（xf.ttl・codes.ttl・labels-ja.ttl） |
| `https://xcp-factory.example/graph/imports` | 標準語彙からの抜粋（imports.ttl・units.ttl） |
| `https://xcp-factory.example/graph/instances` | 生成したインスタンス（工場〜計測項目・API 参照） |

SPARQL は 3 つの和（既定のグラフ）に対して実行する。
