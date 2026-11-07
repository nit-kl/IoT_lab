# graph

工場データのナレッジグラフ。実行手順は [デモ環境準備手順](../docs/デモ環境準備手順.md) の「グラフの構築」を参照してください。

| フォルダ | 内容 |
|---|---|
| `ontology/` | 拡張語彙 `xf.ttl`、標準の語の日本語ラベル、SHACL の検証ルール `shapes.ttl` |
| `mapping/mapping.toml` | Oracle の表からグラフへの変換定義。列の意味はここだけに書く |
| `kg/` | 変換、差分の反映、グラフの読み込み。`transform.py` と `update.py` は Lambda からも使う |
| `queries/` | 問い合わせ。1 行目のコメントが問いの題名 |
| `tools/` | グラフの構築、検証、問い合わせ、ビューアのサーバー |
| `tests/` | Oracle なしで動くテスト |

## 語彙の使い分け

| 表すもの | 語 |
|---|---|
| 工場 → エリア → ライン → 設備 → 子設備 | SAREF4INMA の `Factory`、`Area`、`WorkCenter`、`ProductionEquipment`。構成はすべて `saref:consistsOf` |
| 設備の型式 | `s4inma:ProductionEquipmentCategory`、`s4inma:belongsToCategory` |
| ロット | `s4inma:MaterialBatch` |
| 製品 | `s4inma:ItemCategory` |
| 計測項目 | `sosa:ObservableProperty`、`ssn:isPropertyOf` |
| 人と役割 | `prov:Person`、`org:Role`、`xf:RoleAssignment`(`org:Membership` の下位) |
| 時刻を持つ出来事 | すべて `prov:Activity` の下位。時刻は `prov:startedAtTime`、`prov:endedAtTime` |
| コード値 | `skos:Concept`、`skos:notation` |
| 上の標準にないもの | `xf:`(`ontology/xf.ttl`) |

## 設計の要点

- **期間つきの関係はノードにします。** 部品の取付、担当、当番は開始と終了を持つノードで、「今」や「その時刻」の問いに答えられます。
- **数量を持つ関係もノードにします。** 部品構成と、指図のロット使用が該当します。
- **1 行を 1 つの名前付きグラフに入れます。** 行の更新や論理削除は、そのグラフの差し替えで反映できます。事実の出どころの行も分かります。
- **別システムの ID は 1 つのノードに統合します。** 元の値は `xf:sourceEquipmentId` で残します。
- **問題のあるデータは `xf:DataIssue` に記録します。** 必須の参照先がない行は本体を作らず隔離します。未定義コードなど、本体を残せる行は、問題を記録して続行します。
- **個人名は入れません。** 社員は社員番号で表します。
- **時系列の値は入れません。** センサー値と電力量は S3 に残し、計測項目の定義だけをグラフに入れます。
