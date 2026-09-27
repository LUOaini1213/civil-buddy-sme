#!/usr/bin/env python3
"""Human confirmation at the real workbench and gateway HTTP boundaries: the typed sentence, never a flag."""

from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from fastapi.testclient import TestClient
from pydantic import ValidationError
from packing_assistant.runtime.civil_config import CONFIRM, CONFIRM_EN
from scripts import test_workbench_flow as flow

# A client-set flag, in any shape, is never a person's confirmation.
FLAGS = (True, "true", "yes", 1, 0, None, [], {})
COERCIONS = FLAGS[1:]
# Only the exact sentence: text that merely quotes it (a refusal, a question) approves nothing.
QUOTED = ("不同意：" + CONFIRM, CONFIRM + "吗？", "No: " + CONFIRM_EN, "Did you mean " + CONFIRM_EN)
# The English sentence is exact too: not lower-cased, not without its full stop, not with a comma for the semicolon.
NOT_THE_SENTENCE = ("", "我明白", "我已核对 P0", CONFIRM[:-1], "I understand", CONFIRM_EN.lower(), CONFIRM_EN[:-1],
                    CONFIRM_EN.replace(";", ","), *QUOTED)
SENTENCES = (CONFIRM, CONFIRM_EN)


class WorkbenchConfirmationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.flow = flow.WorkbenchFlowTests()
        self.flow.setUp()
        self.addCleanup(self.flow.doCleanups)
        self.client = self.flow.client

    def test_chat_refuses_a_flag_before_any_high_risk_execution(self) -> None:
        with patch.object(flow.agent_loop, "run_agent") as runner:
            for field, values in (("confirm_ok", COERCIONS), ("confirm_text", (True, 1, [], {}))):
                for value in values:
                    with self.subTest(field=field, value=value):
                        response = self.client.post("/api/chat", json={
                            "message": "写一份消防专篇", "expert_ids": ["fire-protect"],
                            "session_id": self.flow.sid, field: value,
                        })
                        self.assertEqual(422, response.status_code, response.text)
            runner.assert_not_called()
        self.assertFalse(flow.chat_service._ACTIVE)
        self.assertFalse(list(self.flow.root.rglob("*.md")))

    def test_only_the_typed_sentence_writes_an_actual_high_risk_draft(self) -> None:
        # confirm_ok is still sent by the shared page for the Rust workbench; here true alone approves nothing.
        for typed in NOT_THE_SENTENCE:
            waiting, _ = self.flow.post("写一份消防专篇，缺失内容待填", expert_ids=["fire-protect"], confirm_ok=True, confirm_text=typed)
            self.assertTrue(waiting["hitl_pending"], (typed, waiting))
            self.assertFalse(waiting["wrote"])
        self.assertFalse(list(self.flow.root.rglob("*.md")))
        for sentence in SENTENCES:           # either sentence, typed by the person in this request
            completed, _ = self.flow.post("写一份消防专篇，缺失内容待填", expert_ids=["fire-protect"], confirm_text=sentence)
            self.assertFalse(completed["hitl_pending"], (sentence, completed))
            self.assertTrue(completed["ok"] and completed["wrote"], completed)
            self.assertTrue(any(item["name"].endswith(".md") and Path(item["path"]).is_file()
                                for item in completed["deliverables"]))

    def test_background_entry_refuses_flags_and_forwards_only_the_sentence(self) -> None:
        seen = []

        def fake_start(root, turn, *, lease, **_kw):
            seen.append(turn["confirmed"])
            lease.release()
            return {"ok": True, "background": True, "session_id": turn["session_id"], "turn_id": "probe", "state": "done"}

        body = {"session_id": "existing-probe", "message": "写一份消防专篇", "background": True, "expert_ids": ["fire-protect"]}
        with patch.object(flow.chat_service, "start_background_turn", side_effect=fake_start) as runner:
            for value in COERCIONS:
                with self.subTest(value=value):
                    self.assertEqual(422, self.client.post("/api/chat", json={**body, "confirm_ok": value}).status_code)
            runner.assert_not_called()
            for typed, expected in (*((t, False) for t in NOT_THE_SENTENCE), (CONFIRM, True), (CONFIRM_EN, True)):
                response = self.client.post("/api/chat", json={**body, "confirm_text": typed, "confirm_ok": True})
                self.assertEqual(202, response.status_code, response.text)
                self.assertIs(expected, seen[-1])
        self.assertFalse(flow.chat_service._ACTIVE)


