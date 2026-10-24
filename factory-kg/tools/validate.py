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
    """(適合したか, 違反の一覧 [(対象, メッセージ)], 読み込んだトリプル数, 読み込んだファイル, 警告の一覧) を返す。

    警告（sh:Warning。例: 設備の配置がない）は一覧に出すが、適合の判定には含めない。
    """
    files = graph_files(instances)
    data = load_union(files)
    shapes = Graph().parse(SHAPES, format="turtle")
    conforms, report, _ = validate(data, shacl_graph=shapes, inference="none", advanced=True, allow_warnings=True)
    problems, warnings = [], []
    for result, severity in report.subject_objects(SH.resultSeverity):
        focus = report.value(result, SH.focusNode)
        message = report.value(result, SH.resultMessage)
        label = data.value(focus, RDFS.label) if focus is not None else None
        (warnings if severity == SH.Warning else problems).append((str(label or focus), str(message)))
    return conforms, sorted(set(problems)), len(data), files, sorted(set(warnings))


def main():
    ap = argparse.ArgumentParser(description="SHACL でナレッジグラフを検証する")
    ap.add_argument("--instances", type=Path, help="検証するインスタンスの TTL（既定 build/instances.ttl）")
    args = ap.parse_args()

    conforms, problems, triples, files, warnings = run(args.instances)
    for path in files:
        print(f"  読み込み: {path.relative_to(path.parents[1])}" if path.exists() else f"  （なし）: {path.name}")
    for focus, message in warnings:
        print(f"  警告: {focus}: {message}")
    if conforms:
        print(f"SHACL 検証: 適合（{triples} トリプル）")
        return
    print(f"SHACL 検証: 違反 {len(problems)} 件", file=sys.stderr)
    for focus, message in problems:
        print(f"  {focus}: {message}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
