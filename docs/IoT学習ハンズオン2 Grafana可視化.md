# IoT学習ハンズオン2:Grafana可視化

Oct 15, 2026 · @Reo Kojima

前提:[IoT学習ハンズオン PC+シミュレータ+AWS](./IoT学習ハンズオン%20PC%2Bシミュレータ%2BAWS.md)のステップ1〜4が終わっていて、Athenaの `telemetry_raw` にデータがあることです。所要時間は30〜60分です。Grafana自体はPC上で動くローカルのDockerコンテナなので無料ですが、ダッシュボードを開いている間はAthenaへのクエリとS3の読み取りに費用がかかります。学習で1時間開く程度なら百円前後です。ただし**データが1日分たまった状態で開きっぱなしにすると1時間で千円を超える**ので、使い終わったらタブを閉じてください(内訳は[アーキテクチャとコスト](./アーキテクチャとコスト.md))。

## 全体像

前回はAthenaのSQLでデータを見ました。今回はその手前にローカルDockerで動かすGrafanaを置き、ショーケースの庫内温度・売場の室温・店舗の電力をブラウザでグラフとして見られるようにします。店舗の設備担当や本部の店舗運営部が毎日見る「店舗設備の画面」の最小版です。Grafana自体はAWSに作りません。AWS側に追加するのは「Grafanaが Athena を読むためだけの、最小権限のIAMユーザー」1つだけです。

| 追加するもの | 役割 | 学べること |
| --- | --- | --- |
| aws/template.yaml の GrafanaUser / GrafanaUserPolicy | Athena・Glue・S3を読む専用IAMユーザー(xcp-store-iot-owner-grafana) | 「ダッシュボード用の読み取り専用クレデンシャル」という最小権限の考え方 |
| grafana/docker-compose.yml | ローカルDockerでGrafana本体を起動 | コンテナでのツール導入 |
| grafana/provisioning/ | データソースとダッシュボードを起動時に自動設定 | Infrastructure as Code の考え方をGrafanaにも適用する |
| grafana/dashboards/store_overview.json | 庫内温度・室温・電力・件数・ステータス・遅延を見る6パネルのダッシュボード | 運用監視でよく使う3種のパネル(時系列・Stat・テーブル) |

ゲートウェイのX.509証明書と同じ理由で、GrafanaのAWSアクセスキーも「漏れたらなりすまされる長期クレデンシャル」です。秘密鍵と同じ扱いで、ローカル保存・Git管理外にします。

## 準備(15〜30分)

1. **Docker Desktop**を入れます(Windows・Mac・Linuxどれでも可)。インストール後、PowerShellで `docker --version` を打ってバージョンが出ればOKです。
2. 前回のAWS環境がまだなければ、先に手順書1のステップ3まで終わらせておきます(`python aws/setup_aws.py` 済みであること)。
3. iot-labフォルダで、もう一度 `setup_aws.py` を実行します。スタックは更新されるだけで、既に作ったものは壊れません。

   ```
   python aws/setup_aws.py
   ```

   `[4/4] Grafana用アクセスキーを発行中…` と表示され、`grafana/.env` が新しく作られます。中にAWSのアクセスキーIDとシークレットキー、接続先のAthenaワークグループとデータベースの名前が入っています。既に `grafana/.env` がある場合は再利用され、何も変わりません。

## ステップ1:Grafanaを起動する

1. `grafana` フォルダに移動してコンテナを起動します(初回はAthena用プラグインのダウンロードがあるため1〜2分かかります)。

   ```
   cd grafana
   docker compose up -d
   ```
2. ブラウザで `http://localhost:3000` を開き、ユーザー名 `admin` / パスワード `admin` でログインします。初回はパスワード変更を求められるので、変更するか一旦スキップします(ローカルの学習用なのでスキップでも問題ありません)。

## ステップ2:プロビジョニングされたデータソースを確認する

左メニューの **Connections → Data sources** を開くと、「Athena (store-iot)」が既に登録されています。手で入力した項目は何もありません。これは `grafana/provisioning/datasources/athena.yaml` が起動時に読み込まれるためです。

- `workgroup` / `database` は前回作ったAthenaワークグループ(`xcp-store-iot-owner`)とデータベース(`xcp_store_iot_owner`)を指しています。名前に owner が入って人ごとに違うので、YAMLには `$__env{ATHENA_WORKGROUP}` のように環境変数で書き、値は `grafana/.env` から渡しています。
- アクセスキーも `$__env{AWS_ACCESS_KEY_ID}` のように環境変数から読み込む形で書かれていて、YAMLファイル自体には平文で入っていません。実体は `docker-compose.yml` の `env_file: .env` で渡される `grafana/.env` です。

データソース画面右下の **Save & test** を押し、「Datasource is working」と出ることを確認します。失敗する場合はトラブル表を見てください。

## ステップ3:ダッシュボードの6パネルを読む

左メニューの **Dashboards** を開くと、「Store IoT」フォルダに **Store Overview(店舗設備)** があります。開くと6つのパネルが並びます。

