"""使っている標準語彙の IRI が公式の定義に実在するかを検査する。実在しなければ終了コード 1。

    python tools/vocab.py            # 検査(公式の定義は .cache/vocab に保存して再利用)
    python tools/vocab.py --refresh  # 公式から取り直す
"""

import argparse
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rdflib import Dataset, Graph, URIRef

from kg.common import CURRENT, ONTOLOGY_DIR, PREFIXES, ROOT

CACHE_DIR = ROOT / ".cache" / "vocab"
VOCABULARIES = {  # 接頭辞 → (取得先, 形式)
    "org": ("https://www.w3.org/ns/org.ttl", "turtle"),
    "prov": ("https://www.w3.org/ns/prov-o", "turtle"),
    "s4inma": ("https://saref.etsi.org/saref4inma/", "turtle"),
    "saref": ("https://saref.etsi.org/core/", "turtle"),
    "skos": ("http://www.w3.org/2004/02/skos/core.rdf", "xml"),
    "sosa": ("https://www.w3.org/ns/sosa/", "turtle"),
    "ssn": ("https://www.w3.org/ns/ssn/", "turtle"),
}
EXTENSIONS = {"turtle": "ttl", "xml": "rdf"}
ACCEPT = {"turtle": "text/turtle", "xml": "application/rdf+xml"}


def official(prefix, refresh):
    url, fmt = VOCABULARIES[prefix]
    path = CACHE_DIR / f"{prefix}.{EXTENSIONS[fmt]}"
    if refresh or not path.exists():
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(url, headers={"Accept": ACCEPT[fmt], "User-Agent": "factory-ontology-vocab/0.1"})
        with urllib.request.urlopen(request, timeout=120) as response:
            path.write_bytes(response.read())
    graph = Graph()
    graph.parse(path, format=fmt)
    return {str(term) for triple in graph for term in triple if isinstance(term, URIRef)}


def used_iris():
    terms = set()
    for ttl in ONTOLOGY_DIR.glob("*.ttl"):
        terms |= {str(t) for triple in Graph().parse(ttl, format="turtle") for t in triple if isinstance(t, URIRef)}
    if CURRENT.exists():
        dataset = Dataset()
        dataset.parse(CURRENT, format="nquads")
        terms |= {str(t) for s, p, o, _ in dataset.quads((None, None, None, None)) for t in (p, o) if isinstance(t, URIRef)}
    return terms


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    used = used_iris()
    missing = []
    for prefix in VOCABULARIES:
        namespace = PREFIXES[prefix]
        wanted = {t for t in used if t.startswith(namespace)}
        known = official(prefix, args.refresh)
        missing += sorted(wanted - known)
        print(f"{prefix:8} {len(wanted):>3} 語を確認")
    for term in missing:
        print(f"  公式の定義にありません: {term}")
    sys.exit(1 if missing else 0)


if __name__ == "__main__":
    main()
