"""工場ナレッジグラフの MCP サーバーの中身（ツール・リソース）。ローカル（stdio）とリモート（Streamable HTTP）で共通。

ツールは REST API（/v1/…）を呼ぶだけの薄い層で、処理の本体は REST 側（aws/lambda/domain.py）にある。
REST の呼び方は backend として外から渡す（11.3 リモート化を見据えた設計）:

  - ローカル: tools/kg_mcp.py の LocalBackend（aws login の認証情報で SigV4 署名して REST を呼ぶ）
  - リモート（将来）: api Lambda の中で api.dispatch() を直接呼ぶ backend

サーバーは状態を持たない（ツールの呼び出しごとに REST を呼ぶだけ）。
"""

import json
import re
from typing import Annotated, Any, Protocol

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

SPARQL_MAX_ROWS = 200
SPARQL_MAX_TEXT = 300
READINGS_MAX_DETAIL = 1000

INSTRUCTIONS = """\
工場ナレッジグラフ（サンプル工場の 1 ライン・設備 5 台・計測項目 15 個）と、その計測値（工場 CSV リプレイ基盤）に問い合わせるツール群。

使い方の目安:
- 何があるか知りたい → list_lines / list_equipment / search
- 設備の今の状態・値 → get_equipment（計測項目ごとの最新値と稼働状態）
- ある時間帯の様子（いつ止まったか、最大はいつか）→ get_readings（既定は要約。細かい値が必要なときだけ detail=true）
- 単位やコードの意味 → get_property、リソース factory-kg://codes

時刻の注意:
- source_ts は CSV 上の時刻（工場で計測した時刻、例 2026-10-01T14:30）。ユーザーが「14 時台」「昨日の昼」などと言うときは、
  基本的にこちら（get_readings の source_from / source_to）で絞り込む
- ts は replay がその値を反映した時刻（リプレイの実行時刻）。minutes はこちらで直近 N 分
- 時刻はすべて日本時間（+09:00）

稼働状態（metric = status）は 0=停止 1=稼働 2=異常停止。値は {"value": 2, "label": "異常停止"} のように意味つきで返る。
"""

ONTOLOGY_GUIDE = """\
# 工場ナレッジグラフの構造（標準オントロジーの使い分け）

| 表すもの | 標準の語 |
|---|---|
| 工場 → サイト → エリア → ライン → 設備 | SAREF4INMA: s4inma:Factory → s4inma:Site → s4inma:Area → s4inma:WorkCenter（ライン）→ 設備。親子は saref:consistsOf |
| 設備 | s4inma:ProductionEquipment（プレス機・CNC 加工機・搬送コンベア）、saref:Device（エアコンプレッサ・環境センサ）。どちらも sosa:FeatureOfInterest |
| 設備の分類 | s4inma:belongsToCategory → skos:Concept（プレス機・CNC 加工機 など） |
| 計測項目 | sosa:ObservableProperty（ssn:isPropertyOf で設備に結びつく）。xf:metricName が API の metric の値 |
| 単位 | qudt:hasUnit → unit:PERCENT・unit:MegaPA・unit:DEG_C など |
| 値を返す API | dcat:DataService（dcat:endpointURL）、URL テンプレートは hydra:IriTemplate。計測項目ごとに xf:latestEndpoint・xf:readingsEndpoint |
| コードの意味 | skos:ConceptScheme（稼働状態: 0 停止 / 1 稼働 / 2 異常停止） |

接頭辞: xf = https://xcp-factory.example/def#、id = https://xcp-factory.example/id/、
s4inma = https://saref.etsi.org/saref4inma/、saref = https://saref.etsi.org/core/、sosa = http://www.w3.org/ns/sosa/、
ssn = http://www.w3.org/ns/ssn/、qudt = http://qudt.org/schema/qudt/、unit = http://qudt.org/vocab/unit/、
dcat = http://www.w3.org/ns/dcat#、hydra = http://www.w3.org/ns/hydra/core#、skos = http://www.w3.org/2004/02/skos/core#

"""


class BackendError(ToolError):
    """REST・SPARQL の呼び出しが失敗した。ToolError なので、メッセージ（例: 設備 NOPE-01 はありません）がエージェントに届く
    （ほかの例外は SDK が「予期しないエラー」として中身を隠す）。"""


class Backend(Protocol):
    def rest(self, path: str, params: dict | None = None) -> dict: ...

    def sparql(self, query: str) -> dict: ...


# ---------- 応答を小さくする（エージェントの文脈を圧迫しない。懸念点 R5） ----------

def _latest(latest: dict | None) -> dict | None:
    if not latest:
        return None
    if "unavailable" in latest:
        return {"unavailable": latest["unavailable"]}
    return {"value": latest["value"], "source_ts": latest["source_ts"]}


def _unit(unit: dict) -> str:
    return unit.get("symbol") or unit.get("label") or ""