| パネル | 種類 | 元にしたクエリ | 見るもの |
| --- | --- | --- | --- |
| 冷蔵ショーケース 庫内温度(RC-01) | 時系列 | queries.sql 2番 | 5℃前後で安定し、8分ごとの霜取りで一時的に上がる |
| 売場の室温(AC-01) | 時系列 | (同系) | 設定温度と室温。空調が止まる2分間は室温が上がる |
| 店舗の電力(PM-01) | 時系列 | (同系) | 空調が止まると約12kW下がる。電力の大半は空調と冷凍・冷蔵設備 |
| 本日の受信件数 | Stat(数字1つ) | queries.sql 1番相当 | ざっくり動いているかの確認 |
| 機器×ステータス×アラーム件数 | テーブル | queries.sql 7番 | comm_errorやアラームが増えていないか |
| 送信遅延 上位10件 | テーブル | queries.sql 4番 | 回線断からの復旧直後に遅延が大きい行が出る |

**押さえておきたい注意点**:どのパネルも `WHERE dt = date_format(current_date, '%Y-%m-%d')` のように**当日固定**でクエリを書いています。そのため、画面右上の時刻ピッカー(Last 6 hoursなど)を変えても表示は変わりません。これは前回の手順書で触れた「Athenaは読んだデータ量で課金される」という注意をGrafanaでも徹底するためで、バグではありません。昨日のデータを見たい場合は、パネルを編集してSQLの `current_date` の部分を書き換えます。

**欠損チェック(queries.sql 5番)と重複チェック(6番)をダッシュボードに入れていない理由**:ダッシュボードは「今どうなっているか」をひと目で見る運用監視向けで、欠損・重複チェックのような原因調査(フォレンジック)はAthenaで都度SQLを書いて掘る方が向いています。両方を1つのダッシュボードに詰め込むと、どちらの目的にも中途半端になります。

**グラフの読み方の例**:庫内温度のグラフに8分おきに出る山は霜取りです。霜取りの山と、扉の開けっ放しによる上昇(ハンズオン4で試します)は、グラフの形が違います。霜取りは決まった間隔で上がってすぐ戻り、扉の開けっ放しはゆっくり上がり続けます。設備担当者はこうした形の違いで原因の見当を付けます。

**やってみる**:「送信遅延 上位10件」パネルを編集(パネル右上のメニュー→Edit)し、SQLの `LIMIT 10` を `LIMIT 3` に変えて保存し、表示件数が変わることを確認してから元に戻します。クエリを直接書けることがAthenaデータソースの特徴です。

## 片付け

学習が一区切りしたら、まずAWS側を片付けます。

```
python aws/teardown_aws.py
```

これでGrafana用のアクセスキーが削除されてからスタックが削除され、ローカルの `grafana/.env` も消えます(証明書やgateway_config.jsonの削除と同じタイミングです)。

Grafanaのコンテナ自体(ローカルPC上のDocker)は別に片付けます。

```
cd grafana
docker compose down -v
```

コンテナを止めずに残しておく場合も、ダッシュボードを開いたブラウザのタブは閉じておきます。ダッシュボードは30秒ごとに自動更新され、そのたびにAthenaが当日の全ファイルをS3から読むため、開いているだけで費用がかかります(右上の自動更新を「Off」にしても止まります)。

`-v` を付けるとGrafana内部の設定(パスワード変更など)も含めて消えます。再開するときは `setup_aws.py` → `docker compose up -d` をもう一度実行するだけです。

## よくあるつまずき

| 症状 | 原因と対処 |
| --- | --- |
| `docker compose up -d` が `docker: command not found` | Docker Desktopが起動していない、またはインストールされていない |
| 初回起動が遅い・プラグインが入らない | `GF_INSTALL_PLUGINS` でAthenaプラグインを毎回ダウンロードしています。回線が遅いと数分かかります。`docker compose logs grafana` でダウンロード状況を確認 |
| データソースのSave & testで失敗 | `grafana/.env` の中身を確認(`setup_aws.py` を実行し忘れていないか)。中身を直したら `docker compose restart grafana` |
| 「workgroup is not found」などのエラー | 古い `grafana/.env` に ATHENA_WORKGROUP / ATHENA_DATABASE の行が無い。`grafana/.env` を消して `setup_aws.py` を再実行し、`docker compose up -d --force-recreate` |
| データソースはOKだがパネルが「No data」 | その日まだゲートウェイを動かしていない可能性。`python gateway/gateway.py` を動かしてAthenaにデータ(queries.sqlの1番)が見えるか先に確認 |
| 時刻ピッカーを変えても変わらない | 仕様です(ステップ3の注意点を参照)。パネル編集でSQLの `current_date` を書き換えます |
| teardown_aws.py でIAMユーザーが消せないと怒られる | アクセスキーが残っている可能性。`aws iam list-access-keys --user-name <GrafanaUserNameの値>` で確認し、手動で `aws iam delete-access-key` してから再実行 |

## 次のステップ

- [x] IoTルールにLambdaを追加し、機器ごとの最新値をDynamoDBに保存する → [IoT学習ハンズオン3](./IoT学習ハンズオン3%20Lambda%2BDynamoDB最新値.md)
- [ ] 時間帯別の来店客数と売上をダッシュボードに足す(ハンズオン6・7で作成予定)
- [ ] 多店舗を1つのダッシュボードで切り替えて見る(Grafanaの変数で店舗を選ぶ)
