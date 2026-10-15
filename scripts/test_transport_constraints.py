"""Offline synthetic load cases. These are geometry checks, not shipment acceptance."""
from copy import deepcopy
import csv
import io
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
from packing_assistant.logistics import agent, bundle, packing
from packing_assistant.logistics.intake import parse_document
from packing_assistant.logistics.ledger import audit_document, validate_document
from packing_assistant.transport_constraints import normalize


def source(**extra):
    row = dict(package_id="SYN-01", name="Synthetic cargo", package_count=2, quantity=2, unit="pcs",
               length_mm=1000, width_mm=800, height_mm=600, dimension_scope="package",
               net_kg=100, gross_kg=120, weight_scope="package")
    row.update(extra)
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(row))
    writer.writeheader(); writer.writerow(row)
    return stream.getvalue().encode("utf-8-sig")


def project(data):
    return {"confirmed": True, "document": parse_document(data, "synthetic.csv"), "name": "Synthetic", "revision": 1}


class Constraints(unittest.TestCase):
    def test_boolean_header_polarity_is_explicit(self):
        for field, header, value, expected in [
            ("orientation", "this_side_up", "true", "upright"),
            ("stacking", "no_stack", "true", "no_stack"),
            ("stacking", "stackable", "false", "no_stack"),
            ("stacking", "no_stack", "false", "allowed"),
        ]:
            self.assertEqual(normalize(field, value, header), (expected, ""))
        self.assertTrue(normalize("stacking", "sometimes", "stackable")[1])

    def test_source_text_and_location_survive_correction_and_export(self):
        raw = source(upright="true", no_stack="true", handling="keep upright; do not stack")
        doc = project(raw)["document"]
        row = doc["rows"][0]
        self.assertEqual((row["orientation"], row["stacking"]), ("upright", "no_stack"))
        self.assertEqual(row["handling_requirements"], "keep upright; do not stack")
        self.assertEqual(row["evidence"]["stacking"]["raw"], "true")
        self.assertEqual(row["evidence"]["stacking"]["header"], "no_stack")
        self.assertEqual(row["evidence"]["stacking"]["source"]["row"], 2)
        revised = agent.propose_changes(doc, [{"row_id": row["id"], "field": "orientation", "value": "fixed"}], "operator correction")
        self.assertEqual(revised["document"]["rows"][0]["evidence"]["orientation"]["raw"], "true")
        self.assertEqual(doc["rows"][0]["orientation"], "upright")
        exported, _, _ = bundle.export_ledger({"name": "Synthetic", "revision": 1, "document": doc}, "xlsx")
        reread = parse_document(exported, "exchange.xlsx")
        for field in ("orientation", "stacking", "handling_requirements"):
            self.assertEqual(reread["rows"][0][field], row[field])

    def test_conflicting_duplicate_columns_preserve_both_values_and_block(self):
        doc = project(source(orientation="upright", this_side_up="false"))["document"]
        evidence = doc["rows"][0]["evidence"]["orientation"]
        self.assertEqual([a["raw"] for a in evidence["alternatives"]], ["upright", "false"])
        self.assertFalse(audit_document(doc)["ok"])
        self.assertFalse(packing.prepare({"confirmed": True, "document": doc}, "packaged", "40HQ", 1)["ok"])

    def test_unrecognized_or_conflicting_requirement_does_not_reach_solver(self):
        for extra in [dict(handling="keep upright; do not stack; protect against rain"),
                      dict(handling="do not stack", stacking="allowed"), dict(no_stack="sometimes")]:
            cargo = project(source(**extra))
            with patch("packing_assistant.agents.loader.agent_loader", side_effect=AssertionError("must not solve")):
                result = packing.pack(cargo, "packaged", "40HQ", 1)
            self.assertFalse(result["ok"])
            self.assertTrue(result["needs_human"])

    def test_frame_missing_tare_capacity_or_overloaded_does_not_solve(self):
        complete = dict(package_type="A-frame", orientation="upright", net_kg=1000, tare_kg=100, gross_kg=1100, capacity_kg=1200)
        for override, code in [(dict(tare_kg=""), "frame_data_missing"), (dict(capacity_kg=900), "frame_capacity_exceeded"),
                               (dict(gross_kg=1001), "frame_mass_mismatch")]:
            cargo = project(source(**(complete | override)))
            result = packing.prepare(cargo, "packaged", "40HQ", 1)
            self.assertFalse(result["ok"])
            self.assertIn(code, [r["code"] for r in result["needs_human"]])

    def test_frame_and_fragile_boolean_columns_are_not_dropped(self):
        for extra in ({"A-frame": "true"}, {"fragile": "yes"}):
            cargo = project(source(**extra))
            self.assertFalse(packing.prepare(cargo, "packaged", "40HQ", 1)["ok"])
            field = "package_type" if "A-frame" in extra else "handling_requirements"
            self.assertEqual(cargo["document"]["rows"][0]["evidence"][field]["raw"], next(iter(extra.values())))
        self.assertTrue(packing.prepare(project(source(fragile="false")), "packaged", "40HQ", 1)["ok"])

    def test_two_different_real_solver_load_cases(self):
        # Synthetic dimensions and masses are test inputs, not engineering defaults.
        cases = [source(package_count=4, quantity=4, no_stack="true"),
                 source(package_type="A-frame", orientation="upright", no_stack="true", length_mm=2000, width_mm=800,
                        height_mm=1800, net_kg=1000, tare_kg=100, gross_kg=1100, capacity_kg=1200)]
        for raw in cases:
            with self.subTest(raw=raw):
                cargo = project(raw)
                result = packing.run_pack(cargo, "packaged", "40HQ", 1)
                self.assertTrue(result["ok"], result)
                self.assertTrue(result["layout_verified"])
                expected = cargo["document"]["rows"][0]
                layout = result["container_plan"]["layout"]
                self.assertEqual(len(layout), expected["package_count"])
                self.assertTrue(all(item["position"]["z"] == 0 for item in layout))
                self.assertTrue(all(item["size"]["dz"] == expected["height_mm"] for item in layout))
                self.assertEqual(result["constraints"][0]["effective_orientation"], "fixed")
                self.assertIn("securing", result["constraints"][0]["not_checked"])
                tampered = deepcopy(result["container_plan"])
                tampered["layout"][0]["position"]["z"] = 1
                self.assertFalse(packing.verify_packaged_layout(result["boxes"], tampered, "40HQ", 1))

    def test_material_lane_refuses_constraints_before_automatic_boxing(self):
        from packing_assistant.agents.box_scheme import agent_box_scheme, materials_to_passthrough_boxes
        from packing_assistant.agents.material_parser import _normalize_llm_materials
        from packing_assistant.tools.table_mapper import parse_table_rows
        from packing_assistant.tools.pack_ship_solve import rows_blocking_plan
        original = dict(id="SYN-1", name="Synthetic panel", quantity=2, weight_kg=100, length_mm=1000, width_mm=800, height_mm=600,
                        no_stack="true", stackable="false", orientation="upright")
        materials = parse_table_rows([original])["materials"]
        self.assertEqual(materials[0]["no_stack"], "true")
        self.assertEqual(materials[0]["meta"]["handling_source"][0]["raw"], "true")
        normalized = _normalize_llm_materials(materials)
        self.assertEqual(normalized[0]["orientation"], "upright")
        self.assertIn("unsupported_transport_requirements", [r["reason"] for r in rows_blocking_plan(normalized)])
        self.assertEqual(agent_box_scheme({"materials": normalized})["boxes"], [])
        with self.assertRaises(ValueError):
            materials_to_passthrough_boxes(normalized)

    def test_legacy_synonym_cannot_overwrite_restrictive_source(self):
        from packing_assistant.tools.table_mapper import parse_table_rows
        from packing_assistant.tools.pack_ship_solve import rows_blocking_plan
        parsed = parse_table_rows([dict(name="Synthetic panel", quantity=1, weight_kg=10, length_mm=1000, width_mm=400,
                                      height_mm=100, no_stack="true", **{"no stack": "false"})])
        self.assertEqual(len(parsed["materials"][0]["meta"]["handling_source"]), 2)
        self.assertIn("unsupported_transport_requirements", [r["reason"] for r in rows_blocking_plan(parsed["materials"])])

    def test_old_optional_fields_and_json_aliases(self):
        doc = project(source())["document"]
        for field in ("orientation", "stacking", "handling_requirements", "tare_kg", "capacity_kg"):
            doc["rows"][0].pop(field)
        original = deepcopy(doc)
        self.assertTrue(packing.prepare({"confirmed": True, "document": doc}, "packaged", "40HQ", 1)["ok"])
        self.assertEqual(doc, original)
        doc["rows"][0]["no_stack"] = "sometimes"
        self.assertFalse(audit_document(doc)["ok"])


if __name__ == "__main__":
    unittest.main()
