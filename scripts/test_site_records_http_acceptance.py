#!/usr/bin/env python3
"""Real upload/restore/Office downloads and survey public approval gate."""
from io import BytesIO
from pathlib import Path
import sys
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import test_hazard_bim_http_acceptance as hb
from packing_assistant.post_drafts.site_records import TOOLS, UNKNOWN
from packing_assistant.office_job import tables_from_md

ACK = "我明白，将由持证人员签认"
TITLES = {"env": "5 弃土与建筑垃圾", "supervision": "6 期限与闭合台账", "survey": "控制点基准分组 1"}


class SiteRecordsHTTP(unittest.TestCase):
    setUp = hb.HazardBIMHttpAcceptance.setUp
    post = hb.HazardBIMHttpAcceptance.post
    markdown = hb.HazardBIMHttpAcceptance.markdown
    spreadsheet_rows = hb.HazardBIMHttpAcceptance.spreadsheet_rows
    word_table_rows = hb.HazardBIMHttpAcceptance.word_table_rows
    copies = hb.HazardBIMHttpAcceptance.copies
    upload = hb.HazardBIMHttpAcceptance.upload

    def test_natural_inputs_reach_matching_word_and_excel_fields(self):
        for post, message, field, value in (
            ("env", "生成环保文明清单。一个是4#地块桩基泥浆，一天出82方。", "用户产生/外运数量", "82方"),
            ("supervision", "生成监理通知回复。一张说6#楼三层框架柱保护层偏薄，周甲负责，10月26日前交。", "用户完成时限", "10月26日"),
            ("survey", "生成复测记录。GPS09点X=3456218.347，Y=512076.592，高程46.815。", "用户北向 X 坐标", "3456218.347"),
        ):
            self.sid = "sr-natural-"+uuid4().hex[:16]
            md, done = self.markdown(message, post, confirm_text=ACK)
            table = self.copies(done, md, TITLES[post])
            self.assertEqual(value, table[1][table[0].index(field)])

    def test_selected_csv_excel_word_keep_empty_reordered_records_and_original_bytes(self):
        import html
        import openpyxl
        from zipfile import ZipFile
        cases = (
            ("env", [["数量", "活动", "联单号"], ["71方", "泥浆", "L01"], ["92吨", "", ""]], "用户产生/外运数量", "污染源分类"),
            ("supervision", [["期限", "来文条目", "来文编号"], ["11月6日", "漏缝", "N01"], ["11月7日", "", ""]], "用户完成时限", "来文条目"),
            ("survey", [["X", "点号", "Y"], ["71.400", "BM01", "82.300"], ["92.800", "", ""]], "用户北向 X 坐标", "点号"),
        )
        for post, rows, field, identity in cases:
            book = openpyxl.Workbook()
            try:
                for row in rows:
                    book.active.append(row)
                excel = BytesIO()
                book.save(excel)
            finally:
                book.close()
            word = BytesIO()
            xml = "<w:tbl>"+"".join("<w:tr>"+"".join("<w:tc><w:p><w:r><w:t>"+html.escape(v)+"</w:t></w:r></w:p></w:tc>" for v in row)+"</w:tr>" for row in rows)+"</w:tbl>"
            with ZipFile(word, "w") as archive:
                archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'+xml+"</w:body></w:document>")
            csv = ("\n".join(",".join(row) for row in rows)+"\n").encode("utf-8")
            for ext, data in (("csv", csv), ("xlsx", excel.getvalue()), ("docx", word.getvalue())):
                with self.subTest(post=post, extension=ext):
                    self.sid = "sr-office-"+uuid4().hex[:16]
                    response = self.client.post("/api/upload", data={"session_id": self.sid}, files={"files": ("合成记录."+ext, data, "application/octet-stream")})
                    self.assertEqual(200, response.status_code, response.text)
                    identifier = response.json()["files"][0]["id"]
                    original, _ = hb.flow.uploads.upload_file(self.sid, identifier)
                    md, done = self.markdown("请按选中附件生成本岗内部草稿。", post, attachments=[identifier], confirm_text=ACK)
                    table = self.copies(done, md, TITLES[post])
                    self.assertEqual(3, len(table))
                    self.assertEqual([rows[1][0], rows[2][0]], [r[table[0].index(field)] for r in table[1:]])
                    self.assertEqual(UNKNOWN, table[2][table[0].index(identity)])
                    if post == "env":
                        activities = self.copies(done, md, "活动与环保检查范围")
                        self.assertEqual("泥浆", activities[1][activities[0].index("部位活动")])
                        self.assertEqual(UNKNOWN, activities[2][activities[0].index("部位活动")])
                    self.assertEqual(data, original.read_bytes())

    def test_selected_files_not_lend_quantity_deadline_or_coordinate(self):
        for post, first, second, field, value in (
            ("env", "活动,数量\n泥浆,71方\n", "活动,数量\n碎砼,\n", "用户产生/外运数量", "71方"),
            ("supervision", "问题,期限\n漏缝,11月6日\n", "问题,期限\n积水,\n", "用户完成时限", "11月6日"),
            ("survey", "点号,X\nBM01,71.400\n", "点号,X\nBM02,\n", "用户北向 X 坐标", "71.400"),
        ):
            self.sid = "sr-files-"+uuid4().hex[:16]
            uploads = [self.upload(name, data) for name, data in (("甲.csv", first), ("乙.csv", second))]
            md, done = self.markdown("请把两个选中附件分别生成本岗内部草稿，不借值。", post, attachments=[u[0] for u in uploads], confirm_text=ACK)
            if post == "survey":
                first_table = self.copies(done, md, "控制点基准分组 1")
                second_table = self.copies(done, md, "控制点基准分组 2")
                self.assertEqual(value, first_table[1][first_table[0].index(field)])
                self.assertEqual(UNKNOWN, second_table[1][second_table[0].index(field)])
            else:
                table = self.copies(done, md, TITLES[post])
                self.assertEqual(value, table[1][table[0].index(field)])
                self.assertEqual(UNKNOWN, table[2][table[0].index(field)])
            for _, original, data in uploads:
                self.assertEqual(data, original.read_bytes())

    def test_unknown_updates_withdraw_value_from_downloaded_current_fields(self):
        for post, message, field in (
            ("env", "活动：泥浆；数量：71方；数量：没定", "用户产生/外运数量"),
            ("supervision", "问题：漏缝；期限：11月6日；期限：没写", "用户完成时限"),
            ("survey", "点号：BM01；X：71.400；X：还没测", "用户北向 X 坐标"),
        ):
            self.sid = "sr-withdraw-"+uuid4().hex[:16]
            md, done = self.markdown("请生成本岗内部草稿；"+message, post, confirm_text=ACK)
            table = self.copies(done, md, TITLES[post])
            self.assertEqual(UNKNOWN, table[1][table[0].index(field)])

    def test_estimated_disposal_quantity_and_reported_application_not_certified(self):
        md, done = self.markdown("生成环保文明清单；活动：碎砼；估计量：估计214吨；夜间作业：11月6日连续浇筑，已报街道", "env")
        quantity = self.copies(done, md, TITLES["env"])
        self.assertEqual(UNKNOWN, quantity[1][quantity[0].index("用户产生/外运数量")])
        self.assertEqual("估计214吨", quantity[1][quantity[0].index("用户估计运输/产生量（未核）")])
        night = self.copies(done, md, "4 噪声与夜间")
        self.assertEqual("已报街道", night[1][night[0].index("用户报批材料摘录")])
        self.assertEqual(UNKNOWN, night[1][-1])

    def test_notice_children_have_own_deadlines_empty_recheck_and_actual_claims_separate(self):
        md, done = self.markdown("生成监理通知回复；来文编号：N01；问题：漏缝；期限：11月6日；实施记录：用户称已做；问题：积水；拟办：排水", "supervision")
        table = self.copies(done, md, TITLES["supervision"])
        self.assertEqual(["N01", "N01"], [r[table[0].index("来文编号")] for r in table[1:]])
        self.assertEqual(["11月6日", UNKNOWN], [r[table[0].index("用户完成时限")] for r in table[1:]])
        self.assertTrue(all(r[-2:] == [UNKNOWN, "（空栏）"] for r in table[1:]))
        sign = self.copies(done, md, "8 签发与复查空栏")
        self.assertTrue(all(r[1:] == ["（空栏）"]*5 for r in sign[1:]))

    def test_redirected_estimate_keeps_units_and_no_current_measured_value_in_copies(self):
        md, done = self.markdown("生成环保文明清单；活动：泥浆；数量：71方；数量：预计82吨", "env")
        table = self.copies(done, md, TITLES["env"])
        for field, value in (("用户产生/外运数量", UNKNOWN), ("数量单位", UNKNOWN),
                             ("用户估计运输/产生量（未核）", "预计82吨"), ("估计量单位", "吨")):
            self.assertEqual(value, table[1][table[0].index(field)])

    def test_natural_survey_mm_and_original_heights_survive_office_download(self):
        md, done = self.markdown("生成测量复测记录。BM01点 北71.00mm，东92.50毫米，原高程12.50mm，这次测的12.40mm", "survey", confirm_text=ACK)
        coordinates = self.copies(done, md, TITLES["survey"])
        for field, value in (("用户北向 X 坐标", "71.00mm"), ("用户东向 Y 坐标", "92.50毫米"), ("用户高程 H", UNKNOWN)):
            self.assertEqual(value, coordinates[1][coordinates[0].index(field)])
        heights = self.copies(done, md, "原高程与复测高程分组 1")
        self.assertEqual("12.50mm", heights[1][heights[0].index("用户原高程")])
        self.assertEqual("12.40mm", heights[1][heights[0].index("用户复测高程")])
        self.assertEqual([UNKNOWN, UNKNOWN], heights[1][-2:])

    def test_survey_datums_units_raw_heights_and_blank_signature_copies(self):
        md, done = self.markdown("生成复测记录；点号：BM01；原高程：12.486；复测高程：12.483；高程基准：1985；高程单位：m；观测：王甲；点号：BM02；原高程：13052；复测高程：13061；高程基准：SHD；高程单位：mm", "survey", confirm_text=ACK)
        tables = dict(tables_from_md(md))
        names = [name for name in tables if name.startswith("原高程与复测高程分组")]
        self.assertEqual(2, len(names))
        for name in names:
            copied = self.copies(done, md, name)
            self.assertEqual(2, len(copied))
            self.assertEqual([UNKNOWN, UNKNOWN], copied[1][-2:])
        sign = self.copies(done, md, "11 附录与签认空栏")
        self.assertTrue(all(row[1:] == ["（空栏）"]*4 for row in sign[1:]))

    def test_formula_markup_and_encoded_pipes_are_only_literal_office_text(self):
        import openpyxl
        for post, message, title in (
            ("env", "|活动|数量|\n|---|---|\n|甲&amp;amp;乙&#124;丙|=1+2|", TITLES["env"]),
            ("supervision", "|问题|期限|\n|---|---|\n|甲&amp;amp;乙&#124;丙|=1+2|", TITLES["supervision"]),
            ("survey", "|点号|X|\n|---|---|\n|甲&amp;amp;乙&#124;丙|=1+2|", TITLES["survey"]),
        ):
            self.sid = "sr-formula-"+uuid4().hex[:16]
            md, done = self.markdown("生成内部草稿\n"+message, post, confirm_text=ACK)
            table = self.copies(done, md, title)
            self.assertIn("=1+2", table[1])
            artifact = next(a for a in done["deliverables"] if a["name"].endswith(".xlsx"))
            result = self.client.get("/api/file", params={"path": artifact["path"]})
            book = openpyxl.load_workbook(BytesIO(result.content))
            try:
                self.assertFalse(any(c.data_type == "f" for sheet in book for row in sheet for c in row))
            finally:
                book.close()

    def test_survey_public_gate_requires_exact_phrase_not_boolean_or_partial(self):
        for fields in ({}, {"confirm_ok": True}, {"confirm_text": "我明白"}):
            self.sid = "sr-gate-"+uuid4().hex[:16]
            done, _ = self.post("生成测量记录；点号：BM01；X：71.400", expert_ids=["survey"], **fields)
            self.assertFalse(done["wrote"], done)
            self.assertEqual([], done["deliverables"])
            self.assertIn(ACK, done["text"])
        self.sid = "sr-gate-ok-"+uuid4().hex[:16]
        self.markdown("生成测量记录；点号：BM01；X：71.400", "survey", confirm_text=ACK)

    def test_chat_sibling_and_readonly_gates_prevent_files_for_all_three_posts(self):
        import os
        from unittest.mock import patch
        from packing_assistant.runtime.tool_engine import default_engine
        for owner, tool in TOOLS.items():
            self.sid = "sr-chat-"+uuid4().hex[:16]
            done, _ = self.post("这个岗位缺什么资料？只解释，不生成文件。", expert_ids=[owner])
            self.assertFalse(done["wrote"])
            self.assertEqual([], done["deliverables"])
            for expert, intent in ((owner, "chat"), ("finance-tax", "run")):
                denied = default_engine().execute(tool, {"text": "生成内部草稿", "session_id": self.sid, "confirm_ok": True}, expert_id=expert, intent=intent)
                self.assertFalse(denied["ok"])
                self.assertEqual("permission_denied", denied["error_code"])
            with patch.dict(os.environ, {"CIVIL_SANDBOX": "read-only"}):
                denied = default_engine().execute(tool, {"text": "生成内部草稿", "session_id": self.sid, "confirm_ok": True}, expert_id=owner, intent="run")
                self.assertFalse(denied["ok"])
                self.assertFalse(denied.get("wrote", False))
        self.no_model.assert_not_called()


if __name__ == "__main__":
    unittest.main()
