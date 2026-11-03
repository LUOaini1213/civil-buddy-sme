"""Real English request routing, uploads, downloads and read-only/HITL boundaries."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import test_hazard_bim_http_acceptance as hb
from packing_assistant.office_job import tables_from_md

ACK = "我明白，将由持证人员签认"


class EnglishRequestHTTP(unittest.TestCase):
    setUp = hb.HazardBIMHttpAcceptance.setUp
    post = hb.HazardBIMHttpAcceptance.post
    markdown = hb.HazardBIMHttpAcceptance.markdown
    spreadsheet_rows = hb.HazardBIMHttpAcceptance.spreadsheet_rows
    word_table_rows = hb.HazardBIMHttpAcceptance.word_table_rows
    copies = hb.HazardBIMHttpAcceptance.copies

    def upload_example(self, filename, source):
        data = (ROOT / "examples" / "facade-demo" / source).read_bytes()
        response = self.client.post("/api/upload", data={"session_id": self.sid}, files={"files": (filename, data)})
        self.assertEqual(200, response.status_code, response.text)
        identifier = response.json()["files"][0]["id"]
        original, _ = hb.flow.uploads.upload_file(self.sid, identifier)
        return identifier, original, data

    def test_colloquial_english_draft_routes_and_delivers_literal_office_fields(self):
        md, done = self.markdown("Please do up a site diary. Date: 2026-11-04; Weather: cloudy; Workers: 11 installers; Location: Block Z.")
        title, table = next((title, table) for title, table in tables_from_md(md) if "Workers" in str(table))
        self.copies(done, md, title)
        self.assertTrue(any("11 installers" in cell for row in table for cell in row))
        self.assertTrue(any("cloudy" in cell for row in table for cell in row))
        self.assertEqual("run", done["intent"])
        self.assertTrue(any("pm-daily" in item["path"] for item in done["deliverables"]))
        self.no_model.assert_not_called()

    def test_high_risk_english_request_keeps_exact_confirmation_gate(self):
        for fields in ({}, {"confirm_ok": True}, {"confirm_text": "我明白"}):
            self.sid = "en-hazard-" + uuid4().hex[:16]
            done, _ = self.post("Need a safety briefing about panel handling.", **fields)
            self.assertEqual("run", done["intent"])
            self.assertTrue(done["hitl_pending"], done)
            self.assertFalse(done["wrote"])
            self.assertEqual([], done["deliverables"])
        self.sid = "en-hazard-ok-" + uuid4().hex[:16]
        self.markdown("Do the toolbox briefing for panel handling.", confirm_text=ACK)
        self.no_model.assert_not_called()

    def test_convenience_link_request_computes_selected_inputs_but_remains_a_draft(self):
        tender, original_t, bytes_t = self.upload_example("north_itt.md", "facade_itt_doc.md")
        panels, original_p, bytes_p = self.upload_example("glazing_panels.xlsx", "facade_panels.xlsx")
        done, _ = self.post("When you get a chance, check glazing_panels.xlsx against north_itt.md.",
                            attachments=[tender, panels], locale="en")
        self.assertTrue(done["ok"] and done["wrote"], done)
        self.assertEqual("run", done["intent"])
        self.assertEqual("bid-parse", done["skill"])
        self.assertTrue(done["submit_blocked"])
        record = next(item for item in done["deliverables"] if item["name"] == "pack-plan.json")
        plan = self.client.get("/api/file", params={"path": record["path"]}).json()
        self.assertEqual(6, plan["containers_used"])
        self.assertEqual("40HQ", plan["container_type"])
        self.assertEqual(bytes_t, original_t.read_bytes())
        self.assertEqual(bytes_p, original_p.read_bytes())
        self.no_model.assert_not_called()

    def test_convenience_request_cannot_bypass_high_risk_confirmation(self):
        request = "At your convenience, could you prepare the lifting plan for panel installation?"
        for fields in ({}, {"confirm_ok": True}, {"confirm_text": "我明白"}):
            with self.subTest(fields=fields):
                self.sid = "courtesy-gate-" + uuid4().hex[:16]
                done, _ = self.post(request, **fields)
                self.assertEqual("run", done["intent"])
                self.assertTrue(done["hitl_pending"])
                self.assertFalse(done["wrote"])
                self.assertEqual([], done["deliverables"])
                audit = self.client.get("/api/harness/audit/" + self.sid).json()
                self.assertEqual(0, audit["counts"]["tools"])
        self.sid = "convenience-ok-" + uuid4().hex[:16]
        md, done = self.markdown(request, confirm_text=ACK)
        self.assertTrue(done["submit_blocked"])
        self.no_model.assert_not_called()

    def test_convenience_explanation_reminder_and_reported_speech_do_not_write(self):
        tender, original_t, bytes_t = self.upload_example("north_itt.md", "facade_itt_doc.md")
        panels, original_p, bytes_p = self.upload_example("glazing_panels.xlsx", "facade_panels.xlsx")
        for request in (
            "When you get a chance, explain how to check glazing_panels.xlsx against north_itt.md.",
            "When you get a chance, remind me to check glazing_panels.xlsx against north_itt.md.",
            "The PM said that when you get a chance, check glazing_panels.xlsx against north_itt.md.",
            "When you get a chance, check glazing_panels.xlsx against north_itt.md; no files.",
        ):
            with self.subTest(request=request):
                done, _ = self.post(request, attachments=[tender, panels])
                self.assertEqual("chat", done["intent"])
                self.assertFalse(done["wrote"])
                self.assertEqual([], done["deliverables"])
                audit = self.client.get("/api/harness/audit/" + self.sid).json()
                self.assertEqual(0, audit["counts"]["tools"])
                self.assertEqual(0, audit["counts"]["writes"])
                self.assertFalse((self.root / self.sid / "execution-inputs").exists())
                self.assertEqual(bytes_t, original_t.read_bytes())
                self.assertEqual(bytes_p, original_p.read_bytes())
        self.no_model.assert_not_called()

    def test_named_itt_operation_reaches_actual_uploaded_source_and_preserves_original(self):
        identifier, original, data = self.upload_example("north_itt.md", "facade_itt_doc.md")
        md, done = self.markdown("Extract the logistics clauses from north_itt.md.", attachments=[identifier])
        self.assertEqual("run", done["intent"])
        self.assertTrue(any(item["name"] == "tender.parse.md" for item in done["deliverables"]))
        self.assertIn("40HQ", md)
        self.assertEqual(data, original.read_bytes())
        self.no_model.assert_not_called()

    def test_physical_loading_request_runs_real_plan_on_uploaded_panels(self):
        identifier, original, data = self.upload_example("glazing_panels.xlsx", "facade_panels.xlsx")
        done, _ = self.post("Load glazing_panels.xlsx into 40HQ.", attachments=[identifier], confirm_text=ACK)
        self.assertEqual("run", done["intent"])
        self.assertTrue(done["wrote"], done)
        self.assertEqual("pack-ship", done["skill"])
        self.assertTrue(any(item["name"] == "pack-plan.md" for item in done["deliverables"]))
        report = next(item for item in done["deliverables"] if item["name"] == "pack-plan.md")
        text = self.client.get("/api/file", params={"path": report["path"]}).content.decode("utf-8")
        self.assertIn("40HQ", text)
        self.assertNotIn("本轮未接通", done["text"])
        plan_file = next(item for item in done["deliverables"] if item["name"] == "pack-plan.json")
        plan = self.client.get("/api/file", params={"path": plan_file["path"]}).json()
        self.assertEqual(6, plan["containers_used"])
        self.assertEqual("40HQ", plan["container_type"])
        self.assertTrue(plan["can_fit"])
        self.assertTrue(done["submit_blocked"])
        self.assertEqual(data, original.read_bytes())
        from hashlib import sha256
        audit = self.client.get("/api/harness/audit/" + self.sid).json()
        manifest = audit["runs"][-1]["input_files"]
        self.assertEqual([identifier], [r["id"] for r in manifest])
        self.assertEqual(sha256(data).hexdigest(), manifest[0]["sha256"])
        copied = list((self.root / self.sid / "execution-inputs").rglob("glazing_panels.xlsx"))
        self.assertEqual(1, len(copied))
        self.assertEqual(data, copied[0].read_bytes())
        self.no_model.assert_not_called()

    def test_named_inputs_do_not_override_read_only_intent_on_selected_post(self):
        identifier, original, data = self.upload_example("glazing_panels.xlsx", "facade_panels.xlsx")
        for request in (
            "How should I load glazing_panels.xlsx? Explain only.",
            "Load glazing_panels.xlsx into 40HQ; do not write any files.",
            'Explain the instruction "Load glazing_panels.xlsx into 40HQ".',
        ):
            done, _ = self.post(request, expert_ids=["pack-ship"], attachments=[identifier], confirm_text=ACK)
            self.assertEqual("chat", done["intent"], done)
            self.assertFalse(done["wrote"], done)
            self.assertEqual([], done["deliverables"])
            self.assertEqual(data, original.read_bytes())
        self.assertFalse((self.root / self.sid / "execution-inputs").exists())
        self.no_model.assert_not_called()

    def test_selected_agent_core_also_honours_no_files_without_filename_promotion(self):
        from packing_assistant.runtime.agent_loop import run_agent
        identifier, original, data = self.upload_example("glazing_panels.xlsx", "facade_panels.xlsx")
        from packing_assistant.office_job import job_root_scope
        with job_root_scope(original.parent):
            result = run_agent("Load glazing_panels.xlsx into 40HQ; do not write any files.",
                               session_id=self.sid, expert_id="pack-ship", p0_confirmed=True)
        self.assertEqual("chat", result["intent"])
        self.assertFalse(result["wrote"])
        self.assertEqual([], result["tools_used"])
        self.assertEqual(data, original.read_bytes())
        self.no_model.assert_not_called()

    def test_unselected_input_is_never_copied_or_counted(self):
        chosen, original, data = self.upload_example("glazing_panels.xlsx", "facade_panels.xlsx")
        extra, other, other_data = self.upload_example("other_panels.xlsx", "facade_panels_rev_b.xlsx")
        done, _ = self.post("Load glazing_panels.xlsx into 40HQ.", attachments=[chosen], confirm_text=ACK)
        plan_file = next(item for item in done["deliverables"] if item["name"] == "pack-plan.json")
        plan = self.client.get("/api/file", params={"path": plan_file["path"]}).json()
        self.assertEqual(6, plan["containers_used"])
        audit = self.client.get("/api/harness/audit/" + self.sid).json()
        self.assertEqual([chosen], [r["id"] for r in audit["runs"][-1]["input_files"]])
        self.assertFalse(list((self.root / self.sid / "execution-inputs").rglob("other_panels.xlsx")))
        self.assertEqual(data, original.read_bytes())
        self.assertEqual(other_data, other.read_bytes())
        self.no_model.assert_not_called()

    def test_linked_compliance_question_runs_real_selected_tender_and_panel_inputs(self):
        tender, original_t, bytes_t = self.upload_example("north_itt.md", "facade_itt_doc.md")
        panels, original_p, bytes_p = self.upload_example("glazing_panels.xlsx", "facade_panels.xlsx")
        done, _ = self.post("Does the plan for glazing_panels.xlsx respect the container limits in north_itt.md?",
                            attachments=[tender, panels], confirm_text=ACK)
        self.assertEqual("run", done["intent"])
        self.assertTrue(done["ok"] and done["wrote"], done)
        self.assertTrue(any(item["name"] == "tender-packing-link.json" for item in done["deliverables"]))
        self.assertNotIn("本轮未接通", done["text"])
        self.assertEqual(bytes_t, original_t.read_bytes())
        self.assertEqual(bytes_p, original_p.read_bytes())
        self.no_model.assert_not_called()

    def test_selected_bid_parse_link_imperative_is_an_operation_not_a_literal_excerpt_question(self):
        tender, original_t, bytes_t = self.upload_example("north_itt.md", "facade_itt_doc.md")
        panels, original_p, bytes_p = self.upload_example("glazing_panels.xlsx", "facade_panels.xlsx")
        done, _ = self.post("Link the tender north_itt.md to the packing list glazing_panels.xlsx and write the logistics response.",
                            expert_ids=["bid-parse"], attachments=[tender, panels], locale="en")
        self.assertEqual(done["intent"], "run")
        self.assertTrue(done["ok"] and done["wrote"], done)
        artifact = next(item for item in done["deliverables"] if item["name"] == "tender-packing-link.json")
        record = self.client.get("/api/file", params={"path": artifact["path"]}).json()
        self.assertEqual(record["plan"]["containers_used"], 6)
        self.assertTrue(record["submit_blocked"])
        self.assertFalse(record["confirmed_by_person"])
        self.assertEqual(bytes_t, original_t.read_bytes())
        self.assertEqual(bytes_p, original_p.read_bytes())
        self.no_model.assert_not_called()

    def test_selected_copy_scope_refuses_duplicate_names_before_copying(self):
        from selected_inputs import selected_job_inputs
        a, original_a, bytes_a = self.upload_example("same.xlsx", "facade_panels.xlsx")
        b, original_b, bytes_b = self.upload_example("same.xlsx", "facade_panels_rev_b.xlsx")
        run = uuid4().hex
        with self.assertRaisesRegex(ValueError, "同名"):
            with selected_job_inputs(self.root, self.sid, run, [a, b]):
                self.fail("Duplicate versions must not enter the solver")
        self.assertFalse((self.root / self.sid / "execution-inputs" / run).exists())
        self.assertEqual(bytes_a, original_a.read_bytes())
        self.assertEqual(bytes_b, original_b.read_bytes())

    def test_duplicate_attachment_names_report_actionable_failed_turn_then_retry_succeeds(self):
        for names, locale, phrase in ((["same.xlsx", "same.xlsx"], "zh-CN", "同名"),
                                      (["Same.xlsx", "same.xlsx"], "en", "duplicate filenames")):
            with self.subTest(names=names):
                self.sid = "input-error-" + uuid4().hex[:16]
                a, original_a, bytes_a = self.upload_example(names[0], "facade_panels.xlsx")
                b, original_b, bytes_b = self.upload_example(names[1], "facade_panels_rev_b.xlsx")
                done, _ = self.post(f"Load {names[0]} into 40HQ.", attachments=[a, b], locale=locale)
                self.assertFalse(done["ok"] or done["wrote"], done)
                self.assertEqual("failed", done["state"])
                self.assertEqual([], done["deliverables"])
                self.assertIn(phrase, done["text"])
                self.assertNotIn("模型设置", done["text"])
                audit = self.client.get("/api/harness/audit/" + self.sid).json()
                self.assertEqual(0, audit["counts"]["tools"])
                self.assertEqual(0, audit["counts"]["writes"])
                self.assertEqual("input_selection", audit["runs"][-1]["error_code"])
                self.assertEqual("duplicate_name", audit["runs"][-1]["input_error"]["reason"])
                self.assertFalse((self.root / self.sid / "execution-inputs").exists())
                restored = self.client.get("/api/sessions/" + self.sid).json()
                self.assertEqual(done["text"], restored["transcript"][-1]["text"])
                self.assertEqual([], restored["deliverables"])
                retry, _ = self.post(f"Load {names[0]} into 40HQ.", attachments=[a], locale=locale)
                self.assertTrue(retry["ok"] and retry["wrote"], retry)
                plan_file = next(item for item in retry["deliverables"] if item["name"] == "pack-plan.json")
                self.assertEqual(6, self.client.get("/api/file", params={"path": plan_file["path"]}).json()["containers_used"])
                self.assertEqual(bytes_a, original_a.read_bytes())
                self.assertEqual(bytes_b, original_b.read_bytes())
                self.no_model.assert_not_called()

    def test_unsupported_input_name_is_a_selection_failure_not_model_failure(self):
        identifier, original, data = self.upload_example("_panels.xlsx", "facade_panels.xlsx")
        done, _ = self.post("Load _panels.xlsx into 40HQ.", attachments=[identifier], locale="en")
        self.assertFalse(done["ok"] or done["wrote"], done)
        self.assertIn("rename", done["text"])
        audit = self.client.get("/api/harness/audit/" + self.sid).json()
        self.assertEqual("unsupported_name", audit["runs"][-1]["input_error"]["reason"])
        self.assertEqual(0, audit["counts"]["tools"])
        self.assertFalse((self.root / self.sid / "execution-inputs").exists())
        self.assertEqual(data, original.read_bytes())

    def test_unavailable_input_and_size_limit_have_distinct_recoverable_reasons(self):
        from uploads import UploadError
        identifier, original, data = self.upload_example("panels.xlsx", "facade_panels.xlsx")
        for context, reason in (
            (patch("selected_inputs.upload_file", side_effect=UploadError("synthetic unavailable original")), "attachment_unavailable"),
            (patch("selected_inputs.MAX_BYTES", len(data)-1), "too_large"),
        ):
            with self.subTest(reason=reason), context:
                done, _ = self.post("Load panels.xlsx into 40HQ.", attachments=[identifier], locale="en")
                self.assertFalse(done["ok"] or done["wrote"], done)
                self.assertEqual([], done["deliverables"])
                self.assertIn("No calculation was started", done["text"])
                audit = self.client.get("/api/harness/audit/" + self.sid).json()
                self.assertEqual(reason, audit["runs"][-1]["input_error"]["reason"])
                self.assertEqual(0, audit["counts"]["tools"])
                self.assertFalse((self.root / self.sid / "execution-inputs").exists())
                self.assertEqual(data, original.read_bytes())
        self.no_model.assert_not_called()

    def test_unexpected_storage_error_is_not_disguised_as_a_selection_or_model_failure(self):
        from scripts.test_workbench_flow import sse_events
        identifier, original, data = self.upload_example("panels.xlsx", "facade_panels.xlsx")
        for locale, phrase in (("en", "local service failed"), ("zh-CN", "本地服务出现错误")):
            with self.subTest(locale=locale), patch("selected_inputs.upload_file", side_effect=PermissionError("synthetic storage denial")), patch("chat_service.logger.exception"):
                response = self.client.post("/api/chat", json={"session_id": self.sid, "message": "Load panels.xlsx into 40HQ.",
                                                              "attachments": [identifier], "locale": locale})
                self.assertEqual(200, response.status_code, response.text)
                errors = [e["data"] for e in sse_events(response) if e["event"] == "error"]
                self.assertEqual(1, len(errors), errors)
                self.assertIn(phrase, errors[0]["text"])
                self.assertNotIn("模型设置", errors[0]["text"])
                self.assertNotIn("duplicate filenames", errors[0]["text"])
                audit = self.client.get("/api/harness/audit/" + self.sid).json()
                self.assertEqual("interrupted", audit["runs"][-1]["error_code"])
                self.assertEqual({}, audit["runs"][-1]["input_error"])
                self.assertEqual(0, audit["counts"]["tools"])
                self.assertEqual(data, original.read_bytes())
        self.no_model.assert_not_called()

    def test_selected_copy_scope_is_run_local_deduplicates_ids_and_restores_context_on_error(self):
        from selected_inputs import selected_job_inputs
        from packing_assistant.office_job import job_root
        identifier, original, data = self.upload_example("glazing_panels.xlsx", "facade_panels.xlsx")
        previous = job_root()
        first, second = uuid4().hex, uuid4().hex
        with self.assertRaisesRegex(RuntimeError, "synthetic interruption"):
            with selected_job_inputs(self.root, self.sid, first, [identifier, identifier]) as manifest:
                self.assertEqual([identifier], [item["id"] for item in manifest])
                self.assertEqual(self.root / self.sid / "execution-inputs" / first, job_root())
                self.assertEqual(data, (job_root() / "glazing_panels.xlsx").read_bytes())
                raise RuntimeError("synthetic interruption")
        self.assertEqual(previous, job_root())
        with self.assertRaises(FileExistsError):
            with selected_job_inputs(self.root, self.sid, first, [identifier]):
                self.fail("A reused run must never replace an earlier input snapshot")
        with selected_job_inputs(self.root, self.sid, second, [identifier]):
            self.assertEqual(data, (job_root() / "glazing_panels.xlsx").read_bytes())
        self.assertEqual(previous, job_root())
        self.assertEqual(data, original.read_bytes())

    def test_selected_copy_scope_refuses_other_session_and_invalid_run_before_copying(self):
        from selected_inputs import selected_job_inputs
        identifier, original, data = self.upload_example("glazing_panels.xlsx", "facade_panels.xlsx")
        other = "en-other-" + uuid4().hex[:16]
        with self.assertRaisesRegex(ValueError, "不可用"):
            with selected_job_inputs(self.root, other, uuid4().hex, [identifier]):
                self.fail("Another session must not gain access to this attachment")
        for run in ("../escape", "", "z" * 32):
            with self.subTest(run=run), self.assertRaisesRegex(ValueError, "运行 id"):
                with selected_job_inputs(self.root, self.sid, run, [identifier]):
                    self.fail("Invalid run id must not create a directory")
        self.assertFalse((self.root / self.sid / "execution-inputs").exists())
        self.assertFalse((self.root / other / "execution-inputs").exists())
        self.assertEqual(data, original.read_bytes())

    def test_read_only_config_never_stages_copies_even_when_helper_is_called(self):
        from selected_inputs import selected_job_inputs
        identifier, original, data = self.upload_example("glazing_panels.xlsx", "facade_panels.xlsx")
        with patch.dict(os.environ, {"CIVIL_SANDBOX": "read-only"}):
            with selected_job_inputs(self.root, self.sid, uuid4().hex, [identifier]) as manifest:
                self.assertEqual([], manifest)
        self.assertFalse((self.root / self.sid / "execution-inputs").exists())
        self.assertEqual(data, original.read_bytes())

    def test_link_request_no_files_veto_never_stages_available_originals(self):
        tender, original_t, bytes_t = self.upload_example("north_itt.md", "facade_itt_doc.md")
        panels, original_p, bytes_p = self.upload_example("glazing_panels.xlsx", "facade_panels.xlsx")
        done, _ = self.post("Check glazing_panels.xlsx against north_itt.md; do not write any files.",
                            attachments=[tender, panels], confirm_text=ACK)
        self.assertEqual("chat", done["intent"])
        self.assertFalse(done["wrote"])
        self.assertEqual([], done["deliverables"])
        self.assertFalse((self.root / self.sid / "execution-inputs").exists())
        self.assertEqual(bytes_t, original_t.read_bytes())
        self.assertEqual(bytes_p, original_p.read_bytes())
        self.no_model.assert_not_called()

    def test_low_risk_link_draft_does_not_require_confirmation_but_stays_submit_blocked(self):
        tender, original_t, bytes_t = self.upload_example("north_itt.md", "facade_itt_doc.md")
        panels, original_p, bytes_p = self.upload_example("glazing_panels.xlsx", "facade_panels.xlsx")
        done, _ = self.post("Check glazing_panels.xlsx against north_itt.md.", attachments=[tender, panels])
        self.assertEqual("run", done["intent"])
        self.assertTrue(done["ok"] and done["wrote"], done)
        self.assertFalse(done["hitl_pending"])
        self.assertTrue(done["submit_blocked"])
        self.assertEqual(bytes_t, original_t.read_bytes())
        self.assertEqual(bytes_p, original_p.read_bytes())
        self.no_model.assert_not_called()

    def test_missing_high_risk_confirmation_cannot_stage_binary_inputs(self):
        panels, original_p, bytes_p = self.upload_example("glazing_panels.xlsx", "facade_panels.xlsx")
        done, _ = self.post("Need a lifting plan for panel installation using glazing_panels.xlsx.",
                            expert_ids=["construction"], attachments=[panels])
        self.assertEqual("run", done["intent"])
        self.assertTrue(done["hitl_pending"], done)
        self.assertFalse(done["wrote"])
        self.assertFalse((self.root / self.sid / "execution-inputs").exists())
        self.assertEqual(bytes_p, original_p.read_bytes())
        self.no_model.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
