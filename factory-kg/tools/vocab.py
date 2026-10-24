"""参照している標準語彙を取得し、使っている IRI が実在するかを検査して、必要な部分だけを取り込む。

    python tools/vocab.py            # 取得（初回のみ）→ 検査 → ontology/imports.ttl・units.ttl を書き出す
    python tools/vocab.py --check    # 検査だけ（書き出さない）
    python tools/vocab.py --refresh  # 公式から取り直す

検査の対象は ontology/*.ttl（手書き）と、インスタンス（build/instances.ttl、なければ examples/ の例）で使っている
標準語彙の IRI すべて。公式の定義にない IRI があれば失敗する（懸念点 O2）。
取り込むのは使っている語のラベル・説明・上位クラス／上位プロパティだけ（懸念点 O6・O7）。
"""

import argparse
import sys
import urllib.request

import rdflib
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import OWL, RDF, RDFS

from kgcommon import CACHE_DIR, EXAMPLE_INSTANCES, INSTANCES, ONTOLOGY_DIR, PREFIXES, QUDT, SHAPES, UNIT, VOCABULARIES

HAND_WRITTEN = [ONTOLOGY_DIR / "xf.ttl", ONTOLOGY_DIR / "codes.ttl", ONTOLOGY_DIR / "labels-ja.ttl", SHAPES]
IMPORTS = ONTOLOGY_DIR / "imports.ttl"
UNITS = ONTOLOGY_DIR / "units.ttl"
EXTENSIONS = {"turtle": "ttl", "json-ld": "jsonld", "xml": "rdf"}
ACCEPT = {"turtle": "text/turtle", "json-ld": "application/ld+json", "xml": "application/rdf+xml"}

# QUDT に日本語ラベルがない単位の表示名（units.ttl に追加する。QUDT 側にあればそちらを使う）
UNIT_LABELS_JA = {
    "MegaPA": "メガパスカル", "KiloW": "キロワット", "REV-PER-MIN": "毎分回転数", "PERCENT": "パーセント",
    "NUM": "個数", "PPM": "ppm", "M-PER-MIN": "メートル毎分", "UNITLESS": "単位なし", "DEG_C": "セルシウス度", "A": "アンペア",
}
COPY_PREDICATES = [RDF.type, RDFS.label, RDFS.comment, RDFS.subClassOf, RDFS.subPropertyOf, RDFS.domain, RDFS.range]
UNIT_PREDICATES = [RDF.type, RDFS.label, QUDT.symbol, QUDT.ucumCode, QUDT.hasQuantityKind]
LABEL_LANGS = ("en", "ja", None)


def fetch(prefix, refresh):
    namespace, url, fmt = VOCABULARIES[prefix]
    path = CACHE_DIR / f"{prefix}.{EXTENSIONS[fmt]}"
    if refresh or not path.exists():
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"Accept": ACCEPT[fmt], "User-Agent": "factory-kg-vocab/0.1"})
        with urllib.request.urlopen(req, timeout=120) as res:
            path.write_bytes(res.read())
        print(f"取得: {prefix:8} {url}")
    g = Graph()
    g.parse(path, format=fmt)
    return g


def used_iris():
    """手書きの TTL とインスタンスで使っている、標準語彙の IRI を {接頭辞: {IRI}} で返す。"""
    files = HAND_WRITTEN + [INSTANCES if INSTANCES.exists() else EXAMPLE_INSTANCES]
    g = Graph()
    for f in files:
        if f.exists():
            g.parse(f, format="turtle")
    used = {p: set() for p in VOCABULARIES}
    for triple in g:
        for term in triple:
            if not isinstance(term, URIRef):
                continue
            for prefix, (namespace, _, _) in VOCABULARIES.items():
                if str(term).startswith(namespace) and str(term) != namespace:
                    used[prefix].add(term)
    return used


def version_of(g):
    """版の IRI（なければ版の記載の 1 行目を短く）。"""
    for v in g.objects(None, OWL.versionIRI):
        return str(v)
    for v in g.objects(None, OWL.versionInfo):
        return str(v).strip().splitlines()[0][:60]
    return "（版の記載なし）"


def pick_labels(g, s, predicate=RDFS.label):
    """英語・日本語・言語なしのラベルを 1 つずつ。"""
    picked = {}
    for o in g.objects(s, predicate):
        lang = getattr(o, "language", None)
        if lang in LABEL_LANGS and lang not in picked:
            picked[lang] = o
    return list(picked.values())


def copy_term(src, dst, s, include_comment=True):
    for p in COPY_PREDICATES:
        if p == RDFS.comment and not include_comment:
            continue
        objects = pick_labels(src, s, p) if p in (RDFS.label, RDFS.comment) else list(src.objects(s, p))
        for o in objects:
            if isinstance(o, rdflib.BNode):
                continue  # OWL の制約（無名ノード）は取り込まない
            dst.add((s, p, o))


