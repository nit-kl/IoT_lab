# factory-app

工場のナレッジグラフ（[factory-kg](../factory-kg/)）と工場データ（[factory-replay](../factory-replay/)）を使う、3 つの画面を持つローカルアプリ。

1. ① ナレッジグラフ（工場 → ライン → 設備 → 計測項目、値・時系列・グラフ上の関係） … **実装済み**
2. ② データを基にした工場の 3D ビューア（配置はナレッジグラフ、信号灯・設備の動きは最新値に連動） … **実装済み**
3. ③ MCP を利用した AI エージェント（Bedrock、費用の上限つき） … ステップ 3 で実装

- 設計・方針・費用・懸念点: [docs/実現方針.md](docs/実現方針.md)
- 動かし方・確認方法: [docs/作業手順.md](docs/作業手順.md)

## すぐ試す（AWS なし・費用 0 円）

```powershell
cd factory-app\web; npm install; npm run build; cd ..
..\factory-kg\.venv\Scripts\python.exe server\app_server.py --demo      # http://127.0.0.1:8770/
```

AWS の KG・replay につなぐときは `--demo` の代わりに `--owner <owner>`（事前に `aws login`）。

## 構成

| パス | 内容 |
|---|---|
| `server/app_server.py` | 画面（`web/dist`）の配信と、KG の REST API・SPARQL への中継（aws login の認証情報で署名）。127.0.0.1 のみで待ち受け、Host と独自ヘッダーを確認 |
| `server/demo.py` | デモモード: 手元のグラフ（Oxigraph）+ factory-kg の REST の処理 + サンプル CSV を再生する偽の replay |
| `web/` | 画面（Vite + React + TypeScript + Tailwind、Zustand、Cytoscape.js、ECharts、Three.js / React Three Fiber） |
| `web/src/kg/` | ① ナレッジグラフ |
| `web/src/factory3d/` | ② 3D 工場（React Three Fiber）。`model.ts` が配置と最新値から表示用のモデルを作り、`Building.tsx`（建屋）・`Machines.tsx`（設備・信号灯）が描く |
| `web/src/components/` | ヘッダー・詳細欄（設備・計測項目・グラフ上の関係）・時系列グラフ・SPARQL 欄 |
| `tests/` | サーバー・デモモードのテスト（`python -m unittest discover -s tests`） |
| `docs/` | 実現方針・作業手順 |
