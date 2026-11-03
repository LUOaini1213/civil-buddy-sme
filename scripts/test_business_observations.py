#!/usr/bin/env python3
"""Independent commercial/finance record ownership and non-approval tests."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from packing_assistant.post_drafts.business_observations import (
    TOOLS, UNKNOWN, build_draft, parse_records, tax_records,
)
from packing_assistant.office_job import tables_from_md


class BusinessOwnership(unittest.TestCase):
    def rows(self, text, post="cost"):
        return parse_records(text, post)

    def tables(self, text, post="cost"):
        return dict(tables_from_md(build_draft(post, TOOLS[post], text)))

    def test_templates_and_exact_tool_pair(self):
        for post, tool in TOOLS.items():
            md = build_draft(post, tool, "")
            self.assertIn("AI 草稿", md)
            self.assertIn("submit_blocked=true", md)
            self.assertNotIn("## 用户原文", md)
            self.assertIn("（空栏）", md)
            for wrong in set(TOOLS.values())-{tool}:
                self.assertIsNone(build_draft(post, wrong, ""))

    def test_registry_and_tax_compatibility_wrapper_use_same_renderer(self):
        from packing_assistant.post_drafts import build_draft as registered, dedicated_posts
        from packing_assistant.tax_context import draft_tax, records
        for post, tool in TOOLS.items():
            self.assertIn(post, dedicated_posts())
            self.assertEqual(build_draft(post, tool, ""), registered(post, tool, ""))
        text = "主体：甲；税种：测试税；税号：ID01；申报节点：10月16日"
        self.assertEqual(tax_records(text), records(text))
        self.assertIn("ID01", draft_tax(text, "测试免责声明"))

    def test_late_identifier_enriches_each_primary_identity(self):
        for post, text, key in (
            ("cost", "清单项：砼；数量：0；编码：C01", "code"),
            ("variation", "事项：旧墙拆除；数量：0；编号：V01", "id"),
            ("finance-book", "业务：采购；金额：0；发票号：I01", "invoice"),
            ("finance-tax", "主体：主体甲；税种：测试税；税额：0", "tax"),
        ):
            rows = self.rows(text, post)
            self.assertEqual(1, len(rows), (post, rows))
            self.assertIn(key, rows[0])

    def test_same_named_file_is_a_new_epoch(self):
        rows = self.rows("### 同名.csv\n项目：甲；依据：甲文件；清单项：砼；数量：71\n### 同名.csv\n清单项：钢筋")
        self.assertEqual(2, len(rows))
        for key in ("project", "quantity", "basis"):
            self.assertNotIn(key, rows[1])
        self.assertNotEqual(rows[0]["_file"], rows[1]["_file"])

    def test_project_and_stage_changes_freeze_older_records(self):
        rows = self.rows("项目：甲；资料阶段：初稿；清单项：砼；数量：71\n资料阶段：复核；清单项：钢筋\n项目：乙；清单项：墙")
        self.assertEqual(["甲", "甲", "乙"], [r["project"] for r in rows])
        self.assertEqual(["初稿", "复核"], [r["stage"] for r in rows[:2]])
        self.assertNotIn("stage", rows[2])
        self.assertNotIn("quantity", rows[1])

    def test_reordered_matrices_metadata_and_blank_short_rows(self):
        for post, header in (("cost", "数量|项目|清单项"), ("variation", "数量|项目|事项"),
                             ("finance-book", "金额|项目|业务"), ("finance-tax", "税额|项目|主体")):
            rows = self.rows("|"+header+"|\n|---|---|---|\n|71|甲|对象甲|\n|92|乙||\n|||\n|", post)
            self.assertEqual(4, len(rows), (post, rows))
            self.assertEqual(["甲", "乙"], [r["project"] for r in rows[:2]])
            identity = {"cost": "item", "variation": "event", "finance-book": "business", "finance-tax": "entity"}[post]
            self.assertEqual(UNKNOWN, rows[1][identity])
            self.assertEqual(UNKNOWN, rows[3][identity])

    def test_two_tables_can_change_column_order(self):
        rows = self.rows("|清单项|数量|\n|---|---|\n|砼|71|\n|数量|清单项|\n|---|---|\n|92|钢筋|")
        self.assertEqual(2, len(rows))
        self.assertEqual(["砼", "钢筋"], [r["item"] for r in rows])
        self.assertEqual(["71", "92"], [r["quantity"] for r in rows])

    def test_blank_leading_label_cell_is_not_shifted(self):
        for post in TOOLS:
            md = build_draft(post, TOOLS[post], "||数量|719|")
            self.assertNotIn("719", md)

    def test_explicit_global_and_local_zones_and_bases(self):
        rows = self.rows("项目：甲；总体辖区：DUAL；依据：总体口径；CN依据：甲文件；SG依据：乙文件\n清单项：砼；辖区：CN\n清单项：钢筋；辖区：SG")
        self.assertEqual(["CN", "SG"], [r["zone"] for r in rows])
        self.assertTrue(all("basis" not in r for r in rows))
        self.assertTrue(all(r["CN_basis"] == "甲文件" and r["SG_basis"] == "乙文件" for r in rows))

    def test_bare_zone_is_local_and_names_cannot_set_country(self):
        rows = self.rows("清单项：砼\nSG\n清单项：钢筋")
        self.assertEqual("SG", rows[0]["zone"])
        self.assertNotIn("zone", rows[1])
        for post, text in (("cost", "项目：新加坡楼；清单项：中国水泥"),
                           ("finance-tax", "### SG税表.pdf\n主体：中国建筑；税种：GST；税率：7.5%")):
            row = self.rows(text, post)[0]
            self.assertNotIn("zone", row)
            self.assertEqual(UNKNOWN, row["_project_zone"])

    def test_scenarios_excluded_and_named_example_entity_allowed(self):
        rows = self.rows("例如：清单项：假砼；数量：999\n真实记录：清单项：示例砼公司供料；数量：71")
        self.assertEqual(1, len(rows))
        self.assertEqual("71", rows[0]["quantity"])
        self.assertNotIn("999", build_draft("cost", TOOLS["cost"], "假设：清单项：假砼；数量：999"))

    def test_encoded_pipe_and_entities_are_decoded_once_after_split(self):
        row = self.rows("|清单项|数量|来源|\n|---|---|---|\n|甲&#124;乙&lt;丙|=1+2|字面&amp;lt;|")[0]
        self.assertEqual("甲|乙<丙", row["item"])
        self.assertEqual("=1+2", row["quantity"])
        self.assertEqual("字面&lt;", row["source"])

    def test_quantity_units_literal_not_converted_and_no_inferred_unit(self):
        rows = self.rows("清单项：砼；数量：0 m3\n清单项：钢筋；数量：1,286.4 t\n清单项：墙；数量：71")
        self.assertEqual(["0 m3", "1,286.4 t", "71"], [r["quantity"] for r in rows])
        self.assertEqual(["m3", "t"], [r["unit"] for r in rows[:2]])
        self.assertNotIn("unit", rows[2])
        self.assertNotIn("1286400", build_draft("cost", TOOLS["cost"], "清单项：钢筋；数量：1,286.4 t"))

    def test_quantity_and_identifier_unknown_updates_clear_value_and_source(self):
        row = self.rows("清单项：砼；编码：C01；数量：71 m3；数量：没定；编码：我还没查到")[0]
        for key in ("quantity", "unit", "code"):
            self.assertEqual(UNKNOWN, row[key])
            self.assertNotIn(key, row["_sources"])
        self.assertIn("71", row["pending"])
        self.assertEqual(1, len(self.rows("清单项：砼；编码：C01；编码：未查")))

    def test_estimates_future_sources_and_uncertainty_not_confirmed(self):
        row = self.rows("清单项：砼；数量：大概71方；用户报价：市场价500元；来源：计划提供清单.pdf")[0]
        self.assertTrue(all(row[k] == UNKNOWN for k in ("quantity", "quoted_price", "source")))

    def test_cost_quote_separate_from_main_price_and_user_quantity_comparison(self):
        tables = self.tables("清单项：砼；数量：71 m3；模型量：73；图示量：71；现场实测量：70；报价：481元；报价来源：Q01.pdf")
        main = tables["3 工程量拆分"]
        self.assertEqual(["TBD", UNKNOWN], main[1][-2:])
        self.assertNotIn("481元", main[1])
        quotes = tables["6 组价与询价资料"]
        self.assertEqual("481元", quotes[1][quotes[0].index("用户报价摘录")])
        compare = tables["5 用户数量对照（未计算）"]
        self.assertEqual(["71 m3", "73", "71", "70"], compare[1][-4:])

    def test_cost_two_natural_items_have_own_grade_quantity_and_source(self):
        rows = self.rows("两项：一个是A7.5加气块墙250厚，建施-A9量出来738.1方；另一个构造柱C30砼，结施-B9算的是48.3方。")
        self.assertEqual(2, len(rows))
        self.assertIn("A7.5", rows[0]["item"])
        self.assertEqual(["738.1方", "48.3方"], [r["quantity"] for r in rows])
        self.assertEqual(["建施-A9", "结施-B9"], [r["quantity_source"] for r in rows])

    def test_variation_missing_structure_does_not_erase_literal_date(self):
        row = self.rows("编号先用QZ-019，2026-11-02那天5#楼基坑东侧碰到一段图纸上没有的旧墙。", "variation")[0]
        self.assertEqual("2026-11-02", row["date"])
        self.assertEqual("QZ-019", row["id"])

    def test_variation_instruction_date_is_not_event_occurrence_date(self):
        row = self.rows("事项：加固；设计变更通知2026-11-02下发", "variation")[0]
        self.assertEqual("2026-11-02", row["instruction_date"])
        self.assertNotIn("date", row)

    def test_variation_quantity_children_have_independent_materials_and_sources(self):
        row = self.rows("事项：加固；编号：V01；数量类型：砼；数量：14.2 m3；材料：混凝土；规格：C35；数量来源：Q1.pdf；数量类型：钢筋；数量：1.8 t；规格：HRB500", "variation")[0]
        first, second = row["_quantities"]
        self.assertEqual("Q1.pdf", first["quantity_source"])
        self.assertEqual("混凝土", first["material"])
        self.assertNotIn("quantity_source", second)
        self.assertNotIn("material", second)
        self.assertEqual(["m3", "t"], [r["unit"] for r in (first, second)])
        table = self.tables("事项：加固；数量：14.2m3；数量来源：Q1.pdf；数量：1.8t", "variation")["4 工程量"]
        self.assertEqual(["Q1.pdf", UNKNOWN], [r[table[0].index("计量来源")] for r in table[1:]])

    def test_variation_quantity_source_before_quantity_is_first_child_only(self):
        row = self.rows("事项：加固；数量来源：Q1.pdf；数量：14.2 m3；数量：1.8 t", "variation")[0]
        self.assertEqual(2, len(row["_quantities"]))
        self.assertEqual("Q1.pdf", row["_quantities"][0]["quantity_source"])
        self.assertNotIn("quantity_source", row["_quantities"][1])

    def test_variation_unknown_quantity_withdraws_child_not_adds_one(self):
        row = self.rows("事项：加固；数量：14.2 m3；数量：未定", "variation")[0]
        self.assertEqual(1, len(row["_quantities"]))
        detail = row["_quantities"][0]
        self.assertEqual(UNKNOWN, detail["quantity"])
        self.assertEqual(UNKNOWN, detail["unit"])
        self.assertNotIn("quantity", detail["_sources"])

    def test_variation_new_event_does_not_borrow_measurement_instruction_or_machine(self):
        rows = self.rows("事项：旧墙；编号：V01；指令：口头要求；测量员：李甲；数量：71方；机械：PC220；台班：2台班\n事项：停电；编号：V02；工日：19工日", "variation")
        self.assertEqual(2, len(rows))
        self.assertEqual([], rows[1]["_quantities"])
        self.assertTrue(all(key not in rows[1] for key in ("issuer", "instruction", "measurer", "machine", "machine_days")))

    def test_variation_application_user_approval_and_confirmation_independent(self):
        tables = self.tables("事项：旧墙；金额：5100元；实施记录：已实施；申请记录：申请扫描件；批准记录：用户称批准；附件：照片目录", "variation")
        states = tables["用户金额与状态（未确认）"]
        self.assertEqual(["5100元", "申请扫描件", "用户称批准", "TBD", UNKNOWN], states[1][-5:])
        sign = tables["7 签认栏（审批空栏）"]
        self.assertEqual(["（空栏）"]*4, sign[1][1:])

    def test_finance_two_natural_items_keep_invoice_money_and_handler_local(self):
        rows = self.rows("一笔是办公用品，电子普票票号09876543，一共4160块，经办是内业周甲；另一笔王师傅垫钱买的安全帽，他说走安措费，发票弄丢了只剩收款收据，钱是3140。", "finance-book")
        self.assertEqual(2, len(rows))
        self.assertEqual("09876543", rows[0]["invoice"])
        self.assertNotIn("invoice", rows[1])
        self.assertNotIn("handler", rows[1])
        self.assertEqual("安措费", rows[1]["claimed_special"])
        self.assertEqual(["4160块", "3140"], [r["amount"] for r in rows])

    def test_finance_unknown_invoice_clears_previous_and_does_not_duplicate(self):
        rows = self.rows("业务：办公用品；发票号：09876543；发票号：未提供", "finance-book")
        self.assertEqual(1, len(rows))
        self.assertEqual(UNKNOWN, rows[0]["invoice"])
        self.assertNotIn("invoice", rows[0]["_sources"])

    def test_finance_department_and_pending_manager_are_separate_literal_claims(self):
        row = self.rows("业务：租赁；审批：经办徐亮，部门负责人已签，项目经理待签", "finance-book")[0]
        self.assertEqual("徐亮", row["handler"])
        self.assertIn("部门负责人已签", row["approval"])
        self.assertIn("项目经理待签", row["approval"])
        self.assertNotIn("approval_source", row)

    def test_finance_amount_never_posted_or_approved_automatically(self):
        tables = self.tables("业务：租赁；金额：131500元；科目：机械使用费；报销记录：用户称报销；付款记录：用户称付款", "finance-book")
        account = tables["用户科目与账套口径（未记账）"]
        self.assertEqual([UNKNOWN, UNKNOWN], account[1][-2:])
        states = tables["5 用户审批与付款记录"]
        self.assertEqual(UNKNOWN, states[1][-1])
        self.assertNotIn("131500元", states[1])

    def test_tax_multiple_rows_no_country_rate_period_owner_or_deadline_borrowing(self):
        rows = tax_records("主体：甲；辖区：SG；税种：GST；税率：7.5%；申报期：2026-Q3；申报节点：2026-11-03；责任人：王甲\n主体：乙；税种：测试税")
        self.assertEqual(2, len(rows))
        self.assertEqual("7.5%", rows[0]["rate"])
        self.assertEqual(UNKNOWN, rows[1]["rate"])
        self.assertEqual(UNKNOWN, rows[1]["zone"])
        self.assertTrue(all(k not in rows[1] for k in ("period", "deadline", "owner")))

    def test_tax_region_prefix_of_next_record_is_not_previous_tax(self):
        rows = tax_records("辖区：SG；税种：GST；税率：7.5%\n辖区：CN；税种：测试税；税率：3.5%")
        self.assertEqual(2, len(rows))
        self.assertEqual(["SG", "CN"], [r["zone"] for r in rows])
        self.assertEqual(["7.5%", "3.5%"], [r["rate"] for r in rows])

    def test_tax_dual_rate_and_conflicts_visible_but_not_applied(self):
        for source in ("辖区：DUAL；税种：GST；税率：7.5%", "辖区：SG；税种：GST；税率：7.5%；税率：3.5%"):
            row = tax_records(source)[0]
            self.assertEqual(UNKNOWN, row["rate"])
            self.assertIn("7.5%", row["rate_input"])

    def test_tax_reference_to_another_period_not_new_tax_or_guessed_period(self):
        rows = tax_records("另一个是新注册劳务公司，税种：城市维护建设税；申报期他们说跟着增值税走还没确认。")
        self.assertEqual(1, len(rows))
        self.assertEqual("城市维护建设税", rows[0]["tax"])
        self.assertNotIn("period", rows[0])
        self.assertIn("增值税", rows[0]["pending"])

    def test_tax_node_literal_and_amount_not_calculated_or_filed(self):
        tables = self.tables("主体：甲；辖区：CN；税种：测试税；申报期：2026-Q3；申报节点：10月16日；税率：3.5%；税额：1230元；申报记录：回执扫描件", "finance-tax")
        calendar = tables["1 辖区、主体与申报日历"]
        self.assertEqual(["2026-Q3", "10月16日", "3.5%", "1230元"], calendar[1][-4:])
        states = tables["用户申报与缴税材料（未核验）"]
        self.assertEqual(["回执扫描件", UNKNOWN, UNKNOWN, "（空栏）"], states[1][-4:])

    def test_tax_missing_update_withdraws_rate_and_source(self):
        row = tax_records("主体：甲；辖区：SG；税种：GST；税率：7.5%；税率：未核；来源：税表.pdf；来源：未提供")[0]
        self.assertEqual(UNKNOWN, row["rate"])
        self.assertEqual(UNKNOWN, row["source"])
        self.assertNotIn("source", row["_sources"])

    def test_all_objects_have_provenance_not_fabricated_original_page(self):
        row = self.rows("### 合成清单.csv\n清单项：砼；数量：71")[0]
        self.assertIn("合成清单.csv", row["_sources"]["quantity"])
        self.assertIn("非原件页码", row["_sources"]["quantity"])
        self.assertNotIn("第71页", row["_sources"]["quantity"])


if __name__ == "__main__":
    unittest.main()
