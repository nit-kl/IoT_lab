"""ナレッジグラフの生成・検証・検索のテスト（AWS 不要）。

    python -m unittest discover -s tests -v

わざと壊したグラフを SHACL が違反として検出できること（ルールが空振りしていないこと）も確かめる。
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import RDF, XSD

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import generate_ttl  # noqa: E402
import query  # noqa: E402
import validate  # noqa: E402
from kgcommon import ID, QUDT, SAREF, SSN, XF  # noqa: E402

SAMPLE_CSV = ROOT.parent / "factory-replay" / "data" / "factory_sample.csv"
API = "https://example-api.invalid/"


def sample_graph():
    return generate_ttl.build_graph(generate_ttl.rows_from_csv(SAMPLE_CSV), API, "test")


class KgTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, g):
        path = Path(self.tmp.name) / "instances.ttl"
        generate_ttl.write_turtle(g, path)
        return path

    def violations(self, g):
        conforms, problems, *_ = validate.run(self.write(g))
        return conforms, [m for _, m in problems]

    def assertViolation(self, g, fragment):
        conforms, messages = self.violations(g)
        self.assertFalse(conforms, "違反が検出されなかった")
        self.assertTrue(any(fragment in m for m in messages), f"「{fragment}」を含む違反がない: {messages}")


class GenerateTest(KgTestCase):
    def test_sample_counts(self):
        g = sample_graph()
        self.assertEqual(len(set(g.subjects(XF.equipmentId, None))), 5)
        self.assertEqual(len(set(g.subjects(XF.metricName, None))), 15)
        self.assertEqual(len(set(g.subjects(XF.lineId, None))), 1)

    def test_endpoints_use_api_url(self):
        g = sample_graph()
        prop = ID["property/CNC-01/spindle_load"]
        self.assertEqual(str(g.value(prop, XF.latestEndpoint)), f"{API}latest?equipment=CNC-01&metric=spindle_load")
        self.assertEqual(str(g.value(prop, XF.readingsEndpoint)), f"{API}readings?equipment=CNC-01&metric=spindle_load")

    def test_unknown_catalog_entries_fail(self):
        for row, fragment in (
            (("LINE-A", "ROBOT-01", "status", ""), "設備の種類"),
            (("LINE-A", "CNC-01", "vibration", "mm/s"), "計測項目"),
            (("LINE-A", "CNC-01", "spindle_load", "permille"), "単位"),
        ):
            with self.assertRaises(generate_ttl.CatalogError) as ctx:
                generate_ttl.build_graph({row}, API, "test")
            self.assertIn(fragment, str(ctx.exception))

    def test_multiple_lines(self):
        csv_path = Path(self.tmp.name) / "3lines.csv"
        subprocess.run([sys.executable, str(ROOT.parent / "factory-replay" / "tools" / "generate_csv.py"),
                        "--lines", "3", "--interval", "30m", "--out", str(csv_path)], check=True, capture_output=True)
        g = generate_ttl.build_graph(generate_ttl.rows_from_csv(csv_path), API, "test")
        self.assertEqual(len(set(g.subjects(XF.lineId, None))), 3)
        self.assertEqual(len(set(g.subjects(XF.equipmentId, None))), 15)
        conforms, messages = self.violations(g)
        self.assertTrue(conforms, messages)


class ShapesTest(KgTestCase):
    def test_sample_conforms(self):
        conforms, messages = self.violations(sample_graph())
        self.assertTrue(conforms, messages)

    def test_missing_unit(self):
        g = sample_graph()
        g.remove((ID["property/CNC-01/spindle_load"], QUDT.hasUnit, None))
        self.assertViolation(g, "単位")

    def test_unknown_unit(self):
        g = sample_graph()
        prop = ID["property/CNC-01/spindle_load"]
        g.set((prop, QUDT.hasUnit, URIRef("http://qudt.org/vocab/unit/PERMILLE")))  # units.ttl にない
        self.assertViolation(g, "単位")

    def test_endpoint_not_matching_api(self):
        g = sample_graph()
        prop = ID["property/CNC-01/spindle_load"]
        g.set((prop, XF.latestEndpoint, URIRef("https://old-api.invalid/latest?equipment=CNC-01&metric=spindle_load")))
        self.assertViolation(g, "URL の更新漏れ")

    def test_status_without_code_list(self):
        g = sample_graph()
        g.remove((ID["property/CNC-01/status"], XF.valueCodeList, None))
        self.assertViolation(g, "コード表")

    def test_inconsistent_property_link(self):
        g = sample_graph()
        g.remove((ID["equipment/CNC-01"], SSN.hasProperty, ID["property/CNC-01/spindle_load"]))
        self.assertViolation(g, "一致しない")

    def test_equipment_outside_line(self):
        g = sample_graph()
        g.remove((None, SAREF.consistsOf, ID["equipment/CNC-01"]))
        self.assertViolation(g, "ちょうど 1 つのライン")

    def test_missing_japanese_label(self):
        g = sample_graph()
        eq = ID["equipment/CNC-01"]
        for label in [o for o in g.objects(eq, URIRef("http://www.w3.org/2000/01/rdf-schema#label"))
                      if o.language == "ja"]:
            g.remove((eq, None, label))
        self.assertViolation(g, "日本語のラベル")

    def test_bad_equipment_id(self):
        g = sample_graph()
        g.set((ID["equipment/CNC-01"], XF.equipmentId, Literal("cnc 01")))
        self.assertViolation(g, "設備 ID")

    def test_equipment_without_type(self):
        g = sample_graph()
        eq = ID["equipment/ENV-01"]
        g.remove((eq, RDF.type, SAREF.Device))
        conforms, _ = self.violations(g)
        self.assertFalse(conforms)

    def test_missing_placement_is_warning(self):
        # layout.ttl はサンプルの 5 設備だけ。3 ライン（15 設備）では残りが警告になるが、適合のまま
        csv_path = Path(self.tmp.name) / "3lines.csv"
        subprocess.run([sys.executable, str(ROOT.parent / "factory-replay" / "tools" / "generate_csv.py"),
                        "--lines", "3", "--interval", "30m", "--out", str(csv_path)], check=True, capture_output=True)
        g = generate_ttl.build_graph(generate_ttl.rows_from_csv(csv_path), API, "test")
        conforms, problems, _, _, warnings = validate.run(self.write(g))
        self.assertTrue(conforms, problems)
        self.assertEqual(len(warnings), 10)
        self.assertTrue(all("配置" in m for _, m in warnings), warnings)
        _, _, _, _, sample_warnings = validate.run(self.write(sample_graph()))
        self.assertEqual(sample_warnings, [])

    def test_bad_placement(self):
        g = sample_graph()
        placement = BNode()
        g.add((ID["equipment/CNC-01"], XF.placement, placement))  # layout.ttl の配置に加えて 2 つ目
        g.add((placement, RDF.type, XF.Placement))
        for p, v in ((XF.x, "1.0"), (XF.y, "1.0"), (XF.width, "-2.0"), (XF.depth, "1.0"), (XF.height, "1.0")):
            g.add((placement, p, Literal(v, datatype=XSD.decimal)))
        conforms, messages = self.violations(g)
        self.assertFalse(conforms)
        self.assertTrue(any("1 つまで" in m for m in messages), messages)
        self.assertTrue(any("幅" in m for m in messages), messages)


class QueryTest(KgTestCase):
    def test_queries_return_expected_rows(self):
        store, _ = query.open_store(self.write(sample_graph()))
        expected = {"01-hierarchy.rq": 5, "02-line-a-properties.rq": 15, "03-status-codes.rq": 6,
                    "04-devices-by-standard-class.rq": 5, "05-api-templates.rq": 11}
        for name, rows in expected.items():
            _, result = query.run(store, (ROOT / "queries" / name).read_text(encoding="utf-8"))
            self.assertEqual(len(result), rows, name)

    def test_subclass_query_finds_production_equipment_as_device(self):
        store, _ = query.open_store(self.write(sample_graph()))
        _, rows = query.run(store, (ROOT / "queries" / "04-devices-by-standard-class.rq").read_text(encoding="utf-8"))
        classes = {r[0]: r[1] for r in rows}
        self.assertEqual(classes["CNC-01"], "s4inma:ProductionEquipment")
        self.assertEqual(classes["ENV-01"], "saref:Device")


if __name__ == "__main__":
    unittest.main()
