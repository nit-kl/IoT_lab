"""工場・ライン・設備・計測項目と、replay の HTTP API への参照を TTL にする（build/instances.ttl）。

設備と計測項目の一覧は、replay の API（/latest）か CSV から取る。

    python tools/generate_ttl.py --owner kojima                       # replay の API から（URL もスタックから）
    python tools/generate_ttl.py --csv ../factory-replay/data/factory_sample.csv --owner kojima   # CSV から、URL はスタックから
    python tools/generate_ttl.py --csv ../factory-replay/data/factory_sample.csv                  # CSV から、URL は仮
    python tools/generate_ttl.py --csv ... --api-url https://xxxx.lambda-url.ap-northeast-1.on.aws/

カタログ（設備の種類・計測項目・単位の対応表）にないものが出てきたらエラーにする。カタログに追記してから再実行する。
"""

import argparse
import csv
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS, XSD

from kgcommon import (DCAT, DCTERMS, HYDRA, ID, INSTANCES, PREFIXES, PROV, QUDT, S4INMA, SAREF, SOSA, SSN, UNIT, XF)

REPLAY_DIR = Path(__file__).resolve().parents[2] / "factory-replay"
PLACEHOLDER_API = "https://replay-api.invalid/"  # .invalid は予約済みのドメイン（どこにもつながらない）

# ---------- カタログ ----------

# 設備 ID の接頭辞 → 分類（codes.ttl の id:category/...）、日本語名、生産設備か
EQUIPMENT_TYPES = {
    "PRESS": ("press", "プレス機", "Press machine", True),
    "CNC": ("cnc", "CNC 加工機", "CNC machining center", True),
    "CONV": ("conveyor", "搬送コンベア", "Conveyor", True),
    "COMP": ("compressor", "エアコンプレッサ", "Air compressor", False),
    "ENV": ("environment-sensor", "環境センサ", "Environment sensor", False),
}

# メトリクス名 → 日本語名、英語名、説明（値の意味）
METRICS = {
    "status": ("稼働状態", "status", "0=停止 1=稼働 2=異常停止（xf:valueCodeList のコード表を参照）"),
    "shot_count": ("ショット数", "shot count", "その計測間隔内のショット数（累計ではない）"),
    "hydraulic_pressure": ("油圧", "hydraulic pressure", None),
    "motor_current": ("モータ電流", "motor current", None),
    "spindle_speed": ("主軸回転数", "spindle speed", None),
    "spindle_load": ("主軸負荷", "spindle load", "主軸モータの負荷率"),
    "work_count": ("加工数", "work count", "その計測間隔内の加工完了数（累計ではない）"),
    "speed": ("搬送速度", "conveyor speed", None),
    "motor_temp": ("モータ温度", "motor temperature", None),
    "discharge_pressure": ("吐出圧力", "discharge pressure", None),
    "power": ("消費電力", "power consumption", None),
    "temperature": ("温度", "temperature", "工場内の室温"),
    "humidity": ("湿度", "relative humidity", "工場内の相対湿度"),
    "co2": ("CO2 濃度", "CO2 concentration", "工場内の CO2 濃度"),
}

# CSV・API の unit の文字列 → QUDT の単位（units.ttl に抜粋がある必要がある）
UNITS = {
    "MPa": "MegaPA", "degC": "DEG_C", "A": "A", "kW": "KiloW", "rpm": "REV-PER-MIN", "%": "PERCENT",
    "count": "NUM", "ppm": "PPM", "m/min": "M-PER-MIN", "": "UNITLESS",
}

CODE_LISTS = {"status": ID["scheme/equipment-status"]}
EQUIPMENT_ID = re.compile(r"^([A-Z]+)-(\d+)$")
LINE_ID = re.compile(r"^LINE-([A-Z])$")

LATEST_TEMPLATE = "latest{?equipment,metric}"
READINGS_TEMPLATE = "readings{?equipment,metric,minutes,from,to,source_from,source_to,limit,next}"


class CatalogError(Exception):
    pass


# ---------- 入力（設備・計測項目の一覧） ----------

def rows_from_csv(path):
    """CSV から (line_id, equipment_id, metric, unit) の組を集める。"""
    seen = set()
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            seen.add((row["line_id"], row["equipment_id"], row["metric"], row["unit"]))
    return seen


def replay_client(owner, region):
    sys.path.insert(0, str(REPLAY_DIR / "tools"))
    from apiclient import Client  # factory-replay の CLI と同じ（aws login の認証情報で SigV4 署名）
    return Client(owner, region)


def rows_from_api(client):
    status = client.get("/status")
    if not status.get("total_rows"):
        raise SystemExit("replay に CSV が取り込まれていません（/status の total_rows が 0）。先に CSV をアップロードしてください")
    if not status.get("reflected_rows"):
        raise SystemExit("replay の readings が空です。リプレイを開始する（reset など）か、--csv で CSV から生成してください")
    items = client.get("/latest")["items"]
    return {(i["line_id"], i["equipment_id"], i["metric"], i["unit"]) for i in items}