def _equipment(e: dict) -> dict:
    out = {"id": e["id"], "label": e.get("label"), "category": (e.get("category") or {}).get("label"),
           "line": e.get("line"), "metrics": e.get("metrics")}
    if "status" in e:
        out["status"] = _latest(e["status"])
    return out


def build_server(backend: Backend, enable_sparql: bool = True) -> MCPServer:
    """MCP サーバーを作る。enable_sparql はローカル版では True、リモート版では既定で False（11.3）。"""
    server = MCPServer(name="factory-kg", title="工場ナレッジグラフ", instructions=INSTRUCTIONS, version="1.0.0")
    read_only = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

    def call(path: str, params: dict | None = None) -> dict:
        return backend.rest(path, {k: v for k, v in (params or {}).items() if v not in (None, "")})

    @server.tool(annotations=read_only, description=(
        "工場のライン（ワークセンタ）と、各ラインの設備 ID の一覧を返す。工場全体の構成を知りたいときに最初に使う。"))
    def list_lines() -> dict:
        tree = call("/v1/factory")
        lines = []

        def walk(node):
            if node["kind"] == "line":
                lines.append({"id": node["id"], "label": node["label"],
                              "equipment": [{"id": c["id"], "label": c["label"], "category": c.get("category")}
                                            for c in node.get("children", [])]})
            for child in node.get("children", []):
                walk(child)
        for factory in tree["factories"]:
            walk(factory)
        return {"lines": lines}

    @server.tool(annotations=read_only, description=(
        "設備の一覧を返す（ID・名前・分類・ライン・計測項目名、稼働状態を持つ設備は今の稼働状態）。"
        "ラインや分類（例: 'cnc'、'CNC 加工機'、'プレス機'）で絞り込める。"))
    def list_equipment(
        line: Annotated[str | None, Field(description="ライン ID（例: LINE-A）。省略するとすべて")] = None,
        category: Annotated[str | None, Field(description="設備の分類（例: cnc、プレス機）。省略するとすべて")] = None,
    ) -> dict:
        body = call("/v1/equipment", {"line": line, "category": category})
        return {"equipment": [_equipment(e) for e in body["items"]], "count": body["count"]}

    @server.tool(annotations=read_only, description=(
        "設備 1 台の詳細を返す: 分類、標準オントロジーのクラス、稼働状態、計測項目ごとの最新値（単位つき）と CSV 上の時刻。"
        "「CNC-01 の今の状態は？」のような質問に使う。"))
    def get_equipment(
        equipment_id: Annotated[str, Field(description="設備 ID（例: CNC-01、PRESS-01、ENV-01）")],
    ) -> dict:
        e = call(f"/v1/equipment/{equipment_id}")
        out = _equipment(e)
        out["types"] = e.get("types")
        out["properties"] = [{"metric": p["metric"], "label": p["label"], "unit": _unit(p["unit"]),
                              "latest": _latest(p.get("latest"))} for p in e["properties"]]
        return out

    @server.tool(annotations=read_only, description=(
        "計測項目 1 つの詳細を返す: 名前・説明・単位（QUDT）・コード表（稼働状態なら 0/1/2 の意味）・最新値・値を取る API の URL。"))
    def get_property(
        equipment_id: Annotated[str, Field(description="設備 ID（例: CNC-01）")],
        metric: Annotated[str, Field(description="計測項目名（例: spindle_load、status、temperature）")],
    ) -> dict:
        p = call(f"/v1/equipment/{equipment_id}/properties/{metric}")
        return {"equipment": p["equipment"], "metric": p["metric"], "label": p["label"], "description": p.get("description"),
                "unit": {"symbol": p["unit"].get("symbol"), "label": p["unit"].get("label"), "qudt": p["unit"].get("id")},
                "codes": (p.get("codes") or {}).get("values"), "latest": _latest(p.get("latest")),
                "endpoints": p.get("endpoints")}

    @server.tool(annotations=read_only, description=(
        "計測項目の時系列を返す。既定は要約（件数・最小・最大とその時刻・平均。稼働状態なら状態ごとの時間（分）と、状態が変わった時刻）"
        "と、60 点ほどに間引いた値。「14 時台に異常停止はあったか」「午前の最大負荷はいつか」のような質問に使う。"
        "範囲は CSV 上の時刻（source_from / source_to、例 2026-10-01T14:00）で指定するのが基本。"
        "細かい値そのものが必要なときだけ detail=true（最大 1000 件、続きは next）。"))
    def get_readings(
        equipment_id: Annotated[str, Field(description="設備 ID（例: CNC-01）")],
        metric: Annotated[str, Field(description="計測項目名（例: status、spindle_load）")],
        source_from: Annotated[str | None, Field(description="CSV 上の時刻の開始（例: 2026-10-01T14:00。日本時間）")] = None,
        source_to: Annotated[str | None, Field(description="CSV 上の時刻の終了（例: 2026-10-01T15:00）")] = None,
        minutes: Annotated[int | None, Field(description="反映時刻で直近 N 分（source_from / source_to の代わり）", ge=1)] = None,
        detail: Annotated[bool, Field(description="true なら要約ではなく値そのものを返す")] = False,
        limit: Annotated[int, Field(description="detail=true のときの件数（1〜1000）", ge=1, le=READINGS_MAX_DETAIL)] = 200,
        next: Annotated[str | None, Field(description="detail=true のとき、前回の応答の next（続きを取る）")] = None,
    ) -> dict:
        params = {"source_from": source_from, "source_to": source_to, "minutes": minutes}
        base = f"/v1/equipment/{equipment_id}/properties/{metric}/readings"
        if not detail:
            return call(base + "/summary", params)
        body = call(base, {**params, "limit": limit, "next": next})
        return {"equipment": body["equipment"], "metric": body["metric"], "unit": _unit(body["unit"]),
                **({"unavailable": body["unavailable"]} if "unavailable" in body else {}),
                "items": [{"source_ts": i["source_ts"], "value": i["value"]} for i in body.get("items", [])],
                "count": body.get("count", 0), "next": body.get("next")}

    @server.tool(annotations=read_only, description=(
        "設備・計測項目を、ID・名前・分類・説明の一部で探す（例: 'コンプレッサ'、'温度'、'主軸'）。名前は分かるが ID が分からないときに使う。"))
    def search(query: Annotated[str, Field(description="探す文字列（日本語・英語・ID）")]) -> dict:
        body = call("/v1/search", {"q": query})
        return {"equipment": [_equipment(e) for e in body["equipment"]], "properties": body["properties"]}

    @server.tool(annotations=read_only, description=(
        "データの状態を返す: ナレッジグラフ（トリプル数・版）と、replay（再生中か・進み具合・CSV 上の現在位置）。"
        "値が古い・取れないときの確認に使う。"))
    def get_status() -> dict:
        return call("/v1/status")

    if enable_sparql:
        @server.tool(annotations=read_only, description=(
            "【補助】ナレッジグラフに SPARQL（SELECT / ASK）を直接投げる。ほかのツールで答えられないときだけ使う。"
            f"PREFIX は省略できない（リソース factory-kg://ontology を参照）。結果は最大 {SPARQL_MAX_ROWS} 行。"
            "計測値はグラフに入っていない（値は get_equipment / get_readings で取る）。更新はできない。"))
        def sparql_query(query: Annotated[str, Field(description="SPARQL の SELECT または ASK")]) -> dict:
            if re.search(r"\b(INSERT|DELETE|LOAD|CLEAR|DROP|CREATE|COPY|MOVE|ADD)\b", query, re.IGNORECASE) \
                    and not re.search(r"\b(SELECT|ASK|CONSTRUCT|DESCRIBE)\b", query, re.IGNORECASE):
                raise BackendError("更新はできません（読み取り専用）")
            if re.search(r"\bSELECT\b", query, re.IGNORECASE) and not re.search(r"\bLIMIT\s+\d+", query, re.IGNORECASE):
                query = query.rstrip() + f"\nLIMIT {SPARQL_MAX_ROWS + 1}"
            result = backend.sparql(query)
            if "boolean" in result:
                return {"boolean": result["boolean"]}
            names = result["head"]["vars"]
            rows = [{n: _short(b[n]["value"]) for n in names if n in b} for b in result["results"]["bindings"]]
            return {"vars": names, "rows": rows[:SPARQL_MAX_ROWS], "truncated": len(rows) > SPARQL_MAX_ROWS}

    @server.resource("factory-kg://ontology", name="ontology", title="ナレッジグラフの構造と用語",
                     description="標準オントロジーの使い分け、接頭辞、設備と計測項目の一覧", mime_type="text/markdown")
    def ontology() -> str:
        lines = ["", "## 今のグラフにある設備と計測項目", ""]

        def walk(node, depth=0):
            if node["kind"] == "equipment":
                lines.append(f"- {node['id']} {node['label']}（{node.get('category')}）: {', '.join(node.get('metrics', []))}")
                return
            if node["kind"] == "line":
                lines.append(f"### {node['id']} {node['label']}")
            for child in node.get("children", []):
                walk(child, depth + 1)
        for factory in call("/v1/factory")["factories"]:
            walk(factory)
        return ONTOLOGY_GUIDE + "\n".join(lines) + "\n"

    @server.resource("factory-kg://codes", name="codes", title="コード表（稼働状態など）",
                     description="コード値の意味（例: 稼働状態 0=停止 1=稼働 2=異常停止）", mime_type="application/json")
    def codes() -> str:
        return json.dumps(call("/v1/codes/equipment-status"), ensure_ascii=False)

    return server


def _short(value: Any) -> Any:
    text = str(value)
    return text if len(text) <= SPARQL_MAX_TEXT else text[:SPARQL_MAX_TEXT] + "…"
