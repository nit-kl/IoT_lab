"""グラフの読み込みと問い合わせ(pyoxigraph)。"""

import re

from pyoxigraph import NamedNode, QueryBoolean, QueryResultsFormat, QuerySolutions, RdfFormat, Store

from kg.common import CURRENT, ONTOLOGY_DIR, ONTOLOGY_GRAPH, QUERIES_DIR, sparql_prefixes

PARAM = re.compile(r"\{\{(\w+)\}\}")
SAFE_PARAM = re.compile(r"[\w:+.\-]*")


def open_store(path=CURRENT, quads=None, data=None):
    """インスタンス(N-Quads のファイル、quads、または本文の data)とオントロジーを読み込んだストアを返す。"""
    store = Store()
    if quads is not None:
        store.bulk_extend(quads)
    elif data is not None:
        store.bulk_load(data, format=RdfFormat.N_QUADS)
    else:
        store.bulk_load(path=str(path), format=RdfFormat.N_QUADS)
    for ttl in sorted(ONTOLOGY_DIR.glob("*.ttl")):
        if ttl.name != "shapes.ttl":
            store.bulk_load(path=str(ttl), format=RdfFormat.TURTLE, to_graph=NamedNode(ONTOLOGY_GRAPH))
    return store


def run(store, sparql):
    """SELECT を実行して行(変数名 → 値の文字列)のリストを返す。全グラフの和を対象にする。"""
    results = store.query(sparql_prefixes() + sparql, use_default_graph_as_union=True)
    if isinstance(results, QueryBoolean):
        return bool(results)
    if not isinstance(results, QuerySolutions):
        raise ValueError("SELECT か ASK だけを実行できます")
    names = [v.value for v in results.variables]
    return [{n: (s[n].value if s[n] is not None else None) for n in names} for s in results]


def run_json(store, sparql):
    results = store.query(sparql_prefixes() + sparql, use_default_graph_as_union=True)
    if not isinstance(results, (QuerySolutions, QueryBoolean)):
        raise ValueError("SELECT か ASK だけを実行できます")
    return results.serialize(format=QueryResultsFormat.JSON)


def load_query(name, **params):
    """queries/<name>.rq を読み、{{名前}} を埋める。値は安全な文字だけを許す。"""
    text = (QUERIES_DIR / f"{name}.rq").read_text(encoding="utf-8")
    for value in params.values():
        if not SAFE_PARAM.fullmatch(str(value)):
            raise ValueError(f"使えない文字が含まれています: {value}")
    missing = set(PARAM.findall(text)) - set(params)
    if missing:
        raise ValueError(f"パラメータが足りません: {sorted(missing)}")
    return PARAM.sub(lambda m: str(params[m.group(1)]), text)


def query_params(name):
    return sorted(set(PARAM.findall((QUERIES_DIR / f"{name}.rq").read_text(encoding="utf-8"))))


def query_title(name):
    first = (QUERIES_DIR / f"{name}.rq").read_text(encoding="utf-8").splitlines()[0]
    return first.lstrip("# ").strip()
