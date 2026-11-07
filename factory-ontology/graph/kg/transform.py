"""表の行を変換定義(mapping.toml)に沿って RDF にする。DB にもファイルにも触らない。

1 行を 1 つの名前付きグラフに入れるので、行が更新・論理削除されたらそのグラフを差し替えるだけでよい。
Lambda でも使うので、依存は pyoxigraph だけにしている。
"""

import re
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import quote

from pyoxigraph import Literal, NamedNode, Quad

from kg.common import GRAPH, ID, MAPPING, PREFIXES, RDF_TYPE, RDFS_LABEL, expand

JST = timezone(timedelta(hours=9))
XSD = PREFIXES["xsd"]
XF = PREFIXES["xf"]
FIELD = re.compile(r"\{(\w+)\}")
DELETED = "1"
TYPE = NamedNode(RDF_TYPE)
LABEL = NamedNode(RDFS_LABEL)


def load_mapping(path=MAPPING):
    with open(path, "rb") as f:
        return tomllib.load(f)["table"]


@dataclass
class Context:
    """他の行の内容に依存する参照先(設備の別名、定義済みのコード)。"""

    aliases: dict = field(default_factory=dict)  # (システム, 外部 ID) → 設備コード
    codes: set = field(default_factory=set)  # (区分, コード)

    @classmethod
    def from_tables(cls, tables):
        return cls(
            aliases={(r["SYS_KBN"], r["GAIBU_CD"]): r["KIKAI_CD"] for r in tables.get("M_KIKAI_ALIAS", []) if r["DEL_FLG"] != DELETED},
            codes={(r["CD_KBN"], r["CD"]) for r in tables.get("M_CODE", []) if r["DEL_FLG"] != DELETED},
        )


@dataclass
class Result:
    quads: list = field(default_factory=list)
    counts: dict = field(default_factory=dict)  # 表名 → {source, deleted, converted, quarantined}
    issues: list = field(default_factory=list)  # {table, type, column, value, row, quarantined}


def node(path):
    return NamedNode(ID + quote(path, safe="/-_.~"))


def fill(template, row):
    return FIELD.sub(lambda m: str(row[m.group(1)]), template)


def has_values(template, row):
    return all(row.get(name) is not None for name in FIELD.findall(template))


def to_datetime(value):
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def to_date(value):
    value = to_datetime(value)
    return value.date() if isinstance(value, datetime) else value


def literal(value, kind):
    if kind == "string":
        return Literal(str(value))
    if kind == "ja":
        return Literal(str(value), language="ja")
    if kind == "int":
        return Literal(str(int(value)), datatype=NamedNode(XSD + "integer"))
    if kind == "decimal":
        return Literal(format(Decimal(str(value)).normalize(), "f"), datatype=NamedNode(XSD + "decimal"))
    if kind == "date":
        return Literal(to_date(value).isoformat(), datatype=NamedNode(XSD + "date"))
    if kind == "datetime":
        return Literal(to_datetime(value).replace(tzinfo=JST).isoformat(), datatype=NamedNode(XSD + "dateTime"))
    raise ValueError(f"未対応の kind: {kind}")


class Quarantine(Exception):
    """必須の参照先がなく、この行の本体を作れない。"""


def _resolve_aliases(table, row, context, issues):
    """外部システムの ID を読み替えて、{as} として参照できる行を返す。"""
    resolved = dict(row)
    for prop in table.get("props", []):
        if "alias" not in prop:
            continue
        value = row[prop["column"]]
        target = context.aliases.get((prop["alias"], value))
        if target is None:
            issues.append(("unknown_equipment", prop["column"], value, bool(prop.get("required"))))
            if prop.get("required"):
                raise Quarantine
        else:
            resolved[prop["as"]] = target
    return resolved


