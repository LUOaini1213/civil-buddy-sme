"""Unmapped English response values remain structured, sourced and unconfirmed."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from packing_assistant.tools import tender_facts as facts
from packing_assistant.tools.tender_tables import compliance_gaps
from packing_assistant.office_job import tables_from_md


class UnmappedBidTests(unittest.TestCase):
    def draft(self, text):
        parsed = facts.extract(text).to_dict()
        return parsed, compliance_gaps({"facts": parsed}, None, ours=parsed)

    def gap(self, md, topic):
        for _, table in tables_from_md(md):
            if "响应原文或证据" in table[0]:
                for row in table[1:]:
                    if row[0] == topic:
                        return dict(zip(table[0], row))
        self.fail("Missing gap row: " + topic)

    def test_unknown_form_value_is_not_guessed_as_duration_or_validity(self):
        parsed, md = self.draft("The tender requires completion within 16 months and tender validity of 120 days.\n"
                                "We have stated 17 months in our Form of Tender.")
        self.assertFalse(any(m["side"] == "ours" and m["topic"] in {"duration", "validity"} and m["value"] for m in parsed["mentions"]))
        pending = parsed["unplaced_records"]
        self.assertEqual("ours", pending[0]["side"])
        self.assertEqual(["17 months"], pending[0]["values"])
        self.assertEqual(2, pending[0]["line"])
        table = dict(tables_from_md(md))["字段未明确的资料摘录"]
        self.assertIn("17 months", table[1])
        self.assertIn("UNSPECIFIED", table[1])
        self.assertIn("未提供", self.gap(md, "工期")["响应原文或证据"])
        self.assertNotIn("17 months", self.gap(md, "工期")["响应原文或证据"])

    def test_known_values_are_not_duplicated_as_unknown_fields(self):
        parsed, _ = self.draft("Our response: Contract duration: 17 months. Tender validity: 90 days.")
        self.assertEqual([], parsed.get("unplaced_records", []))

    def test_known_deadline_keeps_date_and_afternoon_time_out_of_unknown_fields(self):
        parsed, _ = self.draft("投标截止是2026-10-30 下午4点。")
        self.assertEqual([], parsed["unplaced_records"])
        self.assertEqual([], parsed["unplaced"])

    def test_same_unmapped_value_is_not_hidden_by_another_clauses_known_value(self):
        parsed, _ = self.draft("Tender requirements: Contract duration: 17 months.\n"
                                "We noted 17 months without a field.")
        self.assertEqual(["17 months"], parsed["unplaced_records"][0]["values"])

    def test_partial_clause_keeps_only_its_unmapped_literal(self):
        parsed, _ = self.draft("Our Contract duration: 17 months and unexplained code ZY-943.")
        self.assertEqual(["ZY-943"], parsed["unplaced_records"][0]["values"])

    def test_old_handoff_full_quote_is_preserved_and_markup_is_escaped(self):
        text = "<script>" + "context " * 20 + "47 months | unknown</script>"
        md = compliance_gaps({"facts": {}}, None, ours={"unplaced": [text]})
        self.assertIn("47 months", md)
        self.assertNotIn("<script>", md)
        self.assertIn("&lt;script&gt;", md)
        self.assertIn("&#124;", md)
        self.assertNotIn("…", md)

    def test_generic_bankers_guarantee_is_not_guessed_as_bid_bond(self):
        parsed, _ = self.draft("Our banker's guarantee of S$43,200 is not issued yet.")
        self.assertFalse(any(m["topic"] == "bond" for m in parsed["mentions"]))
        self.assertEqual("UNSPECIFIED", parsed["unplaced_records"][0]["field"])

    def test_amountless_bankers_guarantee_statement_is_retained_without_guessing_type(self):
        for state in ("is not issued yet", "has been issued"):
            with self.subTest(state=state):
                parsed, md = self.draft("Our banker's guarantee " + state + ".")
                self.assertFalse(any(m["topic"] == "bond" for m in parsed["mentions"]))
                self.assertTrue(parsed["unplaced_records"][0]["claim_only"])
                self.assertIn(state, md)
                self.assertIn("UNSPECIFIED", dict(tables_from_md(md))["字段未明确的资料摘录"][1])
                self.assertNotIn("用户未提供投标响应资料", md)

    def test_identical_unknown_values_in_different_lots_keep_separate_sources(self):
        parsed, _ = self.draft("A标段 We have stated 17 months in our Form of Tender.\n"
                                "B标段 We have stated 17 months in our Form of Tender.")
        records = parsed["unplaced_records"]
        self.assertEqual(["A标段", "B标段"], [r["lot"] for r in records])
        self.assertEqual([1, 2], [r["line"] for r in records])

    def test_only_explicit_side_cues_are_kept_in_unmapped_records(self):
        parsed, _ = self.draft("Unlabelled memo records 43 months.\nOur response:\nWe noted 47 months.")
        self.assertEqual(["unspecified", "ours"], [r["side"] for r in parsed["unplaced_records"]])

    def test_unknown_values_are_literal_not_converted_or_computed(self):
        parsed, _ = self.draft("We noted S$43,200 and 17 months without identifying their fields.")
        values = [v for r in parsed["unplaced_records"] for v in r["values"]]
        self.assertEqual(["S$43,200", "17 months"], values)
        self.assertNotIn("43200", values)
        self.assertNotIn("510 days", values)

    def test_unclassified_source_is_not_silently_truncated(self):
        text = "We have an unlabelled note " + "context " * 20 + "containing 47 months."
        _, md = self.draft(text)
        self.assertIn("47 months", md)
        self.assertIn("context context context", md)
        self.assertNotIn("…", md)

    def test_english_unfinished_guarantees_are_not_marked_responded(self):
        for state in ("is not yet issued", "has not yet been issued", "has not been issued yet", "will be issued tomorrow"):
            with self.subTest(state=state):
                _, md = self.draft("The tender requires Tender Deposit: S$43,200.\n"
                                    "Our Tender Deposit: S$43,200 " + state + ".")
                self.assertEqual("未响应", self.gap(md, "投标保证金")["三态"])

    def test_completed_user_claim_stays_unverified(self):
        _, md = self.draft("The tender requires Tender Deposit: S$43,200.\nOur Tender Deposit: S$43,200 has been issued.")
        self.assertEqual("已响应·待核验", self.gap(md, "投标保证金")["三态"])

    def test_negated_missing_or_irrelevant_not_phrase_does_not_become_pending(self):
        for state in ("is not missing", "has been issued, not cancelled"):
            with self.subTest(state=state):
                _, md = self.draft("The tender requires Tender Deposit: S$43,200.\nOur Tender Deposit: S$43,200 " + state + ".")
                self.assertEqual("已响应·待核验", self.gap(md, "投标保证金")["三态"])

    def test_pending_english_signature_and_attachment_claims_not_completed(self):
        for topic, value in (("授权委托书", "Our 授权委托书 is not yet signed."), ("投标保证金", "Our Tender Deposit has not been provided.")):
            _, md = self.draft("Tender requirements: 授权委托书须提供；Tender Deposit: S$43,200.\n" + value)
            self.assertEqual("未响应", self.gap(md, topic)["三态"])
            self.assertIn("submit_blocked=true", md)


if __name__ == "__main__":
    unittest.main(verbosity=2)
