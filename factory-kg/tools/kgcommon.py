"""factory-kg の共通定義（名前空間・ファイルの置き場所・名前付きグラフ・参照する標準語彙）。"""

from pathlib import Path

import rdflib
from rdflib import Namespace

ROOT = Path(__file__).resolve().parent.parent
ONTOLOGY_DIR = ROOT / "ontology"
BUILD_DIR = ROOT / "build"
CACHE_DIR = ROOT / ".cache" / "vocab"
QUERIES_DIR = ROOT / "queries"
EXAMPLE_INSTANCES = ROOT / "examples" / "instances.sample.ttl"
INSTANCES = BUILD_DIR / "instances.ttl"
SHAPES = ONTOLOGY_DIR / "shapes.ttl"

# IRI のベース（決定事項 7: 仮ドメイン。正式なドメインに変えるときはここだけ直して再生成する）
BASE = "https://xcp-factory.example/"
XF = Namespace(BASE + "def#")
ID = Namespace(BASE + "id/")

SOSA = Namespace("http://www.w3.org/ns/sosa/")
SSN = Namespace("http://www.w3.org/ns/ssn/")
SAREF = Namespace("https://saref.etsi.org/core/")
S4INMA = Namespace("https://saref.etsi.org/saref4inma/")
QUDT = Namespace("http://qudt.org/schema/qudt/")
UNIT = Namespace("http://qudt.org/vocab/unit/")
DCAT = Namespace("http://www.w3.org/ns/dcat#")
HYDRA = Namespace("http://www.w3.org/ns/hydra/core#")
SKOS = Namespace("http://www.w3.org/2004/02/skos/core#")
PROV = Namespace("http://www.w3.org/ns/prov#")
DCTERMS = Namespace("http://purl.org/dc/terms/")
GEO = Namespace("http://www.opengis.net/ont/geosparql#")

PREFIXES = {
    "xf": XF, "id": ID, "sosa": SOSA, "ssn": SSN, "saref": SAREF, "s4inma": S4INMA, "qudt": QUDT, "unit": UNIT,
    "dcat": DCAT, "hydra": HYDRA, "skos": SKOS, "prov": PROV, "dcterms": DCTERMS, "geo": GEO,
}

# 参照する標準語彙: 接頭辞 → (名前空間, 公式の取得先, 形式)。vocab.py が取得して IRI の実在を検査する
VOCABULARIES = {
    "sosa": (str(SOSA), "https://www.w3.org/ns/sosa/", "turtle"),
    "ssn": (str(SSN), "https://www.w3.org/ns/ssn/", "turtle"),
    "saref": (str(SAREF), "https://saref.etsi.org/core/", "turtle"),
    "s4inma": (str(S4INMA), "https://saref.etsi.org/saref4inma/", "turtle"),
    "qudt": (str(QUDT), "http://qudt.org/schema/qudt/", "turtle"),
    "unit": (str(UNIT), "http://qudt.org/vocab/unit/", "turtle"),
    "dcat": (str(DCAT), "https://www.w3.org/ns/dcat.ttl", "turtle"),
    "hydra": (str(HYDRA), "http://www.w3.org/ns/hydra/core", "json-ld"),
    "skos": (str(SKOS), "http://www.w3.org/2004/02/skos/core.rdf", "xml"),
    "prov": (str(PROV), "https://www.w3.org/ns/prov.ttl", "turtle"),
    "dcterms": (str(DCTERMS), "https://www.dublincore.org/specifications/dublin-core/dcmi-terms/dublin_core_terms.ttl", "turtle"),
    # GeoSPARQL は名前空間の URL で語彙を返さないので、OGC の公式スキーマ（1.0）を取得する
    "geo": (str(GEO), "https://schemas.opengis.net/geosparql/1.0/geosparql_vocab_all.rdf", "xml"),
}

# 名前付きグラフ: ファイル → グラフ IRI。出どころ（手書き・標準からの抜粋・生成）を区別する
GRAPH_ONTOLOGY = BASE + "graph/ontology"
GRAPH_IMPORTS = BASE + "graph/imports"
GRAPH_INSTANCES = BASE + "graph/instances"
GRAPH_LAYOUT = BASE + "graph/layout"
ONTOLOGY_FILES = {
    ONTOLOGY_DIR / "xf.ttl": GRAPH_ONTOLOGY,
    ONTOLOGY_DIR / "codes.ttl": GRAPH_ONTOLOGY,
    ONTOLOGY_DIR / "labels-ja.ttl": GRAPH_ONTOLOGY,
    ONTOLOGY_DIR / "imports.ttl": GRAPH_IMPORTS,
    ONTOLOGY_DIR / "units.ttl": GRAPH_IMPORTS,
    ONTOLOGY_DIR / "layout.ttl": GRAPH_LAYOUT,
}


def graph_files(instances=None):
    """読み込む TTL と名前付きグラフの組。instances を省略すると build/instances.ttl（なければ例）を使う。"""
    files = dict(ONTOLOGY_FILES)
    if instances is None:
        instances = INSTANCES if INSTANCES.exists() else EXAMPLE_INSTANCES
    files[Path(instances)] = GRAPH_INSTANCES
    return files


def load_union(files):
    """TTL をすべて 1 つの rdflib.Graph に読み込む（SHACL 検証・IRI 検査用）。"""
    g = rdflib.Graph()
    for prefix, ns in PREFIXES.items():
        g.bind(prefix, ns)
    for path in files:
        if path.exists():
            g.parse(path, format="turtle")
    return g
