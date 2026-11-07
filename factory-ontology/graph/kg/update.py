"""届いた差分をグラフへ反映する。S3 には触らない(Lambda とテストの両方から使う)。

1 行は 1 つの名前付きグラフなので、更新はそのグラフの差し替え、論理削除はそのグラフの削除で済む。
同じ差分を 2 回適用しても結果は変わらない。
"""

import gzip
import json
from datetime import datetime, timedelta
from urllib.parse import unquote

from pyoxigraph import NamedNode, Quad, RdfFormat, Store

from kg.common import ID, PREFIXES
from kg.transform import DELETED, JST, Context, convert_row, issue_quads, load_mapping, row_graph

XF = PREFIXES["xf"]
RUN_RETENTION_DAYS = 90
ALIAS_PREDICATES = {"PLC": "plcId", "CMMS": "cmmsId"}


def parse_batch(body):
    """gzip の JSON Lines を (要約, 表名 → 行のリスト) にする。"""
    lines = gzip.decompress(body).decode().splitlines()
    header = json.loads(lines[0])
    tables = {}
    for line in lines[1:]:
        record = json.loads(line)
        tables.setdefault(record["t"], []).append(record["r"])
    return header, tables


def load_store(body=None):
    store = Store()
    if body:
        store.load(gzip.decompress(body), format=RdfFormat.N_QUADS)
    return store


def dump_store(store):
    return gzip.compress(store.dump(format=RdfFormat.N_QUADS), mtime=0)


def _local(node, prefix):
    return unquote(node.value[len(ID + prefix):])


def context_from_store(store):
    """別名と定義済みコードを、今のグラフから復元する。"""
    context = Context()
    for system, name in ALIAS_PREDICATES.items():
        for quad in store.quads_for_pattern(None, NamedNode(XF + name), None, None):
            context.aliases[(system, quad.object.value)] = _local(quad.subject, "equipment/")
    for quad in store.quads_for_pattern(None, NamedNode(PREFIXES["skos"] + "inScheme"), None, None):
        kind, code = _local(quad.subject, "code/").split("/", 1)
        context.codes.add((kind, code))
    return context


def _update_context(name, row, context):
    deleted = row["DEL_FLG"] == DELETED
    if name == "M_KIKAI_ALIAS":
        key = (row["SYS_KBN"], row["GAIBU_CD"])
        if deleted:
            context.aliases.pop(key, None)
        else:
            context.aliases[key] = row["KIKAI_CD"]
    elif name == "M_CODE":
        key = (row["CD_KBN"], row["CD"])
        if deleted:
            context.codes.discard(key)
        else:
            context.codes.add(key)


def _apply_row(store, table, row, context, stats):
    graph = row_graph(table, row)
    store.remove_graph(graph)
    if row["DEL_FLG"] == DELETED:
        stats["deleted"] += 1
        return
    graph, triples, issues = convert_row(table, row, context)
    store.extend(Quad(s, p, o, graph) for s, p, o in triples)
    for issue in issues:
        store.extend(issue_quads(table, row, graph, issue))
    stats["quarantined" if any(i[3] for i in issues) else "converted"] += 1
    stats["issues"] += len(issues)


def _row_failed(name, error, stats):
    """1 行の不正で全体を止めない。"""
    stats["failed"] += 1
    print(json.dumps({"event": "row_failed", "table": name, "error": repr(error)[:200]}, ensure_ascii=False))


def apply_tables(store, tables, mapping=None, context=None):
    """差分を反映して件数を返す。マスタが先に反映されるよう、mapping.toml の順に処理する。"""
    mapping = mapping or load_mapping()
    context = context or context_from_store(store)
    stats = {"converted": 0, "deleted": 0, "quarantined": 0, "issues": 0, "failed": 0}
    for table in mapping:
        name = table["name"]
        rows = tables.get(name, [])
        if "not_in_graph" in table:
            continue
        handled = set()
        usable = []
        for row in rows:
            try:
                _update_context(name, row, context)
                usable.append(row)
            except (KeyError, ValueError, TypeError) as e:
                _row_failed(name, e, stats)
        for row in usable:
            try:
                graph = row_graph(table, row)
                if "row" in table and name != "M_KIKAI_ALIAS":  # 多数の行が 1 つのグラフにまとまる表は 1 回だけ処理する
                    if graph in handled:
                        continue
                    handled.add(graph)
                _apply_row(store, table, row, context, stats)
            except (KeyError, ValueError, TypeError) as e:
                _row_failed(name, e, stats)
    return stats


def prune_runs(store, now=None, days=RUN_RETENTION_DAYS):
    """古い生産実績を消す。実績は件数が多く、問い合わせには最近の分しか使わないため。"""
    cutoff = (now or datetime.now(JST)) - timedelta(days=days)
    graphs = []
    for quad in store.quads_for_pattern(None, NamedNode(PREFIXES["rdf"] + "type"), NamedNode(XF + "ProductionRun"), None):
        ended = next(store.quads_for_pattern(quad.subject, NamedNode(PREFIXES["prov"] + "endedAtTime"), None, None), None)
        if ended is not None and datetime.fromisoformat(ended.object.value) < cutoff:
            graphs.append(quad.graph_name)
    for graph in graphs:
        store.remove_graph(graph)
    return len(graphs)
