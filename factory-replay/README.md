# factory-replay

工場ラインのサンプルデータ（CSV）を S3 にアップロードすると RDB に少しずつ反映され、
HTTP API で取得・リセットできる検証用キット。方針は [docs/実現方針.md](../docs/実現方針.md) を参照。

## AWS 環境の作成・削除

前提: Python 3.10 以上、AWS CLI でログイン済み（`aws login` など）、東京リージョン。
ログインから作成物の確認・削除までの詳しい手順は [docs/作業手順.md](../docs/作業手順.md) を参照。

```
python -m venv .venv                          # 初回のみ
.\.venv\Scripts\Activate.ps1                  # 毎回（PowerShell）
pip install -r requirements.txt               # 初回のみ
python aws/setup_aws.py --owner kojima        # 作成（初回 10〜15 分）。再実行すると更新
python aws/teardown_aws.py --owner kojima     # すべて削除（DB・S3 のデータも消える）
```

`--owner` は英小文字で始まる英小文字・数字 2〜16 文字。共有アカウントで他の人と衝突しないよう、
すべてのリソース名が `xcp-factory-<owner>` から作られる。

| 作られるもの | 名前 | 備考 |
|---|---|---|
| CloudFormation スタック | `xcp-factory-<owner>` | タグ `Project=xcp-factory`, `Owner=<owner>` |
| VPC（サブネット 2、S3 ゲートウェイエンドポイント） | `xcp-factory-<owner>` | インターネット出口なし。固定費なし |
| S3 バケット | `xcp-factory-<owner>-data-<アカウントID>` | CSV は `incoming/` に置く |
| Aurora Serverless v2（PostgreSQL） | `xcp-factory-<owner>` | 0〜4 ACU、5 分無接続で一時停止。Data API 有効 |
| DB 認証情報 | RDS 管理シークレット | パスワードは自動生成・自動ローテーション |
| Lambda | `xcp-factory-<owner>-loader` / `-replayer` | Python 3.13・arm64。ログは 14 日保持 |
| EventBridge Scheduler | `xcp-factory-<owner>-replayer` | 1 分ごとに replayer を起動。リプレイ中だけ ENABLED |
| S3 バケット（Lambda コード用） | `xcp-factory-<owner>-artifacts-<アカウントID>` | スタックより先に必要なため `setup_aws.py` がスタック外で作成。`teardown_aws.py` で削除 |

## ② CSV のアップロード → 少しずつ反映

```
aws s3 cp data/factory_sample.csv s3://xcp-factory-<owner>-data-<アカウントID>/incoming/
```

1. `incoming/*.csv` が置かれると **loader** が起動し、DB の `start_replay()` を呼ぶ。
   DB が S3 から CSV を直接 `staging_readings` に取り込み、`readings` を空にして `replay_state.status = running` にする
   （CSV の中身は Lambda を通らないので、数百 MB の CSV でもよい）
2. **replayer** が 1 分ごとに起動し、「開始からの経過時間 × 速度（既定 60 倍）」ぶんの行を `readings` に写す
3. 全行写し終えると `status = done` になり、スケジュールが止まる → DB は 5 分後に一時停止

- 取り込みに失敗すると `status = error` になり、`message` に理由が入る（直前の `readings` はそのまま）
- 新しい CSV を置くと、その時点で最初からやり直しになる
- 確認手順は [docs/作業手順.md](../docs/作業手順.md) の「4. ステップ 3」

- テーブル定義は `aws/schema.sql`。`setup_aws.py` が Data API で作成する（何度実行しても同じ結果）
- 使っていない間の費用は Aurora のストレージ代（1 GB 未満なら月 1 ドル未満）とシークレット代（月 $0.40）程度。
  検証が終わったら `teardown_aws.py` で削除する
- VPC はリージョンあたり既定 5 個までのため、同じアカウントで多人数が作る場合は上限緩和が必要になることがある
- 主なオプション: `--max-acu`（1/2/4/8、既定 4）、`--auto-pause`（秒、既定 300）、`--vpc-cidr`（既定 10.80.0.0/16）

## ① サンプル CSV

`data/factory_sample.csv` に 1 日分（2026-10-01、1 分間隔、21,600 行）を同梱している。
作り直す・大きいデータを作る場合は次のとおり（Python 3.10 以上、外部ライブラリ不要）。

```
python tools/generate_csv.py                                             # 同梱サンプルを再生成
python tools/generate_csv.py --days 7 --out data/week.large.csv          # 1 週間分
python tools/generate_csv.py --days 30 --interval 10s --out data/month.large.csv
python tools/generate_csv.py --lines 3 --out data/3lines.large.csv       # LINE-A〜C の 3 ライン
```

`*.large.csv` は Git 管理外。同じ引数（`--seed` 含む）なら毎回同じ内容になる。

### 列

| 列 | 内容 |
|---|---|
| `ts` | 計測時刻（ISO 8601、JST） |
| `line_id` | ライン（`LINE-A` …） |
| `equipment_id` | 設備（末尾の番号はライン番号） |
| `metric` / `value` / `unit` | 計測項目・値・単位 |

### 設備とメトリクス

| 設備 | metric | 単位 | 備考 |
|---|---|---|---|
| PRESS-xx プレス機 | `status` | | 0=停止 1=稼働 2=異常停止 |
| | `shot_count` | count | その間隔内のショット数（稼働中 約 20/分） |
| | `hydraulic_pressure` | MPa | |
| | `motor_current` | A | |
| CNC-xx 加工機 | `status` | | 同上 |
| | `spindle_speed` | rpm | |
| | `spindle_load` | % | |
| | `work_count` | count | その間隔内の加工完了数 |
| CONV-xx コンベア | `speed` | m/min | |
| | `motor_temp` | degC | |
| COMP-xx コンプレッサ | `discharge_pressure` | MPa | |
| | `power` | kW | |
| ENV-xx 環境センサ | `temperature` / `humidity` / `co2` | degC / % / ppm | |

### 仕込んであるイベント（1 日目は必ず発生、2 日目以降は確率で発生）

| イベント | 1 日目の時刻 | 見え方 |
|---|---|---|
| 昼休み | 12:00〜13:00 | 全設備 `status=0`（毎日） |
| エア漏れ | 10:00〜11:09 | COMP の `discharge_pressure` が 0.70 → 0.58 MPa まで徐々に低下、`power` が増加 |
| CNC チョコ停 | 14:00〜14:38 | `spindle_load` が 55 → 90% 超に上昇（〜14:30）した後、`status=2` が 8 分続く |
| 室温上昇 | 14:00〜16:30 | ENV の `temperature` が 30 ℃ を超える |
