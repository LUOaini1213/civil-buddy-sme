#!/usr/bin/env python3
"""HR source selection and real Word/Excel downloads through workbench HTTP."""
from io import BytesIO
from pathlib import Path
import sys
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import test_hazard_bim_http_acceptance as hb
from packing_assistant.office_job import tables_from_md
from packing_assistant.post_drafts.hr_observations import TOOLS, UNKNOWN

TITLES = {"hr-recruit": "逐岗招聘需求（用户资料未核）", "hr-train": "逐批培训计划（不是完成记录）",
          "hr-labor": "逐人合同登记（关系与条款待核）"}


class HRHTTPAcceptance(unittest.TestCase):
    setUp = hb.HazardBIMHttpAcceptance.setUp
    post = hb.HazardBIMHttpAcceptance.post
    markdown = hb.HazardBIMHttpAcceptance.markdown
    spreadsheet_rows = hb.HazardBIMHttpAcceptance.spreadsheet_rows
    word_table_rows = hb.HazardBIMHttpAcceptance.word_table_rows
    copies = hb.HazardBIMHttpAcceptance.copies
    upload = hb.HazardBIMHttpAcceptance.upload

    def test_natural_hr_facts_download_in_their_own_columns(self):
        cases = (
            ("hr-recruit", "请生成招聘简报。资料员1个，资料室要的，月薪谈到8300，11月2号之前到岗。", "用户薪资", "8300"),
            ("hr-labor", "请生成合同检查表。一个是电工赵明，月工资9100，2026-11-02进的场。", "劳动者", "赵明"),
            ("hr-train", "请生成培训计划。塔吊司机和信号工那拨13个人，要的是特种作业证复审前再教育，9月29号下午讲，讲师请了市里的王明。", "计划讲师", "王明"),
        )
        for post, text, key, expected in cases:
            with self.subTest(post=post):
                self.sid = "hr-natural-"+uuid4().hex[:16]
                md, done = self.markdown(text, post)
                table = self.copies(done, md, TITLES[post])
                self.assertEqual(expected, table[1][table[0].index(key)])
                self.assertEqual([UNKNOWN, "（空栏）"], table[1][-2:])

    def test_selected_csv_excel_and_word_keep_empty_and_reordered_rows(self):
        import html
        import openpyxl
        from zipfile import ZipFile
        cases = (
            ("hr-recruit", [["薪资", "岗位", "计划到岗"], ["8100", "资料员", "11月2号"], ["9200", "", ""]], "用户薪资", "岗位"),
            ("hr-labor", [["工资", "劳动者", "合同期限"], ["8100", "王甲", "三年"], ["9200", "", ""]], "用户工资", "劳动者"),
            ("hr-train", [["人数", "培训对象", "讲师"], ["13", "班甲", "王明"], ["21", "", ""]], "人数", "培训对象"),
        )
        for post, rows, key, identity in cases:
            book = openpyxl.Workbook()
            try:
                for row in rows:
                    book.active.append(row)
                binary = BytesIO()
                book.save(binary)
            finally:
                book.close()
            word = BytesIO()
            xml = "<w:tbl>"+"".join("<w:tr>"+"".join("<w:tc><w:p><w:r><w:t>"+html.escape(value)+"</w:t></w:r></w:p></w:tc>" for value in row)+"</w:tr>" for row in rows)+"</w:tbl>"
            with ZipFile(word, "w") as archive:
                archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'+xml+"</w:body></w:document>")
            csv = ("\n".join(",".join(row) for row in rows)+"\n").encode("utf-8")
            for ext, data in (("csv", csv), ("xlsx", binary.getvalue()), ("docx", word.getvalue())):
                with self.subTest(post=post, extension=ext):
                    self.sid = "hr-office-"+uuid4().hex[:16]
                    response = self.client.post("/api/upload", data={"session_id": self.sid}, files={"files": ("合成HR."+ext, data, "application/octet-stream")})
                    self.assertEqual(200, response.status_code, response.text)
                    identifier = response.json()["files"][0]["id"]
                    original, _ = hb.flow.uploads.upload_file(self.sid, identifier)
                    md, done = self.markdown("请按选中附件生成本岗内部草稿。", post, attachments=[identifier])
                    table = self.copies(done, md, TITLES[post])
                    self.assertEqual(3, len(table))
                    self.assertEqual([rows[1][0], rows[2][0]], [r[table[0].index(key)] for r in table[1:]])
                    self.assertEqual(UNKNOWN, table[2][table[0].index(identity)])
                    self.assertEqual(data, original.read_bytes())

    def test_two_selected_files_do_not_lend_pay_lecturers_or_dates(self):
        cases = (
            ("hr-recruit", "岗位,薪资\n资料员,8100\n", "岗位,薪资\n施工员,\n", "用户薪资", "8100"),
            ("hr-labor", "劳动者,工资,合同期限\n王甲,8100,三年\n", "劳动者,工资\n王乙,\n", "用户工资", "8100"),
            ("hr-train", "培训对象,讲师,培训日期\n班甲,王明,2026-11-02\n", "培训对象,讲师\n班乙,\n", "计划讲师", "王明"),
        )
        for post, first, second, key, expected in cases:
            with self.subTest(post=post):
                self.sid = "hr-files-"+uuid4().hex[:16]
                uploads = [self.upload(name, text) for name, text in (("甲.csv", first), ("乙.csv", second))]
                md, done = self.markdown("请把两个选中附件分别整理成本岗草稿，不借值。", post, attachments=[u[0] for u in uploads])
                table = self.copies(done, md, TITLES[post])
                self.assertEqual(expected, table[1][table[0].index(key)])
                self.assertEqual(UNKNOWN, table[2][table[0].index(key)])
                for _, original, data in uploads:
                    self.assertEqual(data, original.read_bytes())

    def test_training_plan_does_not_fill_actual_attendance_or_result(self):
        md, done = self.markdown("生成培训计划；培训对象：班甲；培训日期：2026-11-02；公司级学时：6小时；参训人员：王甲、王乙；讲师：李明", "hr-train")
        sign = self.copies(done, md, "6 培训签到空表")
        self.assertTrue(all(cell == "（空栏）" for row in sign[1:] for cell in row[1:]))
        actual = self.copies(done, md, "用户实际培训材料（摘录待核，不作完成结论）")
        self.assertEqual([UNKNOWN]*6, actual[1])

    def test_actual_training_evidence_unverified_and_no_new_signature(self):
        md, done = self.markdown("生成培训计划；培训对象：班甲；实际培训日期：2026-11-02；实际学时：5小时；签到记录：现场签到扫描件", "hr-train")
        table = self.copies(done, md, "用户实际培训材料（摘录待核，不作完成结论）")
        self.assertEqual("5小时", table[1][1])
        self.assertEqual(UNKNOWN, table[1][-1])
        self.assertNotIn("考核已合格", md)

    def test_unknown_update_not_published_as_current_salary(self):
        for post, message, key in (("hr-recruit", "岗位：资料员；薪资：8100；薪资：未批", "用户薪资"),
                                   ("hr-labor", "劳动者：王甲；工资：8100；工资：未定", "用户工资")):
            self.sid = "hr-unknown-"+uuid4().hex[:16]
            md, done = self.markdown("请生成本岗内部草稿；"+message, post)
            table = self.copies(done, md, TITLES[post])
            self.assertEqual(UNKNOWN, table[1][table[0].index(key)])

    def test_formula_markup_and_encoded_pipes_remain_literal_office_cells(self):
        import openpyxl
        md, done = self.markdown("生成合同检查表\n|劳动者|工资|\n|---|---|\n|王甲&amp;amp;乙&#124;丙|=1+2|", "hr-labor")
        table = self.copies(done, md, TITLES["hr-labor"])
        self.assertEqual("王甲&amp;乙|丙", table[1][table[0].index("劳动者")])
        self.assertEqual("=1+2", table[1][table[0].index("用户工资")])
        artifact = next(a for a in done["deliverables"] if a["name"].endswith(".xlsx"))
        result = self.client.get("/api/file", params={"path": artifact["path"]})
        book = openpyxl.load_workbook(BytesIO(result.content))
        try:
            self.assertTrue(all(cell.data_type != "f" for sheet in book for row in sheet for cell in row))
        finally:
            book.close()

    def test_chat_and_named_tool_sibling_readonly_gates(self):
        from packing_assistant.runtime.tool_engine import default_engine
        for owner, tool in TOOLS.items():
            self.sid = "hr-chat-"+uuid4().hex[:16]
            done, _ = self.post("这个岗位还缺哪些资料？只解释，不生成文件。", expert_ids=[owner])
            self.assertFalse(done["wrote"])
            self.assertEqual([], done["deliverables"])
            for expert, intent in ((owner, "chat"), ("finance-tax", "run")):
                denied = default_engine().execute(tool, {"text": "生成内部草稿", "session_id": self.sid}, expert_id=expert, intent=intent)
                self.assertFalse(denied["ok"])
                self.assertFalse(denied.get("wrote", False))
                self.assertEqual("permission_denied", denied["error_code"])
        self.no_model.assert_not_called()


if __name__ == "__main__":
    unittest.main()
