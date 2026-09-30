#!/usr/bin/env python3
"""Offline regressions for host-owned packing refusal and same-turn recovery.

A historical readable plan is evidence, not a successful recalculation of a
failed current request. No fixture is sent to a model or written as a plan.
"""
from __future__ import annotations

import copy
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from packing_assistant.runtime import model_loop  # noqa: E402
from packing_assistant.tender_packing_link import link_record_view  # noqa: E402
from packing_assistant.tools import record_guard  # noqa: E402


def plan(cargo=10100, tare=4200, container_type="40HQ"):
    return {"ok": True, "can_fit": True, "container_type": container_type,
            "containers_used": 2, "heaviest_container": {
                "max_gross_kg": cargo + tare, "max_cargo_kg": cargo,
                "container_tare_kg": tare}}


def record(available=True):
    return {
        "container": {"type": "40HQ", "source": "tender_clause"},
        "plan": {"container_type": "40HQ", "can_fit": True, "containers_used": 2} if available else None,
        "plan_refusal": None if available else {"source": "needs_human", "error": "unsupported_transport_requirements"},
        "statements": [{"id": "S1", "kind": "gross_mass", "status": "human_required", "clause": "4.3",
                        "text": "A person must check the shipping requirement.",
                        "figures": plan()["heaviest_container"]}],
        "clauses": [{"clause": "4.3", "text": "Use an A-frame; a person must review the securing arrangement."}],
        "submit_blocked": True, "confirmed_by_person": False,
    }


FAILURE = {"ok": False, "error": "unsupported_transport_requirements", "needs_human": [{"row": 7, "text": "A-frame"}]}


