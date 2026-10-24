"""AWS 上の SPARQL エンドポイントに問い合わせる CLI（aws login の認証情報で SigV4 署名）。

    python tools/sparql.py --owner kojima --status                  # 公開中のグラフの情報
    python tools/sparql.py --owner kojima queries/01-hierarchy.rq
    python tools/sparql.py --owner kojima --all                     # queries/*.rq をすべて
    python tools/sparql.py --owner kojima -e "SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }"

PREFIX（xf: s4inma: sosa: など）は自動で付く。ローカルで同じことをするのは tools/query.py。
"""

import argparse
import json
import sys
from pathlib import Path

from kgapi import ApiError, KgClient
from kgcommon import PREFIXES, QUERIES_DIR
from query import PREFIX_HEADER, print_table


def shorten(binding):
    if binding is None:
        return ""
    value = binding["value"]
    if binding["type"] == "uri":
        for prefix, ns in PREFIXES.items():
            if value.startswith(str(ns)):
                return f"{prefix}:{value[len(str(ns)):]}"
    return value


def run(client, sparql):
    result = client.query(PREFIX_HEADER + sparql)
    if "boolean" in result:
        return ["result"], [[str(result["boolean"]).lower()]]
    names = result["head"]["vars"]
    return names, [[shorten(b.get(n)) for n in names] for b in result["results"]["bindings"]]


def main():
    ap = argparse.ArgumentParser(description="AWS 上の SPARQL エンドポイントに問い合わせる")
    ap.add_argument("--owner", required=True)
    ap.add_argument("--region", default="ap-northeast-1")
    ap.add_argument("file", nargs="?", type=Path, help="SPARQL のファイル（queries/*.rq）")
    ap.add_argument("-e", "--expr", help="SPARQL を直接指定する")
    ap.add_argument("--all", action="store_true", help="queries/*.rq をすべて実行する")
    ap.add_argument("--status", action="store_true", help="公開中のグラフの情報を表示する")
    args = ap.parse_args()
    if not (args.file or args.expr or args.all or args.status):
        ap.error("SPARQL のファイル、-e、--all、--status のどれかを指定してください")

    client = KgClient(args.owner, args.region)
    try:
        if args.status:
            print(json.dumps(client.status(), ensure_ascii=False, indent=2))
        targets = sorted(QUERIES_DIR.glob("*.rq")) if args.all else ([args.file] if args.file else [])
        for path in targets:
            sparql = path.read_text(encoding="utf-8")
            print(f"■ {path.name}: {sparql.splitlines()[0].lstrip('# ').strip()}")
            print_table(*run(client, sparql))
            print()
        if args.expr:
            print_table(*run(client, args.expr))
    except ApiError as e:
        print(e, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
