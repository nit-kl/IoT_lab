# factory-kg

[factory-replay](../factory-replay/) の工場データを標準オントロジー（SOSA/SSN・SAREF4INMA・QUDT・DCAT/Hydra・SKOS）で整理して
ナレッジグラフにし、各計測項目から replay の HTTP API（`/latest` `/readings`）を参照できるようにする。

- 方針・設計・懸念点: [docs/実現方針.md](docs/実現方針.md)
- 確認手順: [docs/作業手順.md](docs/作業手順.md)
- 標準語彙の使い分け: [ontology/README.md](ontology/README.md)

現在の状況: **ステップ 1（オントロジーと TTL の生成。ローカルで完結）まで**。ステップ 2 以降（S3 + Lambda の SPARQL、ローカルビューア）はこれから。

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

## ファイル構成

```
factory-kg/
├── ontology/            手書きのスキーマ（xf.ttl / codes.ttl / labels-ja.ttl / shapes.ttl）と標準語彙の抜粋（imports.ttl / units.ttl）
├── tools/
│   ├── generate_ttl.py  インスタンス TTL の生成（replay の API か CSV から。カタログ = 設備の種類・計測項目・単位の対応表）
│   ├── vocab.py         標準語彙の取得・使っている IRI の実在検査・imports.ttl / units.ttl の書き出し
│   ├── validate.py      SHACL 検証
│   ├── query.py         Oxigraph（メモリ上）で SPARQL を実行
│   └── kgcommon.py      名前空間・ファイル・名前付きグラフの共通定義
├── queries/*.rq         代表的な SPARQL
├── examples/instances.sample.ttl   同梱サンプル CSV から生成した例（API の URL は仮）
├── tests/test_kg.py     生成・検証（わざと壊したグラフの検出）・検索のテスト
└── docs/
```

## 名前付きグラフ

| グラフ | 中身 |
|---|---|
| `https://xcp-factory.example/graph/ontology` | 手書きのスキーマ（xf.ttl・codes.ttl・labels-ja.ttl） |
| `https://xcp-factory.example/graph/imports` | 標準語彙からの抜粋（imports.ttl・units.ttl） |
| `https://xcp-factory.example/graph/instances` | 生成したインスタンス（工場〜計測項目・API 参照） |

SPARQL は 3 つの和（既定のグラフ）に対して実行する。