# ---------- TTL の組み立て ----------

def lit(text, lang):
    return Literal(text, lang=lang)


def add_service(g, api_url):
    service = ID["service/replay-api"]
    g.add((service, RDF.type, DCAT.DataService))
    g.add((service, RDFS.label, lit("工場 CSV リプレイ HTTP API", "ja")))
    g.add((service, RDFS.label, lit("factory CSV replay HTTP API", "en")))
    g.add((service, RDFS.comment, lit(
        "factory-replay の HTTP API（Lambda Function URL）。IAM 認証のため、SigV4 署名（サービス名 lambda、"
        "リージョン ap-northeast-1）が必要。時刻は日本時間で返す。", "ja")))
    g.add((service, DCAT.endpointURL, URIRef(api_url)))
    for name, template, prop, variables in (
        ("latest", LATEST_TEMPLATE, XF.latestTemplate, ["equipment", "metric"]),
        ("readings", READINGS_TEMPLATE, XF.readingsTemplate,
         ["equipment", "metric", "minutes", "from", "to", "source_from", "source_to", "limit", "next"]),
    ):
        node = ID[f"service/replay-api/template/{name}"]
        g.add((service, prop, node))
        g.add((node, RDF.type, HYDRA.IriTemplate))
        g.add((node, HYDRA.template, Literal(template)))
        g.add((node, HYDRA.variableRepresentation, HYDRA.BasicRepresentation))
        for var in variables:
            mapping = ID[f"service/replay-api/template/{name}/{var}"]
            g.add((node, HYDRA.mapping, mapping))
            g.add((mapping, RDF.type, HYDRA.IriTemplateMapping))
            g.add((mapping, HYDRA.variable, Literal(var)))
            required = var in ("equipment", "metric")
            g.add((mapping, HYDRA.required, Literal(required)))
            if var == "equipment":
                g.add((mapping, HYDRA.property, XF.equipmentId))
            elif var == "metric":
                g.add((mapping, HYDRA.property, XF.metricName))
    return service


def endpoint(api_url, path, equipment_id, metric):
    return URIRef(f"{api_url}{path}?" + urlencode({"equipment": equipment_id, "metric": metric}))


def build_graph(rows, api_url, source):
    g = Graph()
    for prefix, ns in PREFIXES.items():
        g.bind(prefix, ns)

    # 来歴
    doc = ID["graph/instances"]
    g.add((doc, RDF.type, PROV.Entity))
    g.add((doc, RDFS.label, lit("工場のインスタンス（生成）", "ja")))
    g.add((doc, DCTERMS.created, Literal(datetime.now(timezone.utc).isoformat(timespec="seconds"), datatype=XSD.dateTime)))
    g.add((doc, DCTERMS.source, Literal(source)))
    tool = ID["agent/generate_ttl"]
    g.add((doc, PROV.wasAttributedTo, tool))
    g.add((tool, RDF.type, PROV.SoftwareAgent))
    g.add((tool, RDFS.label, Literal("factory-kg/tools/generate_ttl.py")))

    service = add_service(g, api_url)

    # 工場 → サイト → エリア（IEC 62264 / SAREF4INMA の階層。サンプルは 1 つずつ）
    factory, site, area = ID["factory/sample"], ID["site/sample"], ID["area/production"]
    for node, cls, ja, en in ((factory, S4INMA.Factory, "サンプル工場", "Sample factory"),
                              (site, S4INMA.Site, "サンプル工場 サイト", "Sample factory site"),
                              (area, S4INMA.Area, "製造エリア", "Production area")):
        g.add((node, RDF.type, cls))
        g.add((node, RDFS.label, lit(ja, "ja")))
        g.add((node, RDFS.label, lit(en, "en")))
    g.add((factory, SAREF.consistsOf, site))
    g.add((site, SAREF.consistsOf, area))

    problems = []
    for line_id, equipment_id, metric, unit in sorted(rows):
        m_line, m_eq = LINE_ID.match(line_id), EQUIPMENT_ID.match(equipment_id)
        if not m_line:
            problems.append(f"ライン ID の形式が想定外: {line_id}")
            continue
        if not m_eq or m_eq.group(1) not in EQUIPMENT_TYPES:
            problems.append(f"設備の種類がカタログにない: {equipment_id}（EQUIPMENT_TYPES に追加）")
            continue
        if metric not in METRICS:
            problems.append(f"計測項目がカタログにない: {equipment_id} / {metric}（METRICS に追加）")
            continue
        if unit not in UNITS:
            problems.append(f"単位がカタログにない: {equipment_id} / {metric} / '{unit}'（UNITS と units.ttl に追加）")
            continue

        line = ID[f"line/{line_id}"]
        g.add((area, SAREF.consistsOf, line))
        g.add((line, RDF.type, S4INMA.WorkCenter))
        g.add((line, XF.lineId, Literal(line_id)))
        g.add((line, RDFS.label, lit(f"ライン {m_line.group(1)}", "ja")))
        g.add((line, RDFS.label, lit(f"Line {m_line.group(1)}", "en")))

        category, type_ja, type_en, production = EQUIPMENT_TYPES[m_eq.group(1)]
        eq = ID[f"equipment/{equipment_id}"]
        g.add((line, SAREF.consistsOf, eq))
        g.add((eq, RDF.type, S4INMA.ProductionEquipment if production else SAREF.Device))
        g.add((eq, RDF.type, SOSA.FeatureOfInterest))
        g.add((eq, XF.equipmentId, Literal(equipment_id)))
        g.add((eq, RDFS.label, lit(f"{type_ja} {m_eq.group(2)}", "ja")))
        g.add((eq, RDFS.label, lit(f"{type_en} {m_eq.group(2)}", "en")))
        g.add((eq, S4INMA.belongsToCategory, ID[f"category/{category}"]))

        metric_ja, metric_en, comment = METRICS[metric]
        prop = ID[f"property/{equipment_id}/{metric}"]
        g.add((eq, SSN.hasProperty, prop))
        g.add((prop, RDF.type, SOSA.ObservableProperty))
        g.add((prop, SSN.isPropertyOf, eq))
        g.add((prop, RDFS.label, lit(f"{equipment_id} {metric_ja}", "ja")))
        g.add((prop, RDFS.label, lit(f"{equipment_id} {metric_en}", "en")))
        if comment:
            g.add((prop, RDFS.comment, lit(comment, "ja")))
        g.add((prop, XF.metricName, Literal(metric)))
        g.add((prop, QUDT.hasUnit, UNIT[UNITS[unit]]))
        g.add((prop, XF.apiService, service))
        g.add((prop, XF.latestEndpoint, endpoint(api_url, "latest", equipment_id, metric)))
        g.add((prop, XF.readingsEndpoint, endpoint(api_url, "readings", equipment_id, metric)))
        if metric in CODE_LISTS:
            g.add((prop, XF.valueCodeList, CODE_LISTS[metric]))

    if problems:
        raise CatalogError("\n".join(sorted(set(problems))))
    return g


