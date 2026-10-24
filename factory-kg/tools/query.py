"""TTL を Oxigraph（ステップ 2 の Lambda と同じ RDF ストア）にメモリ上で読み込み、SPARQL を実行する。

    python tools/query.py queries/01-hierarchy.rq        # ファイルの SPARQL を実行
    python tools/query.py --all                           # queries/*.rq をすべて実行
    python tools/query.py -e "SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }"

名前付きグラフ（ontology / imports / instances）に分けて読み込み、既定のグラフはそれらの和として検索する。
"""

import argparse
import sys
import time
from pathlib import Path

from pyoxigraph import Literal, NamedNode, QuerySolutions, RdfFormat, Store

from kgcommon import PREFIXES, QUERIES_DIR, graph_files

PREFIX_HEADER = "".join(f"PREFIX {p}: <{ns}>\n" for p, ns in PREFIXES.items()) + \
    "PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>\nPREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"


def open_store(instances=None):
    store = Store()
    started = time.perf_counter()
    for path, graph in graph_files(instances).items():
        if path.exists():
            store.load(path=str(path), format=RdfFormat.TURTLE, to_graph=NamedNode(graph))
    return store, time.perf_counter() - started


def shorten(term):
    if isinstance(term, Literal):
        return term.value
    text = str(term.value) if isinstance(term, NamedNode) else str(term)
    for prefix, ns in PREFIXES.items():
        if text.startswith(str(ns)):
            return f"{prefix}:{text[len(str(ns)):]}"
    return text


def run(store, sparql):
    """SELECT の結果を (変数名, 行のリスト) で返す。"""
    result = store.query(PREFIX_HEADER + sparql, use_default_graph_as_union=True)
    if not isinstance(result, QuerySolutions):
        return ["result"], [[str(result)]]
    names = [v.value for v in result.variables]
    rows = [["" if s[n] is None else shorten(s[n]) for n in names] for s in result]
    return names, rows


def print_table(names, rows):
    widths = [max(len(n), *(len(r[i]) for r in rows)) if rows else len(n) for i, n in enumerate(names)]
    print("  ".join(n.ljust(w) for n, w in zip(names, widths)))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(v.ljust(w) for v, w in zip(r, widths)))
    print(f"（{len(rows)} 行）")


def main():
    ap = argparse.ArgumentParser(description="ナレッジグラフに SPARQL を実行する（ローカル・Oxigraph）")
    ap.add_argument("file", nargs="?", type=Path, help="SPARQL のファイル（queries/*.rq）")
    ap.add_argument("-e", "--expr", help="SPARQL を直接指定する（PREFIX は自動で付く）")
    ap.add_argument("--all", action="store_true", help="queries/*.rq をすべて実行する")
    ap.add_argument("--instances", type=Path, help="インスタンスの TTL（既定 build/instances.ttl、なければ例）")
    args = ap.parse_args()
    if not (args.file or args.expr or args.all):
        ap.error("SPARQL のファイル、-e、--all のどれかを指定してください")

    store, seconds = open_store(args.instances)
    print(f"読み込み: {len(store)} トリプル（{seconds * 1000:.0f} ms）\n")
    targets = sorted(QUERIES_DIR.glob("*.rq")) if args.all else ([args.file] if args.file else [])
    for path in targets:
        sparql = path.read_text(encoding="utf-8")
        title = sparql.splitlines()[0].lstrip("# ").strip()
        print(f"■ {path.name}: {title}")
        print_table(*run(store, sparql))
        print()
    if args.expr:
        print_table(*run(store, args.expr))


if __name__ == "__main__":
    sys.exit(main())