class GatewayConfirmationTests(unittest.TestCase):
    def setUp(self) -> None:
        from gateway import app as gateway

        self.gateway = gateway
        self.client = TestClient(gateway.app)
        self.addCleanup(self.client.close)

    def test_json_routes_refuse_flags_and_take_only_the_typed_sentence(self) -> None:
        routes = (
            ("/api/turn", "packing_assistant.product_turn.run_turn", ("p0_confirmed", "confirm_ok")),
            ("/api/agent", "packing_assistant.runtime.agent_loop.run_agent", ("p0_confirmed", "confirm_ok")),
            ("/api/tender/parse", "gateway.app._tender_parse_via_engine", ("p0_confirmed",)),
            ("/api/tender/delivery", "packing_assistant.tender_delivery.run_tender_delivery_pipeline", ("p0_confirmed",)),
        )
        for route, target, fields in routes:
            with self.subTest(route=route), patch(target, return_value={"ok": True}) as runner:
                for field in fields:
                    for value in FLAGS:
                        for sentence in SENTENCES:     # a flag next to either sentence is refused before anything runs
                            response = self.client.post(route, json={"text": "写一份消防专篇", field: value, "confirm_text": sentence})
                            self.assertEqual(422, response.status_code, (route, field, value, response.text))
                for value in (1, [], {}, None):
                    self.assertEqual(422, self.client.post(route, json={"text": "x", "confirm_text": value}).status_code)
                runner.assert_not_called()
                for body, expected in (({}, False), ({fields[0]: False}, False), ({"confirm_text": "我明白"}, False),
                                       ({"text": "写一份消防专篇。" + CONFIRM}, False),    # the sentence counts only in confirm_text
                                       ({"text": "Write the fire protection report. " + CONFIRM_EN}, False),
                                       *(({"confirm_text": t}, False) for t in NOT_THE_SENTENCE),
                                       ({"confirm_text": CONFIRM_EN}, True), ({"confirm_text": "\n" + CONFIRM_EN + " "}, True),
                                       *(({"confirm_text": t}, False) for t in QUOTED),
                                       ({"confirm_text": " " + CONFIRM + " "}, True), ({fields[0]: False, "confirm_text": CONFIRM}, True)):
                    response = self.client.post(route, json={"text": "写一份消防专篇", **body})
                    self.assertEqual(200, response.status_code, response.text)
                    self.assertIs(expected, runner.call_args.kwargs["p0_confirmed"], (route, body))

    def test_multipart_takes_only_the_typed_sentence(self) -> None:
        for route, field in (("/api/tender/parse/file", "file"), ("/api/tender/parse/files", "files")):
            with self.subTest(route=route), ExitStack() as stack:
                ingest = stack.enter_context(patch.object(self.gateway, "_tender_ingest_from_uploads", return_value={"text": "招标节选材料"}))
                runner = stack.enter_context(patch.object(self.gateway, "_tender_parse_via_engine", return_value={"ok": True}))
                for value in ("true", "yes", "1", "0", "True", "False", " true "):
                    response = self.client.post(route, data={"p0_confirmed": value, "confirm_text": CONFIRM},
                                                files={field: ("input.txt", b"fixture excerpt")})
                    self.assertEqual(422, response.status_code, (value, response.text))
                runner.assert_not_called()
                ingest.assert_not_called()
                for data, expected in (({}, False), ({"p0_confirmed": "false"}, False), ({"confirm_text": "我明白"}, False),
                                       *(({"confirm_text": t}, False) for t in QUOTED), ({"confirm_text": CONFIRM}, True),
                                       ({"confirm_text": CONFIRM_EN}, True), ({"confirm_text": CONFIRM_EN.lower()}, False)):
                    response = self.client.post(route, data=data, files={field: ("input.txt", b"fixture excerpt")})
                    self.assertEqual(200, response.status_code, response.text)
                    self.assertIs(expected, runner.call_args.kwargs["p0_confirmed"], data)

    def test_packing_confirmation_models_do_not_coerce_flags_or_checklist_items(self) -> None:
        invalid = ("true", "yes", "false", 1, 0, None, [], {})
        for model in (self.gateway.DemoRequest, self.gateway.PipelineRequest,
                      self.gateway.ProfilePipelineRequest, self.gateway.TraceRequest):
            for value in invalid:
                with self.subTest(model=model.__name__, value=value), self.assertRaises(ValidationError):
                    model(enable_auto_confirm=value)
            for value in (False, True):
                self.assertIs(value, model(enable_auto_confirm=value).enable_auto_confirm)
        for value in invalid:
            with self.assertRaises(ValidationError):
                self.gateway.ConfirmRequest(action="confirm", checklist_checked={"fixture": value})
            with self.assertRaises(ValidationError):
                self.gateway.ConfirmRequest(action="confirm", enforce_ns_checklist=value)
        confirmed = self.gateway.ConfirmRequest(action="confirm", checklist_checked={"fixture": True}, enforce_ns_checklist=True)
        self.assertIs(True, confirmed.checklist_checked["fixture"])
        self.assertIs(True, confirmed.enforce_ns_checklist)


if __name__ == "__main__":
    unittest.main(verbosity=2)