def write_turtle(g, path):
    body = g.serialize(format="turtle")
    header = ("# 生成ファイル（factory-kg/tools/generate_ttl.py）。手で編集しない。\n"
              "# 設備・計測項目を変えたいときは replay のデータか generate_ttl.py のカタログを直して再生成する。\n\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + body, encoding="utf-8", newline="\n")


def main():
    ap = argparse.ArgumentParser(description="工場のインスタンス TTL を生成する")
    ap.add_argument("--owner", help="replay の利用者名（API から取得する・URL をスタックから取るときに指定）")
    ap.add_argument("--region", default="ap-northeast-1")
    ap.add_argument("--csv", type=Path, help="replay の API ではなく CSV から設備・計測項目を取る")
    ap.add_argument("--api-url", help="TTL に書く replay API の URL（省略時はスタックから。--owner もなければ仮の URL）")
    ap.add_argument("--out", type=Path, default=INSTANCES, help=f"出力先（既定 {INSTANCES.relative_to(INSTANCES.parents[1])}）")
    args = ap.parse_args()
    if not args.csv and not args.owner:
        ap.error("--owner（replay の API から取る）か --csv のどちらかを指定してください")

    try:
        client = replay_client(args.owner, args.region) if args.owner else None
    except Exception as e:  # スタックがない・ログインしていない など
        raise SystemExit(f"replay の環境（xcp-factory-{args.owner}）に接続できません: {e}\n"
                         "replay を作るか、--csv で CSV から生成してください") from None
    api_url = args.api_url or (client.base + "/" if client else PLACEHOLDER_API)
    if not api_url.endswith("/"):
        api_url += "/"

    if args.csv:
        rows, source = rows_from_csv(args.csv), f"CSV: {args.csv.name}"
    else:
        try:
            rows, source = rows_from_api(client), f"replay API: {api_url}latest"
        except Exception as e:  # replay の apiclient.ApiError（503: DB 再開中 など）
            raise SystemExit(f"replay の API から取得できません: {e}\n"
                             "DB の再開中なら 30 秒ほど待って再実行してください") from None

    try:
        g = build_graph(rows, api_url, source)
    except CatalogError as e:
        print(f"カタログにないものがあります:\n{e}", file=sys.stderr)
        sys.exit(1)
    write_turtle(g, args.out)

    equipment = len({r[1] for r in rows})
    print(f"{args.out}: {len(g)} トリプル（ライン {len({r[0] for r in rows})}・設備 {equipment}・計測項目 {len(rows)}）")
    print(f"API の URL: {api_url}" + ("（仮。実際の API は呼べません）" if api_url == PLACEHOLDER_API else ""))


if __name__ == "__main__":
    main()