def _prop_quads(prop, subject, row, context, issues):
    """1 つの prop から (主語, 述語, 目的語) を作る。"""
    if "code" in prop:
        value = row[prop["column"]]
        if value is None:
            return []
        if (prop["code"], value) not in context.codes:
            issues.append(("unknown_code", prop["column"], value, False))
            return []
        return [(subject, NamedNode(expand(prop["predicate"])), node(f"code/{prop['code']}/{value}"))]
    if "iri" in prop:
        return [(subject, NamedNode(expand(prop["predicate"])), node(prop["iri"]))]
    if "ref" in prop or "ref_by" in prop:
        if "unless" in prop and row.get(prop["unless"]) is not None:
            return []
        template = prop.get("ref") or prop["ref_by"]["map"][row[prop["ref_by"]["column"]]]
        if not has_values(template, row):
            return []
        target, predicate = node(fill(template, row)), NamedNode(expand(prop["predicate"]))
        return [(target, predicate, subject) if prop.get("inverse") else (subject, predicate, target)]

    value = row[prop["column"]]
    if value is None:
        if prop.get("when_null") == "warn":
            issues.append(("missing_value", prop["column"], None, False))
        return []
    name = prop.get("predicate") or prop["predicate_by"]["map"][row[prop["predicate_by"]["column"]]]
    return [(subject, NamedNode(expand(name)), literal(value, prop.get("kind", "string")))]


def convert_row(table, row, context):
    """1 行を (その行のグラフ名, トリプルの一覧, 問題の一覧) にする。隔離した行のトリプルは空。"""
    issues = []
    try:
        resolved = _resolve_aliases(table, row, context, issues)
    except Quarantine:
        return row_graph(table, row), [], issues
    subject = node(fill(table["subject"], resolved))
    triples = [(subject, TYPE, NamedNode(expand(t))) for t in table.get("types", [])]
    if "label" in table:
        triples.append((subject, LABEL, literal(fill(table["label"], resolved), "ja")))
    for prop in table.get("props", []):
        triples += _prop_quads(prop, subject, resolved, context, issues)
    return row_graph(table, row), triples, issues


def row_graph(table, row):
    key = fill(table.get("row") or table["subject"], row)
    return NamedNode(f"{GRAPH}row/{table['name']}/{quote(key, safe='/-_.~')}")


def issue_quads(table, row, graph, issue):
    kind, column, value, quarantined = issue
    subject = NamedNode(graph.value.replace(GRAPH + "row/", ID + "issue/") + f"/{column}")
    pairs = [(TYPE, NamedNode(XF + "DataIssue")), (NamedNode(XF + "issueType"), Literal(kind)),
             (NamedNode(XF + "sourceTable"), Literal(table["name"])), (NamedNode(XF + "sourceColumn"), Literal(column)),
             (NamedNode(XF + "quarantined"), Literal("true" if quarantined else "false", datatype=NamedNode(XSD + "boolean")))]
    if value is not None:
        pairs.append((NamedNode(XF + "sourceValue"), Literal(str(value))))
    return [Quad(subject, p, o, graph) for p, o in pairs]


def convert_tables(tables, mapping=None):
    """全表を変換する。tables は 表名 → 行(dict)のリスト。"""
    mapping = mapping or load_mapping()
    context = Context.from_tables(tables)
    result = Result()
    for table in mapping:
        name = table["name"]
        rows = tables.get(name, [])
        counts = {"source": len(rows), "deleted": 0, "converted": 0, "quarantined": 0}
        result.counts[name] = counts
        if "not_in_graph" in table:
            counts["not_in_graph"] = table["not_in_graph"]
            continue
        seen = set()
        for row in rows:
            if row["DEL_FLG"] == DELETED:
                counts["deleted"] += 1
                continue
            graph, triples, issues = convert_row(table, row, context)
            quarantined = any(i[3] for i in issues)
            counts["quarantined" if quarantined else "converted"] += 1
            for s, p, o in triples:
                quad = Quad(s, p, o, graph)
                if quad not in seen:
                    seen.add(quad)
                    result.quads.append(quad)
            for issue in issues:
                result.issues.append({"table": name, "type": issue[0], "column": issue[1], "value": issue[2],
                                      "row": graph.value, "quarantined": issue[3]})
                result.quads += issue_quads(table, row, graph, issue)
    return result
