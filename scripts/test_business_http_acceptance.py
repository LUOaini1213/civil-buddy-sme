#!/usr/bin/env python3
"""Commercial/finance upload, session restore and real Office downloads."""
from io import BytesIO
from pathlib import Path
import sys
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import test_hazard_bim_http_acceptance as hb
from packing_assistant.post_drafts.business_observations import TOOLS, UNKNOWN

TITLES = {"cost": "3 工程量拆分", "variation": "4 工程量",
          "finance-book": "2 核算检查与业务单据", "finance-tax": "1 辖区、主体与申报日历"}


class BusinessHTTPAcceptance(unittest.TestCase):
    setUp = hb.HazardBIMHttpAcceptance.setUp
    post = hb.HazardBIMHttpAcceptance.post
    markdown = hb.HazardBIMHttpAcceptance.markdown
    spreadsheet_rows = hb.HazardBIMHttpAcceptance.spreadsheet_rows
    word_table_rows = hb.HazardBIMHttpAcceptance.word_table_rows
    copies = hb.HazardBIMHttpAcceptance.copies
    upload = hb.HazardBIMHttpAcceptance.upload

    def test_natural_user_facts_in_actual_download_columns(self):
        for post, text, field, value in (
            ("cost", "生成工程量拆分表。一个是A7.5砌块墙250厚，建施-A9量出来738.1方。", "用户数量", "738.1方"),
            ("variation", "生成签证草稿。编号先用V-21，测量员李甲实测破除量93.8方。", "用户工程量", "93.8方"),
            ("finance-book", "生成核算检查表。一笔是办公用品，电子普票票号09876543，一共4160块。", "用户金额", "4160块"),
            ("finance-tax", "生成税务日历。主体是合成企业，税种：测试税；申报节点：10月16日。", "申报节点", "10月16日"),
        ):
            with self.subTest(post=post):
                self.sid = "biz-natural-"+uuid4().hex[:16]
                md, done = self.markdown(text, post)
                table = self.copies(done, md, TITLES[post])
                self.assertEqual(value, table[1][table[0].index(field)])

    def test_selected_csv_excel_word_reordered_empty_rows_and_originals(self):
        import html
        import openpyxl
        from zipfile import ZipFile
        cases = (
            ("cost", [["数量", "清单项", "图号"], ["71.4m3", "砼", "Q01"], ["92.8t", "", ""]], "用户数量", "分项名称特征"),
            ("variation", [["数量", "事项", "编号"], ["71.4m3", "旧墙", "V01"], ["92.8t", "", ""]], "用户工程量", "编号"),
            ("finance-book", [["金额", "业务", "经办"], ["7140", "采购", "王甲"], ["9280", "", ""]], "用户金额", "业务事项"),
            ("finance-tax", [["税额", "主体", "申报期"], ["7140", "主体甲", "2026-Q3"], ["9280", "", ""]], "用户税额（待核）", "主体"),
        )
        for post, rows, field, identity in cases:
            book = openpyxl.Workbook()
            try:
                for row in rows:
                    book.active.append(row)
                binary = BytesIO()
                book.save(binary)
            finally:
                book.close()
            word = BytesIO()
            xml = "<w:tbl>"+"".join("<w:tr>"+"".join("<w:tc><w:p><w:r><w:t>"+html.escape(v)+"</w:t></w:r></w:p></w:tc>" for v in row)+"</w:tr>" for row in rows)+"</w:tbl>"
            with ZipFile(word, "w") as archive:
                archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'+xml+"</w:body></w:document>")
            csv = ("\n".join(",".join(row) for row in rows)+"\n").encode("utf-8")
            for ext, data in (("csv", csv), ("xlsx", binary.getvalue()), ("docx", word.getvalue())):
                with self.subTest(post=post, extension=ext):
                    self.sid = "biz-office-"+uuid4().hex[:16]
                    response = self.client.post("/api/upload", data={"session_id": self.sid}, files={"files": ("合成财商."+ext, data, "application/octet-stream")})
                    self.assertEqual(200, response.status_code, response.text)
                    identifier = response.json()["files"][0]["id"]
                    original, _ = hb.flow.uploads.upload_file(self.sid, identifier)
                    md, done = self.markdown("请按选中附件生成本岗内部草稿。", post, attachments=[identifier])
                    table = self.copies(done, md, TITLES[post])
                    self.assertEqual(3, len(table))
                    self.assertEqual([rows[1][0], rows[2][0]], [r[table[0].index(field)] for r in table[1:]])
                    self.assertEqual(UNKNOWN, table[2][table[0].index(identity)])
                    self.assertEqual(data, original.read_bytes())

    def test_two_selected_files_do_not_lend_numbers_or_sources(self):
        for post, first, second, field, value in (
            ("cost", "清单项,数量,图号\n砼,71.4m3,Q01\n", "清单项,数量\n钢筋,\n", "用户数量", "71.4m3"),
            ("variation", "事项,数量,数量来源\n旧墙,71.4m3,Q01\n", "事项,数量\n停电,\n", "用户工程量", "71.4m3"),
            ("finance-book", "业务,金额,发票号\n采购,7140,I01\n", "业务,金额\n租赁,\n", "用户金额", "7140"),
            ("finance-tax", "主体,税率,辖区\n甲,7.5%,SG\n", "主体,税率\n乙,\n", "用户税率（待核）", "7.5%"),
        ):
            with self.subTest(post=post):
                self.sid = "biz-files-"+uuid4().hex[:16]
                uploads = [self.upload(name, text) for name, text in (("甲.csv", first), ("乙.csv", second))]
                md, done = self.markdown("请把选中附件分别生成本岗内部草稿，不借值。", post, attachments=[u[0] for u in uploads])
                table = self.copies(done, md, TITLES[post])
                self.assertEqual(value, table[1][table[0].index(field)])
                self.assertEqual(UNKNOWN, table[2][table[0].index(field)])
                for _, original, data in uploads:
                    self.assertEqual(data, original.read_bytes())

    def test_unknown_update_not_published_as_current_amount_or_rate(self):
        for post, message, field in (
            ("cost", "清单项：砼；数量：71.4m3；数量：没定", "用户数量"),
            ("variation", "事项：旧墙；数量：71.4m3；数量：没定", "用户工程量"),
            ("finance-book", "业务：采购；金额：7140；金额：未核", "用户金额"),
            ("finance-tax", "主体：甲；辖区：SG；税种：GST；税率：7.5%；税率：未核", "用户税率（待核）"),
        ):
            self.sid = "biz-unknown-"+uuid4().hex[:16]
            md, done = self.markdown("请生成本岗内部草稿；"+message, post)
            table = self.copies(done, md, TITLES[post])
            self.assertEqual(UNKNOWN, table[1][table[0].index(field)])

    def test_variation_child_quantities_not_shared_and_signatures_empty(self):
        md, done = self.markdown("生成签证草稿；事项：加固；编号：V01；数量类型：砼；数量：14.2m3；规格：C35；数量来源：Q1.pdf；数量类型：钢筋；数量：1.8t；规格：HRB500；批准记录：用户称批准", "variation")
        table = self.copies(done, md, TITLES["variation"])
        self.assertEqual(["Q1.pdf", UNKNOWN], [r[table[0].index("计量来源")] for r in table[1:]])
        self.assertEqual(["C35", "HRB500"], [r[table[0].index("规格")] for r in table[1:]])
        sign = self.copies(done, md, "7 签认栏（审批空栏）")
        self.assertEqual(["（空栏）"]*4, sign[1][1:])

    def test_cost_quotes_finance_posting_and_tax_filing_never_confirmed(self):
        for post, message, title, expected in (
            ("cost", "清单项：砼；数量：71；报价：481元", "3 工程量拆分", ["TBD", UNKNOWN]),
            ("finance-book", "业务：采购；金额：7140；审批：用户称批准；付款记录：付款扫描件", "用户科目与账套口径（未记账）", [UNKNOWN, UNKNOWN]),
            ("finance-tax", "主体：甲；税种：测试税；申报节点：10月16日；申报记录：回执扫描件", "用户申报与缴税材料（未核验）", [UNKNOWN, "（空栏）"]),
        ):
            self.sid = "biz-status-"+uuid4().hex[:16]
            md, done = self.markdown("请生成本岗内部草稿；"+message, post)
            table = self.copies(done, md, title)
            self.assertEqual(expected, table[1][-2:])

    def test_literal_formula_and_markup_not_executable_spreadsheet_cells(self):
        import openpyxl
        for post, table in (
            ("cost", "|清单项|数量|\n|---|---|\n|甲&amp;amp;乙&#124;丙|=1+2|"),
            ("variation", "|事项|数量|\n|---|---|\n|甲&amp;amp;乙&#124;丙|=1+2|"),
            ("finance-book", "|业务|金额|\n|---|---|\n|甲&amp;amp;乙&#124;丙|=1+2|"),
            ("finance-tax", "|主体|税额|\n|---|---|\n|甲&amp;amp;乙&#124;丙|=1+2|"),
        ):
            self.sid = "biz-formula-"+uuid4().hex[:16]
            md, done = self.markdown("生成内部草稿\n"+table, post)
            copied = self.copies(done, md, TITLES[post])
            self.assertIn("=1+2", copied[1])
            artifact = next(a for a in done["deliverables"] if a["name"].endswith(".xlsx"))
            result = self.client.get("/api/file", params={"path": artifact["path"]})
            book = openpyxl.load_workbook(BytesIO(result.content))
            try:
                self.assertTrue(all(cell.data_type != "f" for sheet in book for row in sheet for cell in row))
            finally:
                book.close()

    def test_chat_sibling_and_readonly_gates_still_precede_files(self):
        import os
        from unittest.mock import patch
        from packing_assistant.runtime.tool_engine import default_engine
        for owner, tool in TOOLS.items():
            self.sid = "biz-chat-"+uuid4().hex[:16]
            done, _ = self.post("这个岗位缺哪些材料？只解释，不生成文件。", expert_ids=[owner])
            self.assertFalse(done["wrote"])
            self.assertEqual([], done["deliverables"])
            for expert, intent in ((owner, "chat"), ("quality", "run")):
                denied = default_engine().execute(tool, {"text": "生成草稿", "session_id": self.sid}, expert_id=expert, intent=intent)
                self.assertFalse(denied["ok"])
                self.assertEqual("permission_denied", denied["error_code"])
            with patch.dict(os.environ, {"CIVIL_SANDBOX": "read-only"}):
                denied = default_engine().execute(tool, {"text": "生成草稿", "session_id": self.sid}, expert_id=owner, intent="run")
                self.assertFalse(denied["ok"])
                self.assertFalse(denied.get("wrote", False))
        self.no_model.assert_not_called()


if __name__ == "__main__":
    unittest.main()