def superclasses(vocab, terms):
    """terms の上位クラス・上位プロパティを、取得済みの語彙の中でたどれるだけたどる。"""
    seen, todo = set(terms), list(terms)
    while todo:
        s = todo.pop()
        for p in (RDFS.subClassOf, RDFS.subPropertyOf):
            for o in vocab.objects(s, p):
                if isinstance(o, URIRef) and o not in seen and (o, None, None) in vocab:
                    seen.add(o)
                    todo.append(o)
    return seen


def write(g, path, header):
    for prefix, ns in PREFIXES.items():
        g.bind(prefix, ns)
    g.bind("owl", OWL)
    path.write_text(header + g.serialize(format="turtle"), encoding="utf-8", newline="\n")


def main():
    ap = argparse.ArgumentParser(description="標準語彙の取得・IRI の実在検査・必要部分の取り込み")
    ap.add_argument("--check", action="store_true", help="検査だけ行い、imports.ttl・units.ttl を書き出さない")
    ap.add_argument("--refresh", action="store_true", help="公式から取り直す")
    args = ap.parse_args()

    used = used_iris()
    vocabs = {p: fetch(p, args.refresh) for p in VOCABULARIES if used[p] or p in ("qudt", "unit")}
    union = Graph()
    for g in vocabs.values():
        union += g

    missing = []
    for prefix, terms in used.items():
        for term in sorted(terms):
            if (term, None, None) not in vocabs[prefix]:
                missing.append(f"{prefix}: {term}")
    for u in sorted(used["unit"]):
        if (u, RDF.type, QUDT.Unit) not in vocabs["unit"]:
            missing.append(f"unit（qudt:Unit ではない）: {u}")

    total = sum(len(t) for t in used.values())
    print(f"使っている標準語彙の IRI: {total} 個")
    for prefix in VOCABULARIES:
        if used[prefix]:
            print(f"  {prefix:8} {len(used[prefix]):3} 個  {version_of(vocabs[prefix])}")
    if missing:
        print("公式の定義にない IRI があります:", file=sys.stderr)
        for m in missing:
            print(f"  {m}", file=sys.stderr)
        sys.exit(1)
    print("すべて公式の定義に存在します")
    if args.check:
        return

    # imports.ttl: 使っている語（単位以外）とその上位クラス／上位プロパティ
    imports = Graph()
    terms = set().union(*(t for p, t in used.items() if p != "unit"))
    for s in sorted(superclasses(union, terms)):
        copy_term(union, imports, s, include_comment=s in terms)
    sources = "\n".join(f"#   {p:8} {VOCABULARIES[p][1]}  {version_of(vocabs[p])}" for p in VOCABULARIES
                        if used[p] and p != "unit")
    write(imports, IMPORTS, (
        "# 生成ファイル（factory-kg/tools/vocab.py）。手で編集しない。\n"
        "# 参照している標準語彙のうち、使っている語のラベル・説明・上位クラス／上位プロパティだけを取り込んだもの。\n"
        "# 出典（利用条件は各語彙に従う。W3C: W3C Software and Document License、SAREF: ETSI、DCMI: CC BY 4.0）:\n"
        f"{sources}\n\n"))

    # units.ttl: 使っている単位の QUDT の定義の抜粋 + 日本語ラベル
    units = Graph()
    for u in sorted(used["unit"]):
        for p in UNIT_PREDICATES:
            objects = pick_labels(vocabs["unit"], u) if p == RDFS.label else list(vocabs["unit"].objects(u, p))
            for o in objects:
                if p == RDF.type and o != QUDT.Unit:
                    continue
                units.add((u, p, o))
        local = str(u)[len(str(UNIT)):]
        if not any(getattr(o, "language", None) == "ja" for o in units.objects(u, RDFS.label)) and local in UNIT_LABELS_JA:
            units.add((u, RDFS.label, Literal(UNIT_LABELS_JA[local], lang="ja")))
    write(units, UNITS, (
        "# 生成ファイル（factory-kg/tools/vocab.py）。手で編集しない。\n"
        f"# QUDT Units Vocabulary（{VOCABULARIES['unit'][1]}、{version_of(vocabs['unit'])}）から、使っている単位だけを抜粋。\n"
        "# QUDT は CC BY 4.0（https://qudt.org/）。日本語ラベル（@ja）のうち QUDT にないものは factory-kg で追加した。\n\n"))

    print(f"書き出し: {IMPORTS.relative_to(IMPORTS.parents[1])}（{len(imports)} トリプル）、"
          f"{UNITS.relative_to(UNITS.parents[1])}（{len(units)} トリプル）")


if __name__ == "__main__":
    main()
