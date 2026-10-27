"""Actual bid draft/upload/download journeys; no model or external provider calls."""
from io import BytesIO
from pathlib import Path
import sys
import unittest
from uuid import uuid4
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import test_hazard_bim_http_acceptance as hb
from packing_assistant.office_job import tables_from_md

ACK = "我明白，将由持证人员签认"
TITLE = "字段未明确的资料摘录"


class BidUnmappedHTTP(unittest.TestCase):
    setUp = hb.HazardBIMHttpAcceptance.setUp
    post = hb.HazardBIMHttpAcceptance.post
    markdown = hb.HazardBIMHttpAcceptance.markdown
    spreadsheet_rows = hb.HazardBIMHttpAcceptance.spreadsheet_rows
    word_table_rows = hb.HazardBIMHttpAcceptance.word_table_rows
    copies = hb.HazardBIMHttpAcceptance.copies
    upload = hb.HazardBIMHttpAcceptance.upload

    def gap(self, md, topic):
        for title, table in tables_from_md(md):
            if "响应原文或证据" in table[0]:
                for row in table[1:]:
                    if row[0] == topic:
                        return title, dict(zip(table[0], row))
        self.fail("Missing gap row: " + topic)

    def word_text(self, done):
        from xml.etree import ElementTree as ET
        artifact = next(a for a in done["deliverables"] if a["name"].endswith(".docx"))
        response = self.client.get("/api/file", params={"path": artifact["path"]})
        self.assertEqual(200, response.status_code)
        with ZipFile(BytesIO(response.content)) as archive:
            document = ET.fromstring(archive.read("word/document.xml"))
        return "".join(t.text or "" for t in document.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"))

    def test_ambiguous_value_is_in_same_unconfirmed_field_in_both_downloads(self):
        md, done = self.markdown("生成内部响应缺口草稿。\nThe tender requires completion within 16 months "
                                 "and tender validity of 120 days.\nWe stated 17 months in our Form of Tender.",
                                 "bid-compliance", confirm_text=ACK)
        table = self.copies(done, md, TITLE)
        self.assertIn("17 months", table[1])
        self.assertIn("UNSPECIFIED", table[1])
        self.assertIn("我方响应", table[1][0])
        title, duration = self.gap(md, "工期")
        self.copies(done, md, title)
        self.assertEqual("未响应", duration["三态"])
        self.assertNotIn("17 months", duration["响应原文或证据"])
        # The HTTP worker prepends its non-empty request-context heading. This
        # is the assembled input's location, never a source-document page.
        self.assertIn("输入 L4", table[1][3])
        self.no_model.assert_not_called()

    def test_english_pending_states_are_unresponded_in_actual_office_downloads(self):
        for state in ("is not yet issued", "has not been issued yet", "will be issued tomorrow",
                      "has not yet been provided", "is not submitted"):
            with self.subTest(state=state):
                self.sid = "bid-pending-" + uuid4().hex[:16]
                md, done = self.markdown("生成内部响应缺口草稿。\nTender requirements: Tender Deposit: S$43,200.\n"
                                         "Our Tender Deposit: S$43,200 " + state + ".",
                                         "bid-compliance", confirm_text=ACK)
                title, row = self.gap(md, "投标保证金")
                self.copies(done, md, title)
                self.assertEqual("未响应", row["三态"])
        self.no_model.assert_not_called()

    def test_uploaded_long_source_stays_in_markdown_word_and_original_bytes(self):
        source = "Our response:\nWe have an unlabelled note " + "context " * 20 + "containing 47 months."
        identifier, original, data = self.upload("响应草稿.txt", source)
        md, done = self.markdown("请按选中附件生成内部响应缺口草稿，数字用途不明则待确认。", "bid-compliance",
                                 attachments=[identifier], confirm_text=ACK)
        table = self.copies(done, md, TITLE)
        self.assertIn("47 months", table[1])
        self.assertIn("UNSPECIFIED", table[1])
        full = source.splitlines()[1]
        self.assertIn(full, md)
        self.assertIn(full, self.word_text(done))
        self.assertEqual(data, original.read_bytes())
        self.no_model.assert_not_called()

    def test_two_uploaded_lots_keep_repeated_literal_and_input_positions_separate(self):
        uploads = [self.upload(name, text) for name, text in (
            ("响应甲.txt", "A标段 We stated 17 months in our Form of Tender."),
            ("响应乙.txt", "B标段 We stated 17 months in our Form of Tender."))]
        md, done = self.markdown("请按两个选中附件分别生成内部响应缺口草稿，不借用数值。", "bid-compliance",
                                 attachments=[u[0] for u in uploads], confirm_text=ACK)
        table = self.copies(done, md, TITLE)
        self.assertEqual(["17 months", "17 months"], [r[1] for r in table[1:]])
        self.assertEqual(["A标段", "B标段"], [r[4] for r in table[1:]])
        self.assertNotEqual(table[1][3], table[2][3])
        self.assertTrue(all(r[2] == "UNSPECIFIED" for r in table[1:]))
        for _, original, data in uploads:
            self.assertEqual(data, original.read_bytes())
        self.no_model.assert_not_called()

    def test_completed_claim_is_only_unverified_not_certified(self):
        md, done = self.markdown("生成内部响应缺口草稿。\nTender requirements: Tender Deposit: S$43,200.\n"
                                 "Our Tender Deposit: S$43,200 has been issued.", "bid-compliance", confirm_text=ACK)
        title, row = self.gap(md, "投标保证金")
        self.copies(done, md, title)
        self.assertEqual("已响应·待核验", row["三态"])
        self.assertTrue(done["submit_blocked"])
        self.no_model.assert_not_called()

    def test_unqualified_amountless_guarantee_is_preserved_not_assigned_to_bid_deposit(self):
        md, done = self.markdown("生成内部响应缺口草稿。\nTender requirements: Tender Deposit: S$43,200.\n"
                                 "Our banker's guarantee is not issued yet.", "bid-compliance", confirm_text=ACK)
        table = self.copies(done, md, TITLE)
        self.assertIn("UNSPECIFIED", table[1])
        self.assertIn("Our banker's guarantee is not issued yet.", self.word_text(done))
        _, deposit = self.gap(md, "投标保证金")
        self.assertEqual("未响应", deposit["三态"])
        self.assertNotIn("banker's guarantee", deposit["响应原文或证据"])
        self.no_model.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
