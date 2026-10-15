# IoT_lab

IoT学習キット(`iot-lab/`)と、その手順書(`docs/`)のリポジトリ。

- 題材はスーパー1店舗の設備(売場空調 AC-01・冷蔵ショーケース RC-01・電力計 PM-01・冷凍機盤 REF-01)。XCPチームが共用のAWSアカウントで検証する前提で、AWSの名前は `xcp-store-iot-<owner>-…`、MQTTトピックは `store-<owner>/…` にする(`iot-lab/aws/naming.py` と template.yaml)
- `iot-lab/` は学習者に zip で配るキット本体。`tools/` と `.github/` はドキュメント保守用で、キットには含めない
- 手順書は `docs/IoT学習ハンズオンN <テーマ>.md`。既存の手順書の文体(です・ます、表で整理、「やってみる」「よくあるつまずき」「次のステップ」)に合わせる
- Lambda のコードは `iot-lab/aws/lambda/*.py` が正本。`setup_aws.py` が template.yaml の `# __CODE:<名前>__` の行に埋め込む(1ファイル4096文字まで)
- テキストファイルの改行は LF(`docs/MQTT.md` と `docs/Modbus.md` だけは元から CRLF)

## アーキテクチャとコストのドキュメントを追従させる

ハンズオンの追加・変更で次のどれかが変わったら、同じ変更の中で `docs/アーキテクチャとコスト.md` を更新する。

- `iot-lab/aws/template.yaml` のリソース
- 手順書の追加・改名
- メッセージ数、Lambda の実行回数、DynamoDB の読み書きなど、課金される件数

手順はそのドキュメント末尾の「このドキュメントの更新のしかた」に従う。要点は次の3つ。

1. ハンズオン一覧・全体構成図(mermaid)・AWSリソース一覧を手で更新する
2. 件数の変化は `tools/docs_sync.py` の `usage()` に反映し、`python tools/docs_sync.py` でコスト表を再生成する(コスト表の中は手で編集しない)
3. 最後に `python tools/docs_sync.py --check` が OK になることを確認する(CI でも同じ検査が走る)

各手順書の「費用」節に金額を書くときは、`docs/アーキテクチャとコスト.md` の計算結果と矛盾しないようにする。
