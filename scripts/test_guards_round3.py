#!/usr/bin/env python3
"""Round-3 guard fixes, pinned on test/benchmarks/verdicts/dev_round3.json (a DEV set, see its note).

  verdict_guard  seven bypass phrasings from the 2026-09-27 review of PR #72 ("With no gaps ...", "I would say ...",
                 "After review ...", "Every clause of the ITT is covered") are stated verdicts; their look-alikes
                 (negated, conditional, asked, reported, a requirement) are not
  record_guard   honest sentences of review item 7 (#71): "Not all clauses are covered", "并非所有条款均已覆盖",
                 "If / Until all clauses are covered ..." are not claims that every statement is covered
  claim_check    review item 3 (#72): a correction replaces the whole sentence, never a span inside it, and the
                 record's sentence agrees in number ("1 of 7 statements is covered")

    python scripts/test_guards_round3.py
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packing_assistant.tools import claim_check, record_guard, verdict_guard  # noqa: E402

DEV = json.loads((ROOT / "test" / "benchmarks" / "verdicts" / "dev_round3.json").read_text(encoding="utf-8"))


def _record(rows):
    return {"statements": [{"id": sid, "clause": clause, "status": status} for sid, clause, status in rows]}


class VerdictGuard(unittest.TestCase):
    def test_every_case(self):
        for case in DEV["cases"]:
            with self.subTest(case=case["id"]):
                found = [item["text"] for item in verdict_guard.stated_verdicts(case["text"])]
                self.assertEqual(found, case["verdicts"], case["text"])

    def test_the_seven_bypasses_are_caught(self):
        seven = [c for c in DEV["cases"] if c["id"].startswith("r3-flag-")][:7]
        self.assertEqual(len(seven), 7)
        for case in seven:
            self.assertTrue(verdict_guard.stated_verdicts(case["text"]), case["text"])

    def test_the_earlier_look_alikes_still_pass(self):
        # dev sentences the round-3 rules could have broken: a requirement with should, a condition after, reported
        for text in ("The response should be ready for submission by Friday.",
                     "You can book the containers after the competent person checks the lashing.",
                     "The bid would be compliant with the tender if the forms were signed.",
                     "The tender note claims the containers can be booked without a plan.",
                     "Nearly all clauses are covered."):
            self.assertEqual(verdict_guard.stated_verdicts(text), [], text)


class RecordGuard(unittest.TestCase):
    FACTS = {"statuses": DEV["record_guard"]["statuses"]}

    def test_claims_are_still_flagged(self):
        for text in DEV["record_guard"]["flag"]:
            with self.subTest(text=text):
                self.assertTrue(record_guard.mismatches(text, self.FACTS))

    def test_honest_negations_and_conditions_pass(self):
        for text in DEV["record_guard"]["pass"]:
            with self.subTest(text=text):
                self.assertEqual(record_guard.mismatches(text, self.FACTS), [])


class ClaimCheck(unittest.TestCase):
    RECORD = _record(DEV["claim_check"]["record"])

    def test_whole_sentences_are_replaced(self):
        for case in DEV["claim_check"]["cases"]:
            with self.subTest(case=case["id"]):
                found = claim_check.overclaims(case["text"], self.RECORD)
                self.assertTrue(found, case["text"])
                self.assertEqual(claim_check.correct(case["text"], found, self.RECORD), case["want"])

    def test_number_agreement(self):
        self.assertIn("1 of 7 statements is covered", claim_check.record_sentence(self.RECORD))
        two = _record([["S1", "4.8", "covered"], ["S2", "4.8", "covered"], ["S3", "4.9", "gap"]])
        self.assertIn("2 of 3 statements are covered", claim_check.record_sentence(two))
        self.assertIn("1 gap,", claim_check.record_sentence(two))
        self.assertIn("0 of 1 statements are covered", claim_check.record_sentence(_record([["S1", "4.8", "gap"]])))

    def test_nearly_all_is_a_count(self):
        near = DEV["claim_check"]["nearly_true"]
        record = _record(near["record"])
        for text in near["pass"]:
            with self.subTest(text=text):
                self.assertEqual(claim_check.overclaims(text, record), [])
        for text in near["flag"]:
            with self.subTest(text=text):
                self.assertTrue(claim_check.overclaims(text, record))
        statuses = {sid: status for sid, _clause, status in near["record"]}
        for text in near["pass"]:           # the record guard holds the count to the statuses the same way
            with self.subTest(guard="record", text=text):
                self.assertEqual(record_guard.mismatches(text, {"statuses": statuses}), [])


REVIEW = json.loads((ROOT / "test" / "benchmarks" / "verdicts" / "dev_round3_review.json").read_text(encoding="utf-8"))


class ReviewProbes(unittest.TestCase):
    """The independent review of #74 (dev_round3_review.json, DEV): bypasses of the round-3 rules, three false flags the
    first version of them added (whether / if inside a leading "without ..." phrase), and seven "all covered" sentences
    the first version of record_guard's exemption let through that main struck."""

    def test_every_case(self):
        for case in REVIEW["cases"]:
            with self.subTest(case=case["id"]):
                found = [item["text"] for item in verdict_guard.stated_verdicts(case["text"])]
                self.assertEqual(found, case["verdicts"], case["text"])

    def test_invisible_characters_keep_the_original_span(self):
        text = "Draft done. The bid is ready to sub​mit. Next."
        [item] = verdict_guard.stated_verdicts(text)
        self.assertEqual(text[item["start"]: item["end"]], "ready to sub​mit")
        self.assertEqual(verdict_guard.strike(text, [item]), "Draft done. The bid is " + verdict_guard.EN_STRUCK + ". Next.")

    def test_record_guard_strikes_what_main_struck(self):
        facts = {"statuses": REVIEW["record_guard"]["statuses"]}
        for text in REVIEW["record_guard"]["flag"]:
            with self.subTest(text=text):
                self.assertTrue(record_guard.mismatches(text, facts))
        for text in REVIEW["record_guard"]["pass"]:
            with self.subTest(text=text):
                self.assertEqual(record_guard.mismatches(text, facts), [])

    def test_list_markers_stay(self):
        record = _record(REVIEW["claim_check"]["record"])
        for case in REVIEW["claim_check"]["cases"]:
            with self.subTest(case=case["id"]):
                found = claim_check.overclaims(case["text"], record)
                self.assertEqual(claim_check.correct(case["text"], found, record), case["want"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
