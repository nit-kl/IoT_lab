"""名前空間、ファイルの置き場所、名前付きグラフの定義。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ONTOLOGY_DIR = ROOT / "ontology"
SHAPES = ONTOLOGY_DIR / "shapes.ttl"
MAPPING = ROOT / "mapping" / "mapping.toml"
QUERIES_DIR = ROOT / "queries"
BUILD_DIR = ROOT / "build"
CURRENT = BUILD_DIR / "current.nq"
REPORT = BUILD_DIR / "report.json"
ONPREM = ROOT.parent / "onprem"

# IRI のベース(仮ドメイン)
BASE = "https://xcp-factory.example/"
ID = BASE + "id/"
GRAPH = BASE + "graph/"
ONTOLOGY_GRAPH = GRAPH + "ontology"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"

PREFIXES = {
    "xf": BASE + "def#",
    "id": ID,
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "org": "http://www.w3.org/ns/org#",
    "prov": "http://www.w3.org/ns/prov#",
    "s4inma": "https://saref.etsi.org/saref4inma/",
    "saref": "https://saref.etsi.org/core/",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "sosa": "http://www.w3.org/ns/sosa/",
    "ssn": "http://www.w3.org/ns/ssn/",
}


def expand(name):
    """xf:Alarm のような短縮形を IRI にする。"""
    prefix, local = name.split(":", 1)
    return PREFIXES[prefix] + local


def sparql_prefixes():
    return "".join(f"PREFIX {p}: <{ns}>\n" for p, ns in PREFIXES.items())