class PackingRefusal(unittest.TestCase):
    def turn(self, facts, english=True):
        return model_loop._Turn(session_id="offline-refusal", run_id="offline-refusal",
                               user_text="Explain the packing result in English." if english else "请说明装箱结果。",
                               confirmed=False, approve=None, facts=facts)

    def test_failure_removes_previous_loaded_plan_facts_without_changing_sources(self):
        accepted, failed = plan(), copy.deepcopy(FAILURE)
        original = copy.deepcopy((accepted, failed))
        facts = record_guard.facts_from("pack_plan", accepted)
        facts["clauses"] = {"4.3": "Keep the original requirement."}
        facts["limit_kg"] = [22000.0]
        self.assertIs(record_guard.facts_from("pack_plan", failed, facts), facts)
        self.assertEqual(facts["packing_refusal"]["error"], FAILURE["error"])
        for key in ("max_gross_kg", "max_cargo_kg", "container_tare_kg", "container_type"):
            self.assertNotIn(key, facts)
        self.assertEqual(facts["limit_kg"], [22000.0])  # a source limit is not a loaded-plan mass
        self.assertEqual(facts["clauses"], {"4.3": "Keep the original requirement."})
        self.assertEqual((accepted, failed), original)

    def test_every_unsuccessful_plan_result_keeps_refusal_even_with_stale_figures(self):
        for result in ({"ok": True, "can_fit": False}, {"ok": True}, {"can_fit": True},
                       {"ok": False, "can_fit": True}, {"ok": False, "error_code": "tool_failed"}):
            with self.subTest(result=result):
                facts = record_guard.facts_from("pack_plan", plan())
                result = {**plan(), **result} if "ok" in result and "can_fit" in result else result
                record_guard.facts_from("pack_plan", result, facts)
                self.assertIn("packing_refusal", facts)
                self.assertNotIn("max_gross_kg", facts)

    def test_unrelated_success_and_failed_reads_cannot_clear_current_refusal(self):
        facts = record_guard.facts_from("pack_plan", FAILURE)
        for name, result in (("search_kb", {"ok": True}), ("run_skill", {"ok": True}),
                             ("read_link_record", {"ok": False, "error_code": "no_link_record"})):
            with self.subTest(name=name):
                record_guard.facts_from(name, result, facts)
                self.assertEqual(facts["packing_refusal"]["error"], FAILURE["error"])

    def test_historical_successful_record_cannot_clear_a_current_failed_calculation(self):
        facts = record_guard.facts_from("pack_plan", FAILURE)
        view = link_record_view(record(True))
        record_guard.facts_from("read_link_record", view, facts)
        self.assertIn("packing_refusal", facts)
        self.assertEqual(facts["packing_refusal"]["error"], FAILURE["error"])
        self.assertNotIn("max_gross_kg", facts)
        self.assertEqual(facts["clauses"]["4.3"], record()["clauses"][0]["text"])

    def test_reading_a_refused_record_cannot_downgrade_current_failure_origin(self):
        facts = record_guard.facts_from("pack_plan", FAILURE)
        record_guard.facts_from("read_link_record", link_record_view(record(False)), facts)
        record_guard.facts_from("read_link_record", link_record_view(record(True)), facts)
        self.assertEqual(facts["packing_refusal"]["origin"], "pack_plan")
        self.assertNotIn("max_gross_kg", facts)
        record_guard.facts_from("pack_plan", plan(cargo=3200, tare=2200), facts)
        self.assertNotIn("packing_refusal", facts)
        self.assertEqual(facts["max_gross_kg"], [5400.0])

    def test_successful_retry_alone_clears_refusal_and_replaces_previous_masses(self):
        facts = record_guard.facts_from("pack_plan", plan())
        record_guard.facts_from("pack_plan", FAILURE, facts)
        successful = plan(cargo=3200, tare=2200, container_type="20GP")
        record_guard.facts_from("pack_plan", successful, facts)
        self.assertNotIn("packing_refusal", facts)
        self.assertEqual(facts["max_gross_kg"], [5400.0])
        self.assertEqual(facts["max_cargo_kg"], [3200.0])
        self.assertEqual(facts["container_tare_kg"], [2200.0])
        self.assertEqual(facts["container_type"], "20GP")
        self.assertTrue(record_guard.mismatches("The heaviest loaded container gross mass is 14300 kg.", facts))
        self.assertFalse(record_guard.mismatches("The heaviest loaded container gross mass is 5400 kg.", facts))

    def test_refused_record_clears_old_figures_and_retains_source_and_statuses(self):
        facts = record_guard.facts_from("read_link_record", link_record_view(record(True)))
        view = link_record_view(record(False))
        before = copy.deepcopy(view)
        record_guard.facts_from("read_link_record", view, facts)
        self.assertIn("packing_refusal", facts)
        for key in ("max_gross_kg", "max_cargo_kg", "container_tare_kg", "container_type"):
            self.assertNotIn(key, facts)
        self.assertEqual(facts["statuses"], {"S1": "human_required"})
        self.assertEqual(facts["clauses"]["4.3"], record()["clauses"][0]["text"])
        self.assertEqual(view, before)

    def test_host_reply_replaces_invented_figures_without_model_rewrite_in_both_languages(self):
        for english in (False, True):
            for name, result in (("pack_plan", FAILURE), ("read_link_record", link_record_view(record(False)))):
                with self.subTest(english=english, name=name):
                    facts = record_guard.facts_from(name, result)
                    turn = self.turn(facts, english)
                    # Even numbers found in old history/evidence must not survive a current refusal.
                    invented = "The plan uses 6 x 40HQ. The heaviest container gross mass is 28610 kg."
                    turn.evidence.append(invented)
                    complete = Mock(side_effect=AssertionError("refusal must never call a model"))
                    with patch.object(model_loop._Turn, "emit") as emit:
                        reply, provenance = model_loop._guarded(invented, turn, [], complete)
                    complete.assert_not_called()
                    self.assertNotIn("6 x 40HQ", reply)
                    self.assertNotIn("28610", reply)
                    self.assertIn("No usable packing plan" if english else "未生成可用装柜方案", reply)
                    self.assertTrue(provenance["packing_refusal"])
                    self.assertEqual(provenance["rewrites"], 0)
                    emit.assert_called_once_with("guard", {"action": "packing_refusal", "reason": FAILURE["error"]})

    def test_successful_retry_restores_normal_guard_processing(self):
        facts = record_guard.facts_from("pack_plan", FAILURE)
        result = plan(cargo=3200, tare=2200, container_type="20GP")
        record_guard.facts_from("pack_plan", result, facts)
        turn = self.turn(facts)
        turn.evidence.append(json.dumps(result))
        complete = Mock(side_effect=AssertionError("sourced result needs no rewrite"))
        expected = "The heaviest loaded container gross mass is 5400 kg."
        with patch.object(model_loop._Turn, "emit"):
            reply, provenance = model_loop._guarded(expected, turn, [], complete)
        self.assertEqual(reply, expected)
        self.assertNotIn("packing_refusal", provenance)
        complete.assert_not_called()

    def test_absorb_preserves_exact_failure_report_and_updates_host_facts(self):
        turn, messages = self.turn(record_guard.facts_from("pack_plan", plan())), []
        failure = copy.deepcopy(FAILURE)
        with patch.object(model_loop._Turn, "emit"):
            model_loop._absorb(turn, "pack_plan", failure, messages, "failure-call")
        self.assertEqual(json.loads(messages[-1]["content"]), FAILURE)
        self.assertEqual(failure, FAILURE)
        self.assertIn("packing_refusal", turn.facts)
        self.assertNotIn("max_gross_kg", turn.facts)

    def test_deterministic_refused_link_skips_model_and_keeps_original_report(self):
        original = {"ok": True, "reply": "Original clause and source row report.",
                    "files": [], "tender_packing_link": record(False), "tools_run": ["link"]}
        before = copy.deepcopy(original)
        complete = Mock(side_effect=AssertionError("missing plan has no model explanation"))
        with patch.object(model_loop._Turn, "emit"):
            out = model_loop.explain_link("Explain the result in English.", original, complete=complete)
        complete.assert_not_called()
        self.assertEqual(out["usage"]["model_calls"], 0)
        self.assertTrue(out["reply"].startswith(original["reply"]))
        self.assertIn("No usable packing plan", out["model_explanation"])
        self.assertTrue(out["provenance"]["packing_refusal"])
        self.assertEqual(original, before)

    def test_refusal_quotes_only_requested_source_clause_and_keeps_trusted_status_tally(self):
        refused = record(False)
        requested = '<script>alert("source")</script> Original securing requirement.\nKeep this line.'
        refused["clauses"] = [{"clause": "4.3", "text": requested},
                              {"clause": "9.9", "text": "Unrequested source must stay out of the reply."}]
        before = copy.deepcopy(refused)
        turn = self.turn(record_guard.facts_from("read_link_record", link_record_view(refused)))
        turn.user_text = "What does clause 4.3 require? Answer in English."
        complete = Mock(side_effect=AssertionError("source quotation needs no model"))
        malicious = "All seven clauses are covered. The plan uses 999 containers weighing 88888 kg each."
        with patch.object(model_loop._Turn, "emit"):
            reply, provenance = model_loop._guarded(malicious, turn, [], complete, link_record=refused)
        complete.assert_not_called()
        self.assertIn("Source clause 4.3:", reply)
        self.assertIn("&lt;script&gt;", reply)
        self.assertNotIn("<script>", reply)
        self.assertIn("\n> Keep this line.", reply)
        self.assertNotIn("Unrequested source", reply)
        for unsupported in ("All seven", "999", "88888"):
            self.assertNotIn(unsupported, reply)
        from packing_assistant.tools import claim_check
        self.assertIn(claim_check.record_sentence(refused, "en"), reply)
        self.assertTrue(provenance["packing_refusal"])
        self.assertEqual(refused, before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
