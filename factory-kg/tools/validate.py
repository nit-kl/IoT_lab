"""ナレッジグラフ（ontology/*.ttl + インスタンス）を SHACL（ontology/shapes.ttl）で検証する。

    python tools/validate.py                          # build/instances.ttl（なければ examples/ の例）を検証
    python tools/validate.py --instances other.ttl

違反があれば内容を表示して終了コード 1 で終わる（publish_ttl.py は通ったときだけ S3 に上げる）。
"""

import argparse
import sys
from pathlib import Path

from pyshacl import validate
from rdflib import Graph, Namespace
from rdflib.namespace import RDFS

from kgcommon import SHAPES, graph_files, load_union

SH = Namespace("http://www.w3.org/ns/shacl#")


def run(instances=None):
    """(適合したか, 違反の一覧 [(対象, メッセージ)], 読み込んだトリプル数, 読み込んだファイル) を返す。"""
    files = graph_files(instances)
    data = load_union(files)
    shapes = Graph().parse(SHAPES, format="turtle")
    conforms, report, _ = validate(data, shacl_graph=shapes, inference="none", advanced=True, allow_warnings=False)
    problems = []
    for result in report.subjects(SH.resultSeverity, None):
        focus = report.value(result, SH.focusNode)
        message = report.value(result, SH.resultMessage)
        label = data.value(focus, RDFS.label) if focus is not None else None
        problems.append((str(label or focus), str(message)))
    return conforms, sorted(set(problems)), len(data), files


def main():
    ap = argparse.ArgumentParser(description="SHACL でナレッジグラフを検証する")
    ap.add_argument("--instances", type=Path, help="検証するインスタンスの TTL（既定 build/instances.ttl）")
    args = ap.parse_args()

    conforms, problems, triples, files = run(args.instances)
    for path in files:
        print(f"  読み込み: {path.relative_to(path.parents[1])}" if path.exists() else f"  （なし）: {path.name}")
    if conforms:
        print(f"SHACL 検証: 適合（{triples} トリプル）")
        return
    print(f"SHACL 検証: 違反 {len(problems)} 件", file=sys.stderr)
    for focus, message in problems:
        print(f"  {focus}: {message}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
