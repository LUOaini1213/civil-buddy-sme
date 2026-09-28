#!/usr/bin/env python3
"""Offline release regressions: named documents do not turn explanations into writes; quoted consent is inert."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
for key in tuple(os.environ):
    if key.endswith("_API_KEY"):
        os.environ.pop(key, None)

from packing_assistant.runtime.civil_config import CONFIRM, CONFIRM_EN, contains_confirmation, confirms_in_message
from packing_assistant.runtime.task_router import route_task
from scripts import test_workbench_flow as flow

READ_ONLY = (
    "Could you explain how to check facade_panels.xlsx against facade_itt_doc.md?",
    "Explain the shipping requirements in facade_itt_doc.md using facade_panels.xlsx as context.",
    "Please explain whether facade_panels.xlsx meets the clauses in facade_itt_doc.md; do not run anything or write any files.",
    "I checked facade_panels.xlsx against the requirements in facade_itt_doc.md yesterday.",
    "We will check facade_panels.xlsx against facade_itt_doc.md tomorrow.",
    "Which shipping requirements apply to facade_panels.xlsx in facade_itt_doc.md?",
    "Shipping requirements for facade_panels.xlsx are in tender.check.md.",
)


class LinkIntentTests(unittest.TestCase):
    def test_explanations_and_reports_remain_read_only(self):
        for request in READ_ONLY:
            with self.subTest(request=request):
                self.assertEqual(route_task(request)["intent"], "chat")

    def test_positive_current_requests_still_execute(self):
        for request in (
            "Check whether facade_panels.xlsx meets the logistics clauses of facade_itt_doc.md",
            "Does our loading plan for facade_panels.xlsx comply with the container clauses in facade_itt_doc.md?",
            "Can you match facade_panels.xlsx to the clauses of facade_itt_doc.md?",
            "核对 facade_panels.xlsx 是否满足 facade_itt_doc.md 的物流条款",
        ):
            with self.subTest(request=request):
                self.assertEqual(route_task(request)["intent"], "run")


class MessageApprovalTests(unittest.TestCase):
    def test_only_the_sentence_alone_approves(self):
        # Merged with the development line's stricter rule (review of PR #67 / #72): the typed task is never read
        # for approval, so the sentence appended to a request approves nothing either.
        for sentence in (CONFIRM, CONFIRM_EN):
            self.assertTrue(confirms_in_message(sentence), sentence)
            for request in (
                "写一份消防专篇。" + sentence,
                "Draft the fire protection report. " + sentence,
                'Draft the report. "' + sentence + '"',
                "Draft the report.\n> " + sentence,
                "Draft the report.\n```text\n" + sentence + "\n```",
                "Draft the report.\n```text\n" + sentence,
                "Draft the report. I refuse.\n" + sentence,
                "Draft the report. I am refusing:\n" + sentence,
                "Draft the report. No. " + sentence,
                "Draft the report. User wrote:\n" + sentence,
                "Draft the report. Earlier confirmation:\n" + sentence,
                "写一份消防专篇。上次签认：\n" + sentence,
                "写一份消防专篇。不要签认：\n" + sentence,
                sentence + "吗？", sentence + "\n？",
            ):
                with self.subTest(request=request):
                    self.assertTrue(contains_confirmation(request))  # still detected by memory scrubbing
                    self.assertFalse(confirms_in_message(request))
        self.assertFalse(confirms_in_message(True))


class HttpRegressions(unittest.TestCase):
    def setUp(self):
        self.flow = flow.WorkbenchFlowTests()
        self.flow.setUp()
        self.addCleanup(self.flow.doCleanups)

    def test_explanation_with_available_originals_writes_no_deliverable(self):
        # Real HTTP + deterministic runtime with existing named synthetic files. No tool/model mocks.
        attachments = []
        for name in ("facade_itt_doc.md", "facade_panels.xlsx"):
            source = ROOT / "examples" / "facade-demo" / name
            response = self.flow.client.post("/api/upload", data={"session_id": self.flow.sid},
                                            files={"files": (name, source.read_bytes())})
            self.assertEqual(response.status_code, 200, response.text)
            attachments.append(response.json()["files"][0]["id"])
        for request in READ_ONLY[:3]:
            with self.subTest(request=request):
                out, _ = self.flow.post(request, attachments=attachments)
                self.assertFalse(out["wrote"], out)
                self.assertEqual(out["intent"], "chat")
                self.assertFalse(out.get("deliverables"))
        self.assertFalse(list(self.flow.root.rglob("bidbook.en.*")))
        self.assertFalse(list(self.flow.root.rglob("tender-packing-link.*")))

    def test_quoted_or_refused_message_sentence_cannot_write_high_risk_draft(self):
        requests = (
            'Draft the fire protection report. I refuse the confirmation phrase "' + CONFIRM_EN + '"; leave signing for later.',
            'Draft the fire protection report. "' + CONFIRM_EN + '"',
            "写一份消防专篇。不同意：" + CONFIRM,
            "写一份消防专篇。上次签认：\n" + CONFIRM,
        )
        for request in requests:
            with self.subTest(request=request):
                out, _ = self.flow.post(request, expert_ids=["fire-protect"], confirm_text="")
                self.assertFalse(out["wrote"], out)
                self.assertTrue(out["hitl_pending"], out)
        self.assertFalse(list(self.flow.root.rglob("fire-protect__brief.*")))

    def test_sentence_in_the_confirmation_box_still_writes_for_both_languages(self):
        for sentence in (CONFIRM, CONFIRM_EN):
            out, _ = self.flow.post("写一份消防专篇，缺失内容待填。",
                                   expert_ids=["fire-protect"], confirm_text=sentence)
            self.assertTrue(out["wrote"], out)
            self.assertFalse(out["hitl_pending"], out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
