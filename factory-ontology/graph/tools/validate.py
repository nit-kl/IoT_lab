"""ナレッジグラフを SHACL(ontology/shapes.ttl)で検証する。違反があれば終了コード 1。

    python tools/validate.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pyshacl import validate
from rdflib import Dataset, Graph, Namespace

from kg.common import CURRENT, ONTOLOGY_DIR, SHAPES

SH = Namespace("http://www.w3.org/ns/shacl#")


def load_data(nquads_path=CURRENT, nquads_text=None):
    dataset = Dataset()
    if nquads_text is not None:
        dataset.parse(data=nquads_text, format="nquads")
    else:
        dataset.parse(nquads_path, format="nquads")
    data = Graph()
    for s, p, o, _ in dataset.quads((None, None, None, None)):
        data.add((s, p, o))
    for ttl in ONTOLOGY_DIR.glob("*.ttl"):
        if ttl != SHAPES:
            data.parse(ttl, format="turtle")
    return data


def run(data):
    """(適合したか, 違反の一覧 [(対象, メッセージ)]) を返す。"""
    shapes = Graph().parse(SHAPES, format="turtle")
    conforms, report, _ = validate(data, shacl_graph=shapes, inference="none", advanced=True)
    problems = sorted({(str(report.value(r, SH.focusNode)), str(report.value(r, SH.resultMessage)))
                       for r in report.subjects(SH.resultSeverity, None)})
    return conforms, problems


def main():
    data = load_data()
    conforms, problems = run(data)
    for focus, message in problems[:30]:
        print(f"  違反: {focus}: {message}")
    print(f"{len(data)} トリプルを検証: " + ("適合" if conforms else f"違反 {len(problems)} 件"))
    sys.exit(0 if conforms else 1)


if __name__ == "__main__":
    main()
