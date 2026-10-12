#!/usr/bin/env python3
"""Content-level regressions from the 2026-10-09 synthetic live acceptance."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packing_assistant import expert_turn
from packing_assistant.tools import tender_facts


class FinaleAcceptanceRegressions(unittest.TestCase):
    def test_colloquial_daily_facts_reach_the_report_not_only_the_source(self):
        md = expert_turn._pm_daily_md(
            "【本轮用户要求】\nSYNTHETIC 功能验收，不用于真实工程。"
            "请生成项目日报。项目：合成幕墙验收A；日期：2026-10-09；天气：晴；"
            "部位：东立面；今日完成：幕墙安装20块；工人：8人；问题：材料未到；"
            "明日计划：安装15块。缺失信息保持待填。"
        )
        self.assertIn("| 形象进度 | 幕墙安装20块 |", md)
        self.assertIn("| 机械材料 | 材料未到 |", md)
        self.assertIn("| 明日计划 | 安装15块 |", md)
        self.assertIn("用户提供的形象进度：幕墙安装20块", md.split("## 4 形象进度", 1)[1])

    def test_english_daily_has_populated_english_fields(self):
        md = expert_turn._pm_daily_md(
            "【本轮用户要求】\nSYNTHETIC acceptance test only. Generate a daily report. "
            "Project: Synthetic Facade A. Date: 2026-10-09. Weather: sunny. "
            "Location: east elevation. Work completed: installed 20 facade panels. "
            "Workers: 8. Issue: materials not delivered. Tomorrow: install 15 panels. "
            "Leave missing information unspecified."
        )
        self.assertIn("| Project | Synthetic Facade A |", md)
        self.assertIn("| Date | 2026-10-09 |", md)
        self.assertIn("| Work completed | installed 20 facade panels |", md)
        self.assertIn("| Workers | 8 |", md)
        self.assertIn("| Materials and equipment | materials not delivered |", md)
        self.assertIn("| Tomorrow | install 15 panels |", md)
        self.assertNotIn("待填", md)

    def test_unknown_daily_fields_do_not_turn_into_invented_values(self):
        md = expert_turn._pm_daily_md("请生成日报；项目名称：待填；今日完成：未知；明日计划：TBD")
        self.assertIn("| 项目名称 | 待填 |", md)
        self.assertIn("| 形象进度 | 待填 |", md)

    def test_daily_issues_are_not_lost_when_resources_are_already_present(self):
        cn = expert_turn._pm_daily_md("请生成日报；机械材料：吊机已到；问题：接口图未确认")
        en = expert_turn._pm_daily_md("Generate a daily report. Materials: crane delivered. Issues: interface drawing pending.")
        self.assertIn("| 机械材料 | 吊机已到 |", cn)
        self.assertIn("| 问题 | 接口图未确认 |", cn)
        self.assertIn("| Materials and equipment | crane delivered |", en)
        self.assertIn("| Issues | interface drawing pending |", en)

    def test_warehouse_specification_is_not_the_stock_unit(self):
        rows = expert_turn._parse_wh_rows(
            "【本轮用户要求】\nSYNTHETIC 功能验收。请生成仓库收发存台账草稿："
            "铝板 3mm 本周入库 120 张，领用 35 张，盘点差 2 张，仓管员张测试。"
            "没有期初库存，不要推算结存。"
        )
        self.assertEqual(len(rows), 1, rows)
        row = rows[0]
        self.assertEqual(row["name"], "铝板")
        self.assertEqual(row["spec"], "3mm")
        self.assertEqual(row["unit"], "张")
        self.assertEqual(row["inbound"], "120张")
        self.assertEqual(row["outbound"], "35张")
        self.assertNotIn("balance", row)

    def test_warehouse_labelled_record_is_not_lost(self):
        rows = expert_turn._parse_wh_rows(
            "请生成材料台账。\n材料名称：铝板\n规格：3mm\n数量：120张\n"
            "供应商：合成供应商甲\n批次：SYN-B001\n库位：一号仓"
        )
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["name"], "铝板")
        self.assertEqual(rows[0]["spec"], "3mm")
        self.assertEqual(rows[0]["unit"], "张")
        # Generic quantity has no declared movement: it must not be invented as inbound.
        self.assertNotIn("inbound", rows[0])
        self.assertEqual(rows[0]["quantity"], "120张")

    def test_warehouse_prefaces_do_not_discard_following_material_facts(self):
        cases = (
            "SYNTHETIC 请生成材料入库台账：材料名称：铝板；规格：3mm；数量：120张",
            "没有期初库存，铝板入库120张。不要推算结存。",
            "未提供期初库存；材料名称：铝板；入库数量：120张",
        )
        for text in cases:
            with self.subTest(text=text):
                rows = expert_turn._parse_wh_rows(text)
                self.assertEqual(len(rows), 1, rows)
                self.assertEqual(rows[0]["name"], "铝板")
                self.assertEqual(rows[0]["inbound"], "120张")
                self.assertNotIn("balance", rows[0])

    def test_request_instructions_are_not_tender_requirements(self):
        facts = tender_facts.extract(
            "请解析以下招标文本：项目名称：合成幕墙；招标编号：SYN-009；工期180日历天。"
            "未提供的资格、业绩、价格信息保持未检出，不作审批结论。"
        )
        self.assertFalse(any(m.topic == "track_record" and m.value for m in facts.mentions), facts.to_dict())

    def test_english_score_points_are_extracted(self):
        facts = tender_facts.extract("Technical proposal: 35 marks; Programme: 12 marks.")
        self.assertEqual([(p.name, p.score) for p in facts.scores],
                         [("Technical proposal", "35 marks"), ("Programme", "12 marks")])

    def test_english_document_scores_keep_decimals_and_each_factor(self):
        from packing_assistant.tools import tender_document
        doc = tender_document.read("# Invitation to Tender\n## Evaluation criteria\n"
                                   "Technical proposal: 35.5 marks. Programme: 12 marks.\n")
        self.assertEqual([(name, score) for name, score, _ in tender_document.scores(doc)],
                         [("Technical proposal", "35.5 marks"), ("Programme", "12 marks")])

    def test_english_tender_table_keeps_original_values_and_missing_fields(self):
        from packing_assistant.tools.tender_parse import parse_tender_text
        from packing_assistant.tools.tender_tables import extract_table_en
        md = extract_table_en(parse_tender_text("Project: Synthetic Facade A.\n"
                              "Contract duration: 180 calendar days.\nTechnical proposal: 35 marks. Programme: 12 marks."))
        self.assertIn("Tender requirements", md)
        self.assertIn("180 calendar days", md)
        self.assertIn("35 marks", md)
        self.assertIn("12 marks", md)
        self.assertIn("| Qualifications | Not detected in supplied text |", md)
        self.assertIn("submit_blocked=true", md)

    def test_content_oracle_accepts_translated_labels_not_wrong_fields(self):
        from scripts.eval_post_content import classify, spots
        fact = {"value": "180 days", "field": "工期"}
        right = "| Item | Source value / quotation |\n| --- | --- |\n| Contract duration | 180 days |"
        wrong = right.replace("Contract duration", "Tender validity")
        self.assertEqual(classify(fact, right, "", spots(right)), "placed")
        self.assertEqual(classify(fact, wrong, "", spots(wrong)), "misplaced")

    def test_english_export_preserves_document_records_and_lot_bindings(self):
        from packing_assistant.tools.tender_tables import extract_table_en
        parsed = {"facts": {
            "lots": ["Lot A", "Lot B"], "lot_scopes": {"Lot A": "East facade"},
            "scores": [{"name": "Programme", "score": "12 marks", "lot": "Lot B", "ref": "5.2"}],
            "unplaced": ["Inspections take place at 09:30."],
            "mentions": [{"topic": "duration", "value": "180 days", "ref": "2.1",
                          "origin": "公告与前附表不一致", "side": "tender"}],
        }, "handoff": {"document": {
            "review": [{"group": "Eligibility", "factor": "Registration", "standard": "CR16 required", "locator": "6.1"}],
            "remainder": [{"name": "Sample", "text": "Submit a colour sample", "locator": "2.8"}],
            "candidates": [{"text": "Late bids may be rejected", "locator": "4.3"}],
            "forms": [{"name": "Signed tender form", "locator": "7.1"}],
        }}}
        md = extract_table_en(parsed)
        for literal in ("Lot A", "East facade", "Programme (Lot B)", "12 marks", "5.2",
                        "CR16 required", "6.1", "Submit a colour sample", "2.8",
                        "Late bids may be rejected", "4.3", "Signed tender form", "7.1",
                        "Inspections take place at 09:30.", "SOURCE CONFLICT"):
            self.assertIn(literal, md)

    def test_response_side_continues_across_comma_but_not_a_new_tender_subject(self):
        text = ("招标要求工期180日历天、投标有效期90天。"
                "我们的草稿工期写成200日历天，投标有效期90天，保证金缴纳凭证尚未提供。")
        facts = tender_facts.extract(text)
        self.assertTrue(any(m.topic == "validity" and m.side == "ours" and m.value == "90天"
                            for m in facts.mentions), facts.to_dict())
        theirs, ours = tender_facts.split_sides(text)
        self.assertIn("投标有效期90天", ours)
        self.assertIn("180日历天", theirs)
        facts = tender_facts.extract("我们承诺工期200日历天，招标要求工期180日历天。")
        self.assertTrue(any(m.side == "tender" and m.value == "180日历天" for m in facts.mentions))

    def test_explicit_instruction_block_never_becomes_a_requirement(self):
        text = ("招标正文：\n工期150日历天。\n\n"
                "输出要求：\n不要编造业绩；缺失价格保持未检出。\n\n"
                "投标响应：\n工期170日历天。")
        facts = tender_facts.extract(text)
        self.assertTrue(any(m.topic == "duration" and m.side == "tender" and m.value == "150日历天"
                            for m in facts.mentions))
        self.assertTrue(any(m.topic == "duration" and m.side == "ours" and m.value == "170日历天"
                            for m in facts.mentions))
        self.assertFalse(any(m.topic == "track_record" for m in facts.mentions))

    def test_natural_request_keeps_the_named_project(self):
        facts = tender_facts.extract("请生成合成幕墙项目B的技术标目录和响应草稿。招标工期180日历天。")
        self.assertEqual(facts.first("project").value, "合成幕墙项目B")

    def test_explicit_inbound_ledger_binds_quantity_and_location(self):
        rows = expert_turn._parse_wh_rows(
            "请生成材料入库台账。项目：合成幕墙项目D；入库日期：2026-10-09；"
            "材料名称：铝板；规格：3mm；数量：120张；供应商：合成供应商甲；"
            "批次：SYN-B001；仓库：一号仓。未知单价和验收人保持待填。"
        )
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["inbound"], "120张")
        self.assertEqual(rows[0]["location"], "一号仓")
        self.assertNotIn("balance", rows[0])

    def test_minutes_are_not_replaced_with_a_logistics_checklist(self):
        from packing_assistant.post_drafts.admin import build_draft
        from packing_assistant.runtime.task_router import route_task

        text = ("请生成会议纪要草稿。会议名称：合成幕墙协调会；日期：2026-10-09；"
                "地点：东楼301；与会人员：张测试、李测试；议题：幕墙接口核对；"
                "决定：李测试在2026-10-12前提供节点图；未提供的信息保持待填。")
        self.assertEqual(route_task(text)["expert_ids"], ["admin-doc"])
        md = build_draft("admin-doc", "admin-doc__draft", text)
        self.assertIn("| 时间 | 2026-10-09 |", md)
        self.assertIn("| 李测试在2026-10-12前提供节点图 |", md)
        self.assertNotIn("节点图；未提供", md)

    def test_draft_excel_is_readable_and_keeps_formula_like_text_literal(self):
        import openpyxl
        from packing_assistant.office_job import write_xlsx

        with tempfile.TemporaryDirectory(prefix="civil-finale-xlsx-") as directory:
            with patch.dict(os.environ, {"CIVIL_SANDBOX_ROOTS": directory}):
                path = write_xlsx(Path(directory) / "draft.xlsx", [("核对", [
                    ["事项", "响应原文或证据", "缺口"],
                    ["工期", "170日历天（我们投标函草稿工期写成了170日历天）",
                     "响应170日历天超过招标150日历天，待人工核验"],
                    ["来源文本", "=HYPERLINK(\"https://example.invalid\")", "待核验"],
                ])])
                book = openpyxl.load_workbook(path)
                try:
                    sheet = book.active
                    self.assertTrue(sheet["B2"].alignment.wrap_text)
                    self.assertGreaterEqual(sheet.column_dimensions["B"].width, 28)
                    self.assertGreaterEqual(sheet.row_dimensions[2].height, 30)
                    self.assertEqual(sheet.freeze_panes, "A2")
                    self.assertEqual(sheet.print_title_rows, "$1:$1")
                    self.assertEqual(sheet["B3"].data_type, "s")
                finally:
                    book.close()


if __name__ == "__main__":
    unittest.main()
