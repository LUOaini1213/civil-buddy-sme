"""Offline language parity: English commands retain the explicit proposal boundary."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from packing_assistant.logistics.agent import execute, operation, propose_command, apply_proposal
from packing_assistant.logistics.intake import parse_document


class LanguageTests(unittest.TestCase):
    def setUp(self):
        self.document = parse_document((
            "package_id,material_id,name,package_count,quantity,units_per_package,unit,"
            "package length mm,package width mm,package height mm,net weight per package kg,gross weight per package kg\n"
            "BOX-A,MAT-A,原始材料,2,10,5,PCS,1200,800,100,100,110\n"
        ).encode(), "双语样例.csv", ocr_backend="none")
        self.context = {"document": self.document, "locale": "en", "project": {"can_undo": True}}
        self.row = self.document["rows"][0]["id"]

    def test_english_summary_retains_original_data_and_counts(self):
        before = deepcopy(self.context)
        result = execute(self.context, operation("Summarise package totals"), {}, "Summarise package totals")
        self.assertTrue(result["ok"])
        self.assertIn("Packages: 2", result["reply"])
        self.assertIn("gross mass: 220 kg", result["reply"])
        self.assertEqual(result["row_catalog"][0]["name"], "原始材料")
        self.assertEqual(self.context, before)
        self.assertNotIn("logistics_proposal", result)

    def test_both_english_forms_create_equivalent_unapplied_proposals(self):
        before = deepcopy(self.document)
        for command in [f"change {self.row} gross mass to 120 kg", f"Change the gross mass of {self.row} to 120 kg"]:
            self.assertEqual(operation(command), "logistics_propose")
            result = execute(self.context, "logistics_propose", {}, command)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["logistics_proposal"]["changes"][0]["after"], 120)
            self.assertIn("ledger is unchanged", result["reply"])
            self.assertEqual(self.document, before)
            applied = apply_proposal(self.document, result["logistics_proposal"])
            self.assertEqual(applied["rows"][0]["gross_kg"], 120)
            self.assertEqual(applied["source"], before["source"])

    def test_bad_units_ambiguous_changes_and_unknown_rows_fail_closed(self):
        before = deepcopy(self.document)
        for command in [f"set {self.row} gross mass to 12 m", "change all weights to 120 kg", "set R99999 package count to 3", f"change {self.row} quantity to 12 boxes"]:
            result = execute(self.context, "logistics_propose", {}, command)
            self.assertFalse(result["ok"], command)
            self.assertNotIn("logistics_proposal", result)
            self.assertEqual(self.document, before)

    def test_english_undo_is_only_an_explicit_proposal(self):
        self.assertEqual(operation("Undo the last change"), "logistics_undo")
        result = execute(self.context, "logistics_undo", {}, "Undo the last change")
        self.assertEqual(result["logistics_action"], "undo")
        self.assertIn("has not been applied", result["reply"])
        self.assertFalse(execute(self.context, "logistics_undo", {}, "Summarise the ledger")["ok"])
        self.assertEqual(operation("Check missing quantities"), "logistics_audit")

    def test_explicit_english_units_and_unknown_values_preserve_semantics(self):
        proposal = propose_command(self.document, f"set {self.row} package count to 3 boxes")
        self.assertEqual(proposal["changes"][0]["after"], 3)
        proposal = propose_command(self.document, f"set {self.row} gross mass to unknown")
        self.assertEqual(proposal["changes"][0]["after"], "UNSPECIFIED")


if __name__ == "__main__":
    unittest.main()
