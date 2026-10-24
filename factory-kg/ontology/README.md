# ontology

ナレッジグラフのスキーマ部分（手書き）と、標準語彙からの抜粋（生成）。インスタンス（工場・設備など）は
`tools/generate_ttl.py` が `build/instances.ttl` に生成する。

## 標準語彙の使い分け（懸念点 O1: 同じものを 2 通りに書かない）

| 表すもの | 使う語彙・語 | 備考 |
|---|---|---|
| 工場 → サイト → エリア → ライン | SAREF4INMA: `s4inma:Factory` → `s4inma:Site` → `s4inma:Area` → `s4inma:WorkCenter` | IEC 62264（ISA-95）の階層。ラインは WorkCenter |
| 設備 | `s4inma:ProductionEquipment`（プレス・CNC・コンベア）、`saref:Device`（コンプレッサ・環境センサ） | どちらも `sosa:FeatureOfInterest`（計測対象）でもある |
| 設備の分類 | `s4inma:belongsToCategory` → `codes.ttl` の分類（`skos:Concept`。生産設備は `s4inma:ProductionEquipmentCategory` も） | |
| 構成（親 → 子） | `saref:consistsOf` | 工場 → サイト → エリア → ライン → 設備 のすべてで同じ語を使う |
| 計測項目 | SOSA/SSN: `sosa:ObservableProperty`、`ssn:hasProperty` / `ssn:isPropertyOf` | SAREF の Property は使わない |
| 単位 | QUDT: `qudt:hasUnit` → `unit:*` | SAREF の `isMeasuredIn` は使わない |
| 値を返す API | DCAT: `dcat:DataService`、`dcat:endpointURL`。URL テンプレートは Hydra: `hydra:IriTemplate` | |
| コード値の意味 | SKOS: `skos:ConceptScheme`、`skos:notation`（API の値）、`skos:prefLabel` | |
| 来歴 | PROV-O・DCTERMS | |
| 配置（3D 表示用） | `xf:placement` → `xf:Placement`（GeoSPARQL の `geo:hasGeometry` → `geo:Geometry` の下位） | 設備・エリア・ラインに付ける。SAREF4INMA の生産設備は `geo:SpatialObject` の下位クラスなので、その形状として表す。座標はメートル（床の南西の角が原点、x は東・y は北） |
| 上の標準にないもの | `xf:`（`xf.ttl`）: `equipmentId` / `lineId` / `metricName`（API のパラメータの値）、`apiService` / `latestEndpoint` / `readingsEndpoint` / `latestTemplate` / `readingsTemplate`（API への参照）、`valueCodeList`、`Placement` / `placement` / `x` / `y` / `width` / `depth` / `height` / `rotation`（3D 表示用の配置） | 最小限にし、`rdfs:subPropertyOf`・`rdfs:domain`・`rdfs:range` で標準に紐づける |

IRI は仮ドメイン `https://xcp-factory.example/`（スキーマ `def#`、インスタンス `id/`）。変えるときは `tools/kgcommon.py` の `BASE` と
TTL の接頭辞を直して再生成する。

## ファイル

| ファイル | 内容 | 作り方 | 名前付きグラフ |
|---|---|---|---|
| `xf.ttl` | 拡張語彙 | 手書き | `graph/ontology` |
| `codes.ttl` | 稼働状態のコード表、設備の分類 | 手書き | `graph/ontology` |
| `labels-ja.ttl` | 使っている標準の語の日本語ラベル（表示用） | 手書き | `graph/ontology` |
| `layout.ttl` | サンプル工場の配置（エリア・ライン・設備の位置・大きさ・向き）。factory-app の 3D 工場が使う | 手書き | `graph/layout` |
| `shapes.ttl` | SHACL の検証ルール（設備に配置がないときは違反ではなく警告） | 手書き | （検証にだけ使い、グラフには入れない） |
| `imports.ttl` | 使っている標準の語のラベル・説明・上位クラス／上位プロパティ | `tools/vocab.py` が生成 | `graph/imports` |
| `units.ttl` | 使っている単位の QUDT 抜粋（+ 日本語ラベル） | `tools/vocab.py` が生成 | `graph/imports` |

- `imports.ttl` があるので、推論なしでも `rdfs:subClassOf*` で上位クラスから検索できる（例: `queries/04`）
- 標準の語・単位を新しく使ったら `python tools/vocab.py` を実行する。公式の定義にない IRI ならここで失敗する
- 出典・利用条件: GeoSPARQL（OGC）、SOSA/SSN・DCAT・PROV-O・SKOS（W3C）、SAREF・SAREF4INMA（ETSI）、QUDT（CC BY 4.0）、DCMI Terms（CC BY 4.0）、Hydra（W3C Community Group）
