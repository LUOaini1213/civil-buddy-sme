#!/usr/bin/env python3
"""Actual HTTP upload/restore/download journeys for hazard and BIM drafts."""
from io import BytesIO
from pathlib import Path
import sys
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import test_finale_http_acceptance as finale
from scripts import test_workbench_flow as flow
from packing_assistant.office_job import tables_from_md

ACK = "我明白，将由持证人员签认"
UNKNOWN = "UNSPECIFIED"
TITLES = {
    "method-hazard": "3 尺寸与触发事实", "bim-coord": "协调问题责任与版本对照",
    "bim-qto": "算量模型与规则来源对照", "bim-deliver": "模型交付目的与期限",
}


class HazardBIMHttpAcceptance(unittest.TestCase):
    setUp = flow.WorkbenchFlowTests.setUp
    post = flow.WorkbenchFlowTests.post
    markdown = finale.FinaleHttpAcceptance.markdown
    spreadsheet_rows = finale.FinaleHttpAcceptance.spreadsheet_rows
    word_table_rows = finale.FinaleHttpAcceptance.word_table_rows

    def copies(self, done, md, title):
        table = dict(tables_from_md(md))[title]
        self.assertEqual(table, self.spreadsheet_rows(done, title))
        self.assertEqual(table, self.word_table_rows(done, table[0], title=title))
        self.assertTrue(done["submit_blocked"])
        return table

    def upload(self, name, text):
        data = text.encode("utf-8")
        response = self.client.post("/api/upload", data={"session_id": self.sid},
                                    files={"files": (name, data, "text/csv")})
        self.assertEqual(200, response.status_code, response.text)
        identifier = response.json()["files"][0]["id"]
        original, _ = flow.uploads.upload_file(self.sid, identifier)
        return identifier, original, data

    def test_natural_values_reach_the_same_professional_columns_in_both_copies(self):
        cases = (
            ("method-hazard", "生成危大判定资料卡。一个是3#楼地下室顶板高支模，支模高度10.7米，最大跨度19.3米，尺寸都是从结施-H07图看的",
             "支模高度", "10.7米"),
            ("bim-coord", "生成协调纪要。结构模型V3，一条是B1层车库入口排烟风管与KL-7梁硬碰，问题编号 Q-19，暖通张明负责，要求周五前改完模型",
             "责任人", "张明"),
            ("bim-qto", "生成模型算量口径说明。结构模型V3，地下室剪力墙的混凝土体积，材质只要C30的，明细表导出来是712.30立方",
             "用户数量原记录（未核算）", "712.30立方"),
            ("bim-deliver", "生成BIM交付清单。结构模型要到LOD350，施工深化阶段，要交给业主，格式交IFC4，周五前交完",
             "用户LOD", "LOD350"),
        )
        for post, text, key, expected in cases:
            with self.subTest(post=post):
                self.sid = "hb-natural-"+uuid4().hex[:16]
                md, done = self.markdown(text, post, confirm_text=ACK)
                table = self.copies(done, md, TITLES[post])
                self.assertEqual(expected, table[1][table[0].index(key)])
                if post != "method-hazard":
                    self.assertEqual("", table[1][-1])
                self.no_model.assert_not_called()

    def test_reordered_selected_csv_rows_keep_blank_identifiers_and_original_bytes(self):
        cases = (
            ("method-hazard", "开挖深度,作业,尺寸来源\n6.8m,坑甲,JK-A\n9.1m,,\n", "开挖深度", ["6.8m", "9.1m"], "作业名称"),
            ("bim-coord", "责任人,问题编号,问题描述\n张工,Q-A,净高不足\n李工,,预留洞待核\n", "责任人", ["张工", "李工"], "问题编号"),
            ("bim-qto", "数量,构件类别,单位,模型\n0,墙,m3,A\n7.5,,件,B\n", "用户数量原记录（未核算）", ["0", "7.5"], "构件类别"),
            ("bim-deliver", "版本,模型,目的\nV7,A,深化\n,B,资产管理\n", "交付目的", ["深化", "资产管理"], "用户版本"),
        )
        for post, source, key, expected, missing in cases:
            with self.subTest(post=post):
                self.sid = "hb-csv-"+uuid4().hex[:16]
                identifier, original, data = self.upload("合成验收.csv", source)
                md, done = self.markdown("请按选中资料生成本岗内部草稿，缺项保持待填。", post,
                                         attachments=[identifier], confirm_text=ACK)
                table = self.copies(done, md, TITLES[post])
                self.assertEqual(expected, [row[table[0].index(key)] for row in table[1:]])
                self.assertEqual(UNKNOWN, table[2][table[0].index(missing)])
                self.assertEqual(data, original.read_bytes())

    def test_two_selected_files_do_not_lend_parameters_basis_or_version(self):
        cases = (
            ("method-hazard", "作业,开挖深度,尺寸来源,辖区\n甲,6.8m,JK-A,CN\n", "作业\n乙\n",
             TITLES["method-hazard"], "作业名称", "开挖深度", "6.8m"),
            ("bim-coord", "模型,版本,来源\nA,V7,a.csv\n", "模型\nB\n",
             "模型单体与版本来源", "模型", "模型版本", "V7"),
            ("bim-qto", "模型,构件类别,数量,单位,来源\nA,墙,712.30,m3,a.csv\n", "模型,构件类别\nB,梁\n",
             TITLES["bim-qto"], "构件类别", "用户数量原记录（未核算）", "712.30"),
            ("bim-deliver", "模型,版本,LOD,交付对象\nA,V7,LOD350,业主\n", "模型\nB\n",
             TITLES["bim-deliver"], "模型", "用户版本", "V7"),
        )
        for post, first, second, title, identity, key, expected in cases:
            with self.subTest(post=post):
                self.sid = "hb-files-"+uuid4().hex[:16]
                # CSV needs at least two known columns to be recognized as a
                # literal matrix; retain an explicitly blank version/depth.
                second = second.replace("作业\n乙\n", "作业,开挖深度\n乙,\n").replace("模型\nB\n", "模型,版本\nB,\n")
                uploaded = [self.upload(name, text) for name, text in (("甲.csv", first), ("乙.csv", second))]
                md, done = self.markdown("请按这两个选中附件生成本岗草稿，分别归属，不借用相邻资料。", post,
                                         attachments=[item[0] for item in uploaded], confirm_text=ACK)
                table = self.copies(done, md, title)
                self.assertEqual(expected, table[1][table[0].index(key)])
                self.assertEqual(UNKNOWN, table[2][table[0].index(key)])
                self.assertEqual("乙" if post == "method-hazard" else "梁" if post == "bim-qto" else "B",
                                 table[2][table[0].index(identity)])
                for _, original, data in uploaded:
                    self.assertEqual(data, original.read_bytes())

    def test_explicit_model_reference_exports_only_the_matching_version(self):
        text = "生成模型算量口径说明。模型：A；版本：V7；来源：a.csv\n模型：B；版本：V9\n构件类别：墙；关联模型：A；数量：0"
        md, done = self.markdown(text, "bim-qto")
        title = "对象与模型版本关联（仅明确归属）"
        table = self.copies(done, md, title)
        self.assertEqual("V7", table[1][table[0].index("关联模型用户版本")])
        self.assertEqual("a.csv", table[1][table[0].index("关联模型用户来源")])
        self.assertNotIn("V9", table[1])
        counts = dict(tables_from_md(md))["4 用户数量登记表"]
        self.assertEqual("0", counts[1][4])
        self.assertEqual(["TBD", "TBD"], counts[1][8:10])

    def test_questions_never_write_and_boolean_or_partial_ack_cannot_open_hazard_gate(self):
        for post in TITLES:
            with self.subTest(post=post):
                self.sid = "hb-chat-"+uuid4().hex[:16]
                done, _ = self.post("这个岗位缺哪些资料？只解释，不生成文件。", expert_ids=[post])
                self.assertFalse(done["wrote"])
                self.assertEqual([], done["deliverables"])
        for fields in ({}, {"confirm_ok": True}, {"p0_confirmed": True}, {"confirm_text": ACK[:-1]}):
            with self.subTest(fields=fields):
                self.sid = "hb-gate-"+uuid4().hex[:16]
                done, _ = self.post("请生成危大判定资料卡；作业：高支模；支模高度：10m。",
                                    expert_ids=["method-hazard"], **fields)
                self.assertFalse(done["wrote"], done)
                self.assertEqual([], done["deliverables"])
                self.assertIn(ACK, done["text"])
        self.no_model.assert_not_called()

    def test_formula_and_encoded_markup_are_literal_not_excel_formulas(self):
        for post in ("method-hazard", "bim-deliver"):
            with self.subTest(post=post):
                self.sid = "hb-markup-"+uuid4().hex[:16]
                header = "作业|高度" if post == "method-hazard" else "模型|目的"
                md, done = self.markdown("生成本岗内部草稿\n|"+header+"|\n|---|---|\n|=A&#124;B &lt; 9|字面&amp;lt;|",
                                         post, confirm_text=ACK)
                table = self.copies(done, md, TITLES[post])
                self.assertEqual("=A|B < 9", table[1][0])
                self.assertIn("字面&lt;", table[1])
                artifact = next(item for item in done["deliverables"] if item["name"].endswith(".xlsx"))
                import openpyxl
                book = openpyxl.load_workbook(BytesIO(self.client.get("/api/file", params={"path": artifact["path"]}).content))
                try:
                    cells = [cell for sheet in book for row in sheet for cell in row]
                    self.assertIn("=A|B < 9", [cell.value for cell in cells])
                    self.assertFalse(any(cell.data_type == "f" for cell in cells))
                finally:
                    book.close()

    def test_hazard_judgments_remain_pending_even_with_user_positive_claim(self):
        md, done = self.markdown("生成危大判定资料卡；作业：高支模；支模高度：10m；已有判定：用户称已认定危大。",
                                 "method-hazard", confirm_text=ACK)
        table = self.copies(done, md, "7 结论待判与方案交接")
        self.assertEqual(["信息不足"]*3, table[1][2:5])
        self.assertEqual("", table[1][-1])
        claimed = dict(tables_from_md(md))["5 已有人工判定与证据"]
        self.assertEqual("用户称已认定危大", claimed[1][1])
        self.assertEqual(UNKNOWN, claimed[1][3])

    def test_selected_xlsx_and_word_tables_have_the_same_independent_rows(self):
        import html
        import openpyxl
        from zipfile import ZipFile
        cases = (
            ("method-hazard", [["开挖深度", "作业", "尺寸来源"], ["6.8m", "坑甲", "JK-A"], ["9.1m", "", ""]],
             "开挖深度", ["6.8m", "9.1m"]),
            ("bim-coord", [["责任人", "问题编号", "问题描述"], ["张工", "Q-A", "净高不足"], ["李工", "", "洞口待核"]],
             "责任人", ["张工", "李工"]),
            ("bim-qto", [["数量", "构件类别", "模型", "单位"], ["0", "墙", "A", "m3"], ["7.5", "", "B", "件"]],
             "用户数量原记录（未核算）", ["0", "7.5"]),
            ("bim-deliver", [["版本", "模型", "目的"], ["V7", "A", "深化"], ["", "B", "资产管理"]],
             "交付目的", ["深化", "资产管理"]),
        )
        for post, source_rows, key, expected in cases:
            book = openpyxl.Workbook()
            try:
                for row in source_rows:
                    book.active.append(row)
                xlsx = BytesIO()
                book.save(xlsx)
            finally:
                book.close()
            docx = BytesIO()
            table_xml = "<w:tbl>"+"".join("<w:tr>"+"".join(
                "<w:tc><w:p><w:r><w:t>"+html.escape(value)+"</w:t></w:r></w:p></w:tc>" for value in row
            )+"</w:tr>" for row in source_rows)+"</w:tbl>"
            with ZipFile(docx, "w") as archive:
                archive.writestr("word/document.xml",
                                 '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'+
                                 table_xml+"</w:body></w:document>")
            for extension, data in (("xlsx", xlsx.getvalue()), ("docx", docx.getvalue())):
                with self.subTest(post=post, extension=extension):
                    self.sid = "hb-office-"+uuid4().hex[:16]
                    response = self.client.post("/api/upload", data={"session_id": self.sid},
                                                files={"files": ("合成验收."+extension, data, "application/octet-stream")})
                    self.assertEqual(200, response.status_code, response.text)
                    identifier = response.json()["files"][0]["id"]
                    original, _ = flow.uploads.upload_file(self.sid, identifier)
                    md, done = self.markdown("请按选中附件生成本岗内部草稿，空栏不借值。", post,
                                             attachments=[identifier], confirm_text=ACK)
                    table = self.copies(done, md, TITLES[post])
                    self.assertEqual(expected, [row[table[0].index(key)] for row in table[1:]])
                    self.assertEqual(data, original.read_bytes())

    def test_named_tool_denies_chat_sibling_readonly_and_unconfirmed_hazard(self):
        import os
        from unittest.mock import patch
        from packing_assistant import expert_turn
        from packing_assistant.post_drafts.hazard import TOOL
        from packing_assistant.post_drafts.bim import _TOOLS
        from packing_assistant.runtime.tool_engine import default_engine
        engine = default_engine()
        for post, tool in {"method-hazard": TOOL, **_TOOLS}.items():
            with self.subTest(post=post):
                for expert, intent in ((post, "chat"), ("hr-labor", "run")):
                    denied = engine.execute(tool, {"text": "生成本岗内部草稿", "confirm_ok": True},
                                            expert_id=expert, intent=intent)
                    self.assertFalse(denied["ok"], denied)
                with patch.dict(os.environ, {"CIVIL_SANDBOX": "read-only"}):
                    denied = expert_turn.run_named_exclusive(tool, {"text": "生成本岗内部草稿", "confirm_ok": True,
                                                                    "session_id": self.sid})
                    self.assertFalse(denied["wrote"], denied)
                    self.assertEqual([], denied["files"])
        blocked = expert_turn.run_named_exclusive(TOOL, {"text": "作业：高支模；支模高度：9m", "session_id": self.sid})
        self.assertFalse(blocked["wrote"])
        self.assertTrue(blocked["hitl_pending"])
        self.assertFalse(list(self.root.rglob("*.md")))
        self.no_model.assert_not_called()


if __name__ == "__main__":
    unittest.main()
