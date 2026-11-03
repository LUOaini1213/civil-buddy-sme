#!/usr/bin/env python3
"""Site discussion templates through upload, restore and actual Office downloads."""
from io import BytesIO
from pathlib import Path
import sys
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import test_hazard_bim_http_acceptance as hb
from packing_assistant.office_job import tables_from_md
from packing_assistant.post_drafts.site_briefs import TOOLS, UNKNOWN

ACK = "我明白，将由持证人员签认"
TITLES = {"construction": "施工参数与现场条件", "emergency": "风险场景与现场资源", "worker-brief": "危险点与交叉作业"}


class SiteBriefHTTPAcceptance(unittest.TestCase):
    setUp = hb.HazardBIMHttpAcceptance.setUp
    post = hb.HazardBIMHttpAcceptance.post
    markdown = hb.HazardBIMHttpAcceptance.markdown
    spreadsheet_rows = hb.HazardBIMHttpAcceptance.spreadsheet_rows
    word_table_rows = hb.HazardBIMHttpAcceptance.word_table_rows
    copies = hb.HazardBIMHttpAcceptance.copies
    upload = hb.HazardBIMHttpAcceptance.upload

    def test_natural_parameters_are_in_the_same_columns_in_word_and_excel(self):
        cases = (
            ("construction", "生成专项方案讨论稿。云杉园4#楼地下室顶板，支模高度9.2米，板厚275，最大梁截面550×1300，架体用盘扣式，图纸是结施-21。",
             "支模高度", "9.2米"),
            ("emergency", "生成应急预案提纲。云杉园基坑开挖阶段，主要风险是边坡失稳，现场处置由安全部张明牵头，现场备了3台PC210挖机和24袋编织袋，2026-12-08打算组织一次专项演练。",
             "应急物资", "3台PC210挖机和24袋编织袋"),
            ("worker-brief", "写一段班前口播。钢筋班今天上午在6#楼九层绑梁筋，紧挨着电梯井口，防护没了先停，找带班张明，再找安全员李慧。",
             "危险点", "电梯井口"),
        )
        for post, message, field, expected in cases:
            with self.subTest(post=post):
                self.sid = "site-natural-"+uuid4().hex[:16]
                md, done = self.markdown(message, post, confirm_text=ACK)
                table = self.copies(done, md, TITLES[post])
                self.assertEqual(expected, table[1][table[0].index(field)])
                self.assertTrue(done["submit_blocked"])
                self.no_model.assert_not_called()

    def test_selected_csv_xlsx_and_word_matrices_preserve_row_boundaries_and_bytes(self):
        import html
        import openpyxl
        from zipfile import ZipFile
        cases = (
            ("construction", [["支模高度", "部位", "板厚"], ["9.2m", "A", "275"], ["11m", "", ""]],
             "支模高度", ["9.2m", "11m"], "作业部位"),
            ("emergency", [["应急物资", "场景", "演练时间"], ["担架2副", "A", "11月8号"], ["检测仪", "", ""]],
             "应急物资", ["担架2副", "检测仪"], "场景"),
            ("worker-brief", [["危险", "班组", "部位"], ["洞口", "班甲", "A"], ["交叉作业", "", ""]],
             "危险点", ["洞口", "交叉作业"], "班组"),
        )
        for post, rows, field, expected, identity in cases:
            book = openpyxl.Workbook()
            try:
                for row in rows:
                    book.active.append(row)
                xlsx = BytesIO()
                book.save(xlsx)
            finally:
                book.close()
            word = BytesIO()
            table_xml = "<w:tbl>"+"".join("<w:tr>"+"".join(
                "<w:tc><w:p><w:r><w:t>"+html.escape(value)+"</w:t></w:r></w:p></w:tc>" for value in row
            )+"</w:tr>" for row in rows)+"</w:tbl>"
            with ZipFile(word, "w") as archive:
                archive.writestr("word/document.xml",
                                 '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'+
                                 table_xml+"</w:body></w:document>")
            csv_bytes = ("\n".join(",".join(row) for row in rows)+"\n").encode("utf-8")
            for extension, data in (("csv", csv_bytes), ("xlsx", xlsx.getvalue()), ("docx", word.getvalue())):
                with self.subTest(post=post, extension=extension):
                    self.sid = "site-office-"+uuid4().hex[:16]
                    response = self.client.post("/api/upload", data={"session_id": self.sid},
                                                files={"files": ("合成场景."+extension, data, "application/octet-stream")})
                    self.assertEqual(200, response.status_code, response.text)
                    identifier = response.json()["files"][0]["id"]
                    original, _ = hb.flow.uploads.upload_file(self.sid, identifier)
                    md, done = self.markdown("请按选中附件生成本岗内部讨论稿。", post,
                                             attachments=[identifier], confirm_text=ACK)
                    table = self.copies(done, md, TITLES[post])
                    self.assertEqual(expected, [r[table[0].index(field)] for r in table[1:]])
                    self.assertEqual(UNKNOWN, table[2][table[0].index(identity)])
                    self.assertEqual(data, original.read_bytes())

    def test_two_files_do_not_lend_people_materials_or_dimensions(self):
        cases = (
            ("construction", "部位,支模高度,板厚\nA,9.2m,275\n", "部位,支模高度\nB,\n", "支模高度", "9.2m"),
            ("emergency", "场景,应急物资,现场处置负责人\nA,担架2副,张工\n", "场景,应急物资\nB,\n", "应急物资", "担架2副"),
            ("worker-brief", "班组,危险,安全员\n班甲,洞口,张工\n", "班组,危险\n班乙,\n", "危险点", "洞口"),
        )
        for post, first, second, field, expected in cases:
            with self.subTest(post=post):
                self.sid = "site-files-"+uuid4().hex[:16]
                uploaded = [self.upload(name, text) for name, text in (("甲.csv", first), ("乙.csv", second))]
                md, done = self.markdown("请按两个选中附件分别整理本岗草稿，不借值。", post,
                                         attachments=[item[0] for item in uploaded], confirm_text=ACK)
                table = self.copies(done, md, TITLES[post])
                self.assertEqual(expected, table[1][table[0].index(field)])
                self.assertEqual(UNKNOWN, table[2][table[0].index(field)])
                for _, original, data in uploaded:
                    self.assertEqual(data, original.read_bytes())

    def test_construction_unknown_jurisdiction_has_one_real_structured_word_copy(self):
        md, done = self.markdown("请生成专项方案讨论稿；部位：A；板厚：275；支模高度：9.2m。", "construction", confirm_text=ACK)
        self.assertIn("辖区：UNSPECIFIED", md)
        self.assertNotIn("住建部令第", md)
        words = [item for item in done["deliverables"] if item["name"].endswith(".docx")]
        self.assertEqual(1, len(words), done)
        table = self.copies(done, md, TITLES["construction"])
        self.assertEqual("275", table[1][table[0].index("板厚")])
        # The registry reports actual Word completion, not the old external
        # template-helper pending flag despite a usable common Word copy.
        for zone in ("CN", "SG", "EU", "DUAL"):
            self.sid = "site-zone-"+uuid4().hex[:16]
            md, done = self.markdown("生成专项方案讨论稿；总体辖区："+zone+"；部位：A；板厚：275。", "construction", confirm_text=ACK)
            self.assertIn("辖区："+zone, md)
            self.copies(done, md, TITLES["construction"])
            self.assertEqual(1, sum(item["name"].endswith(".docx") for item in done["deliverables"]))

    def test_high_risk_exact_ack_and_all_three_question_turns_do_not_write(self):
        for post in TOOLS:
            with self.subTest(post=post):
                self.sid = "site-chat-"+uuid4().hex[:16]
                done, _ = self.post("本岗还缺哪些资料？只解释，不生成文件。", expert_ids=[post])
                self.assertFalse(done["wrote"])
                self.assertEqual([], done["deliverables"])
        for post in ("construction", "emergency"):
            for fields in ({}, {"confirm_ok": True}, {"p0_confirmed": True}, {"confirm_text": ACK[:-1]}):
                with self.subTest(post=post, fields=fields):
                    self.sid = "site-gate-"+uuid4().hex[:16]
                    done, _ = self.post("生成本岗内部讨论草稿。", expert_ids=[post], **fields)
                    self.assertFalse(done["wrote"], done)
                    self.assertEqual([], done["deliverables"])
                    self.assertIn(ACK, done["text"])
        self.no_model.assert_not_called()

    def test_planned_drill_never_becomes_a_completed_record_in_downloads(self):
        md, done = self.markdown("生成应急预案提纲；场景：清淤；演练时间：11月8号；演练科目：撤离。", "emergency", confirm_text=ACK)
        planned = self.copies(done, md, "拟演练计划")
        actual = self.copies(done, md, "用户实际演练记录待核")
        self.assertEqual("11月8号", planned[1][planned[0].index("演练时间")])
        for field in ("实际演练日期", "演练记录", "本轮评估结论"):
            self.assertEqual(UNKNOWN, actual[1][actual[0].index(field)])
        self.assertEqual("", actual[1][-1])

    def test_worker_unavailable_person_and_unsourced_size_do_not_enter_spoken_instructions(self):
        md, done = self.markdown("生成班前白话稿；班组：焊工班；部位：A；用户尺寸：1200mm；安全员：老周\n安全员老周说他明天不在场",
                                 "worker-brief")
        spoken = md.split("## 班前口播", 1)[1].split("## 对象依据", 1)[0]
        self.assertNotIn("1200mm", spoken)
        self.assertNotIn("老周", spoken)
        table = self.copies(done, md, "喊停与报告对象")
        self.assertEqual(UNKNOWN, table[1][table[0].index("安全员")])
        source = self.copies(done, md, "防护动作与来源")
        self.assertEqual("1200mm", source[1][source[0].index("用户尺寸")])

    def test_formula_and_markup_are_literal_text_in_generated_copies(self):
        md, done = self.markdown("生成专项方案讨论稿\n|部位|板厚|\n|---|---|\n|=A&#124;B &lt; 9|字面&amp;lt;|", "construction", confirm_text=ACK)
        table = self.copies(done, md, TITLES["construction"])
        self.assertEqual("=A|B < 9", table[1][0])
        self.assertEqual("字面&lt;", table[1][table[0].index("板厚")])
        import openpyxl
        artifact = next(item for item in done["deliverables"] if item["name"].endswith(".xlsx"))
        book = openpyxl.load_workbook(BytesIO(self.client.get("/api/file", params={"path": artifact["path"]}).content))
        try:
            self.assertFalse(any(c.data_type == "f" for sheet in book for row in sheet for c in row))
        finally:
            book.close()

    def test_named_tools_cannot_write_in_readonly_chat_or_sibling_context(self):
        import os
        from unittest.mock import patch
        from packing_assistant import expert_turn
        from packing_assistant.runtime.tool_engine import default_engine
        engine = default_engine()
        for post, tool in {**TOOLS, "construction-fill": "construction__fill_scheme_docx"}.items():
            owner = "construction" if post == "construction-fill" else post
            with self.subTest(post=post):
                for expert, intent in ((owner, "chat"), ("hr-labor", "run")):
                    denied = engine.execute(tool, {"text": "生成本岗讨论稿", "confirm_ok": True}, expert_id=expert, intent=intent)
                    self.assertFalse(denied["ok"], denied)
                with patch.dict(os.environ, {"CIVIL_SANDBOX": "read-only"}):
                    denied = expert_turn.run_named_exclusive(tool, {"text": "生成本岗讨论稿", "confirm_ok": True, "session_id": self.sid})
                    self.assertFalse(denied["wrote"], denied)
                    self.assertEqual([], denied["files"])
        self.assertFalse(list(self.root.rglob("*.docx")))


if __name__ == "__main__":
    unittest.main()
