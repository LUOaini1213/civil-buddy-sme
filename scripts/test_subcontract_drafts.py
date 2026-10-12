#!/usr/bin/env python3
"""Subcontract party/period ownership and real unsigned settlement exports."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from packing_assistant import expert_turn
from packing_assistant.office_job import tables_from_md
from packing_assistant.post_drafts import build_draft as dedicated
from packing_assistant.post_drafts.subcontract import MISSING, TOOL, _owned, build_draft, parse
from packing_assistant.runtime.scheduler import Scheduler


def draft(text):
    return build_draft("subcontract", TOOL, text)


def table(md, prefix):
    return next(rows for title, rows in tables_from_md(md) if title.startswith(prefix))


class Subcontract(unittest.TestCase):
    def test_exclusive_eleven_sections_and_no_country_rate_law_or_payment_default(self):
        md = draft("")
        self.assertEqual(md, dedicated("subcontract", TOOL, ""))
        self.assertIsNone(build_draft("interim", TOOL, ""))
        self.assertIsNone(build_draft("subcontract", "interim__measure", ""))
        self.assertEqual(11, len([s for s in md.splitlines() if s.startswith("## ")]))
        for text in ("AI 草稿", "submit_blocked=true", "TBD", "未会签", "（空）", "finance-fund", "finance-tax"):
            self.assertIn(text, md)
        for text in ("## 用户原文", "SG：", "CN：", "GB 50500", "PSSCOC", "保障农民工工资支付条例", "3%", "同意支付", "结算无误"):
            self.assertNotIn(text, md)

    def test_private_kb_does_not_instruct_a_default_singapore_jurisdiction(self):
        text = (ROOT / "demo/kb/commercial/subcontract/web-knowledge.md").read_text(encoding="utf-8")
        self.assertNotIn("默认新加坡工地（SG）", text)
        self.assertIn("未提供则 UNSPECIFIED", text)

    def test_every_development_fact_reaches_its_semantic_field(self):
        from scripts.eval_post_content import classify, load, spots, split_echo
        for case in load("cases"):
            if case["post"] != "subcontract":
                continue
            with self.subTest(case=case["id"]):
                md = draft(case["request"])
                body, echo = split_echo(md)
                for fact in case["facts"]:
                    self.assertEqual("placed", classify(fact, body, echo, spots(body)), fact)
                self.assertTrue(all(t not in md for t in case.get("forbidden", [])))

    def test_declared_completed_foreman_confirmed_and_upstream_quantities_are_not_interchangeable(self):
        data = parse("分包单位：甲班\n分项：钢筋\n申请量：35吨\n完成量：32吨\n工长核量：30吨\n确认量：29吨\n总包确认量：27吨\n合同单价：920元/吨")
        row = data.rows[0]
        self.assertEqual(("35吨", "32吨", "30吨", "29吨", "27吨"), tuple(row[k] for k in ("declared", "completed", "foreman_qty", "confirmed", "upstream_qty")))
        self.assertNotIn("user_payable", row)
        self.assertEqual(["TBD · 未计算", "TBD · 未核定"], table(draft("确认量：29吨\n合同单价：920元/吨"), "4 合同")[1][-2:])

    def test_foreman_named_record_keeps_task_rate_and_quantity_without_approving_payment(self):
        data = parse("帮我出2026-08期的劳务结算表头，宏达劳务干的5#楼主体钢筋绑扎，合同编号HDLW-25-017，任务单RW-0823，工长周建国核完量是186.4吨，合同单价920元一吨，本期要扣水电费3150元，之前累计已经付了118000元")
        self.assertEqual(1, len(data.rows))
        row = data.rows[0]
        self.assertEqual(("2026-08", "宏达劳务", "HDLW-25-017", "RW-0823", "周建国", "186.4吨", "920元一吨"), tuple(row[k] for k in ("period", "company", "contract", "task", "foreman", "foreman_qty", "price")))
        self.assertNotIn("confirmed", row)
        self.assertNotIn("upstream_qty", row)
        self.assertNotIn("user_payable", row)
        self.assertEqual("3150元", data.deductions[0]["deduction_amount"])
        self.assertEqual("118000元", data.payments[0]["cumulative_paid"])

    def test_task_or_upstream_signature_record_never_creates_missing_quantity(self):
        data = parse("分包单位：甲班\n任务单：RW-A\n对上确认记录：用户称总包已签字\n会签记录：用户称项目经理已签字")
        row = data.rows[0]
        self.assertTrue(all(k not in row for k in ("confirmed", "foreman_qty", "upstream_qty", "price", "user_payable")))
        rows = table(draft("会签记录：用户称项目经理已签字"), "9 会签")
        self.assertEqual(["未会签", "（空）"], rows[1][-2:])

    def test_two_teams_keep_quantity_rate_damage_and_living_advance_apart(self):
        data = parse("老李的木工班和鑫源架子队这个月一块结，木工干的是3#楼标准层模板，工长核完是4120平，单价46.5。架子队搭的7#楼外架，量还没核出来，只知道合同价19.5一平，他们上回摔坏扣件要扣1800，另外木工班预支过生活费30000，也得列上，合同号我回头再找")
        self.assertEqual(2, len(data.rows))
        wood, scaffold = data.rows
        self.assertEqual(("老李的木工班", "4120平", "46.5"), tuple(wood[k] for k in ("company", "foreman_qty", "price")))
        self.assertEqual(("鑫源架子队", "19.5一平"), tuple(scaffold[k] for k in ("company", "price")))
        self.assertNotIn("foreman_qty", scaffold)
        self.assertEqual("鑫源架子队", _owned(data, data.deductions)[0]["company"])
        self.assertEqual("老李的木工班", _owned(data, data.payments)[0]["company"])
        self.assertEqual("30000", data.payments[0]["living_paid"])
        self.assertNotIn("wage_paid", data.payments[0])

    def test_ambiguous_team_reference_does_not_choose_the_last_party(self):
        data = parse("分包单位：甲木工班\n工长核量：120平\n分包单位：乙木工班\n工长核量：80平\n另外木工班预支过生活费30000")
        payment = _owned(data, data.payments)[0]
        self.assertNotIn("company", payment)
        self.assertNotIn("foreman_qty", payment)

    def test_multiple_items_under_one_referenced_team_make_a_party_level_payment(self):
        data = parse("分包单位：甲木工班\n合同编号：A\n分项：顶板模板\n确认量：120平\n分项：墙模板\n确认量：80平\n另外木工班预支过生活费30000")
        payment = _owned(data, data.payments)[0]
        self.assertEqual("甲木工班", payment["company"])
        self.assertEqual("A", payment["contract"])
        self.assertNotIn("name", payment)
        self.assertNotIn("confirmed", payment)

    def test_new_party_drops_contract_rate_quantities_sources_and_children(self):
        data = parse("分包单位：甲班\n合同编号：A\n分项：钢筋\n确认量：30吨\n确认量来源：Q-A.pdf\n合同单价：920元/吨\n单价来源：R-A.pdf\n质量罚款：2400元\n通知单：N-A\n累计已付：10000元\n支付凭证：P-A.pdf\n分包单位：乙班\n分项：防水\n确认量：200平\n质量罚款：1200元\n累计已付：8000元")
        row = data.rows[1]
        self.assertTrue(all(k not in row for k in ("contract", "price", "price_source", "confirmed_source")))
        self.assertNotIn("notice", data.deductions[1])
        self.assertNotIn("payment_source", data.payments[1])

    def test_same_named_party_redeclared_is_a_new_record_not_source_permission(self):
        data = parse("分包单位：甲班\n确认量：30吨\n确认量来源：Q-A.pdf\n分包单位：甲班\n确认量：20吨")
        self.assertEqual(2, len(data.rows))
        self.assertNotIn("confirmed_source", data.rows[1])

    def test_repeated_quantity_has_own_row_and_never_reuses_old_quantity_source_or_rate(self):
        data = parse("分包单位：甲班\n合同编号：A\n分项：钢筋\n清单编号：X\n确认量：35吨\n确认量来源：Q-A.pdf\n单价：920元/吨\n确认量：20吨")
        self.assertEqual(2, len(data.rows))
        self.assertEqual(["35吨", "20吨"], [r["confirmed"] for r in data.rows])
        self.assertEqual(("甲班", "A", "钢筋", "X"), tuple(data.rows[1][k] for k in ("company", "contract", "name", "code")))
        self.assertNotIn("confirmed_source", data.rows[1])
        self.assertNotIn("price", data.rows[1])

    def test_repeated_rate_does_not_apply_new_rate_to_prior_confirmed_quantity_or_source(self):
        data = parse("分包单位：甲班\n确认量：35吨\n合同单价：920元/吨\n单价来源：R-A.pdf\n合同单价：800元/吨")
        self.assertEqual(2, len(data.rows))
        self.assertEqual(["920元/吨", "800元/吨"], [r["price"] for r in data.rows])
        self.assertNotIn("confirmed", data.rows[1])
        self.assertNotIn("price_source", data.rows[1])

    def test_new_period_after_table_keeps_global_project_not_last_party_or_dates(self):
        data = parse("项目：甲\n辖区：SG\n结算期间：2026-07-26至2026-08-25\n| 分包单位 | 确认量 |\n| --- | --- |\n| 甲班 | 35吨 |\n\n结算期间：这个月\n确认量：20吨")
        self.assertEqual(("甲", "SG", "这个月"), tuple(data.rows[1][k] for k in ("project", "jurisdiction", "period")))
        self.assertNotIn("company", data.rows[1])
        self.assertNotIn("begin", data.rows[1])
        self.assertNotIn("end", data.rows[1])

    def test_scalar_or_empty_period_table_cell_never_inherits_header_date_range(self):
        data = parse("结算期间：2026-07-26至2026-08-25\n| 分包单位 | 期间 | 确认量 |\n| --- | --- | --- |\n| 甲班 | 2026-09 | 30吨 |\n| 乙班 | | 20吨 |")
        self.assertEqual(["2026-09", MISSING], [r["period"] for r in data.rows])
        self.assertTrue(all("begin" not in r and "end" not in r for r in data.rows))

    def test_explicit_pending_payment_preserves_user_amount_but_not_actual_paid(self):
        data = parse("累计已付：预计明天支付8000元\n支付凭证：P-A.pdf\n工资代发已付：据说4000元")
        self.assertEqual(["8000元", "4000元"], [r["planned_payment"] for r in data.payments])
        self.assertTrue(all("cumulative_paid" not in r and "wage_paid" not in r for r in data.payments))
        self.assertNotIn("payment_source", data.payments[1])

    def test_changed_item_retains_party_contract_only_not_price_quantity_or_payment(self):
        data = parse("分包单位：甲班\n合同编号：A\n分项：钢筋\n确认量：30吨\n合同单价：920元/吨\n累计已付：10000元\n分项：模板\n确认量：120平")
        self.assertEqual(("甲班", "A", "模板", "120平"), tuple(data.rows[1][k] for k in ("company", "contract", "name", "confirmed")))
        self.assertNotIn("price", data.rows[1])
        self.assertEqual(1, len(data.payments))

    def test_contract_change_keeps_stated_party_not_previous_rate_or_sources(self):
        data = parse("分包单位：甲班\n合同编号：A\n确认量：30吨\n确认量来源：Q-A.pdf\n合同单价：920元/吨\n合同编号：B\n确认量：20吨")
        self.assertEqual("甲班", data.rows[1]["company"])
        self.assertEqual("B", data.rows[1]["contract"])
        self.assertNotIn("price", data.rows[1])
        self.assertNotIn("confirmed_source", data.rows[1])

    def test_period_change_drops_old_start_end_price_and_payment_sources(self):
        data = parse("项目：甲\n辖区：SG\n结算期间：2026-07-26至2026-08-25\n分包单位：甲班\n合同编号：A\n确认量：30吨\n合同单价：920元/吨\n累计已付：10000元\n支付凭证：P-A.pdf\n结算期间：这个月\n确认量：20吨\n累计已付：8000元")
        self.assertEqual(("甲班", "A", "这个月", "SG", "甲"), tuple(data.rows[1][k] for k in ("company", "contract", "period", "jurisdiction", "project")))
        self.assertTrue(all(k not in data.rows[1] for k in ("begin", "end", "price")))
        self.assertNotIn("payment_source", data.payments[1])

    def test_new_project_or_jurisdiction_has_no_party_contract_or_children(self):
        for key, first, second in (("项目", "甲", "乙"), ("辖区", "SG", "CN")):
            with self.subTest(key=key):
                data = parse(f"{key}：{first}\n分包单位：甲班\n合同编号：A\n确认量：30吨\n合同单价：920元/吨\n水电费：100元\n通知单：N-A\n{key}：{second}\n确认量：20吨\n水电费：200元")
                self.assertNotIn("company", data.rows[1])
                self.assertNotIn("contract", data.rows[1])
                self.assertNotIn("notice", data.deductions[1])

    def test_repeated_file_name_is_an_independent_epoch(self):
        for other in ("b.md", "a.md"):
            with self.subTest(other=other):
                data = parse(f"### a.md\n分包单位：甲班\n合同编号：A\n确认量：30吨\n确认量来源：Q-A.pdf\n水电费：100元\n通知单：N-A\n### {other}\n确认量：20吨\n水电费：200元")
                self.assertNotIn("company", data.rows[1])
                self.assertNotIn("confirmed_source", data.rows[1])
                self.assertNotIn("notice", data.deductions[1])

    def test_blank_table_identity_is_not_previous_party_and_tail_is_unassigned(self):
        data = parse("| 分包单位 | 合同编号 | 确认量 | 单价 | 累计已付 | 支付凭证 |\n| --- | --- | --- | --- | --- | --- |\n| 甲班 | A | 30吨 | 920元/吨 | 10000元 | P-A.pdf |\n| | | 20吨 | | 8000元 | |\n| 乙班 | B | 120平 | 38元/平 | | |\n\n确认量：10平")
        self.assertEqual([r.get("company") for r in data.rows], ["甲班", MISSING, "乙班", None])
        self.assertEqual(MISSING, data.rows[1]["price"])
        self.assertEqual(MISSING, data.payments[1]["payment_source"])
        self.assertNotIn("contract", data.rows[-1])

    def test_reordered_table_fields_own_all_children_before_column_order(self):
        data = parse("| 通知单 | 水电费 | 支付凭证 | 累计已付 | 确认量 | 合同编号 | 分包单位 |\n| --- | --- | --- | --- | --- | --- | --- |\n| N-A | 100元 | P-A.pdf | 10000元 | 30吨 | A | 甲班 |\n| | 200元 | | 8000元 | 20吨 | B | 乙班 |")
        self.assertEqual(["甲班", "乙班"], [r["company"] for r in _owned(data, data.deductions)])
        self.assertEqual(["A", "B"], [r["contract"] for r in _owned(data, data.payments)])
        self.assertEqual(MISSING, data.deductions[1]["notice"])

    def test_unique_and_duplicate_code_reference_never_choose_last_matching_item(self):
        source = "| 清单编号 | 分包单位 | 合同编号 | 分项 | 确认量 |\n| --- | --- | --- | --- | --- |\n| X | 甲班 | A | 钢筋 | 30吨 |\n| Y | 乙班 | B | 模板 | 120平 |\n\n清单编号：X\n单价：920元/吨"
        data = parse(source)
        self.assertEqual("920元/吨", data.rows[0]["price"])
        self.assertNotIn("price", data.rows[1])
        data = parse(source.replace("| Y |", "| X |"))
        self.assertNotIn("company", data.rows[-1])
        self.assertEqual("920元/吨", data.rows[-1]["price"])

    def test_new_deduction_kind_or_repeat_amount_drops_previous_notice_and_source(self):
        data = parse("水电费：100元\n扣款依据：D-A.pdf\n通知单：N-A\n扣款金额：200元\n质量罚款：2400元")
        self.assertEqual(3, len(data.deductions))
        self.assertEqual(["水电费", "水电费", "质量罚款"], [r["deduction_kind"] for r in data.deductions])
        self.assertTrue(all("deduction_source" not in r and "notice" not in r for r in data.deductions[1:]))

    def test_two_deduction_columns_keep_amounts_separate_and_generic_notice_unassigned(self):
        data = parse("| 分包单位 | 水电费 | 质量罚款 | 通知单 | 扣款依据 |\n| --- | --- | --- | --- | --- |\n| 甲班 | 3150元 | 2400元 | N-A | D-A.pdf |")
        self.assertEqual(3, len(data.deductions))
        orphan, utility, penalty = data.deductions
        self.assertNotIn("deduction_kind", orphan)
        self.assertNotIn("deduction_amount", orphan)
        self.assertEqual(("水电费", "3150元"), (utility["deduction_kind"], utility["deduction_amount"]))
        self.assertEqual(("质量罚款", "2400元"), (penalty["deduction_kind"], penalty["deduction_amount"]))
        self.assertTrue(all("notice" not in r and "deduction_source" not in r for r in data.deductions[1:]))

    def test_qualified_quality_notice_only_attaches_to_unique_named_penalty(self):
        data = parse("| 分包单位 | 水电费 | 质量罚款 | 质量罚款通知单 |\n| --- | --- | --- | --- |\n| 甲班 | 3150元 | 2400元 | N-Q |")
        self.assertEqual(2, len(data.deductions))
        self.assertNotIn("notice", data.deductions[0])
        self.assertEqual("N-Q", data.deductions[1]["notice"])
        data = parse("质量罚款：2400元\n质量罚款：1200元\n质量罚款通知单：N-Q")
        self.assertTrue(all("notice" not in r for r in data.deductions[:2]))
        self.assertNotIn("deduction_amount", data.deductions[-1])

    def test_qualified_notice_before_amount_is_not_lost_or_borrowed_by_water_charge(self):
        data = parse("质量罚款通知单：N-Q\n质量罚款：2400元\n水电费：3150元")
        self.assertEqual(2, len(data.deductions))
        self.assertEqual(("2400元", "N-Q"), (data.deductions[0]["deduction_amount"], data.deductions[0]["notice"]))
        self.assertNotIn("notice", data.deductions[1])

    def test_payment_roles_in_same_table_row_do_not_share_one_unqualified_voucher(self):
        data = parse("| 分包单位 | 本期已付 | 累计已付 | 支付凭证 |\n| --- | --- | --- | --- |\n| 甲班 | 2000元 | 10000元 | P-A.pdf |")
        self.assertEqual(3, len(data.payments))
        self.assertNotIn("paid", data.payments[0])
        self.assertNotIn("cumulative_paid", data.payments[0])
        self.assertEqual("P-A.pdf", data.payments[0]["payment_source"])
        self.assertEqual("2000元", data.payments[1]["paid"])
        self.assertEqual("10000元", data.payments[2]["cumulative_paid"])
        self.assertTrue(all("payment_source" not in r for r in data.payments[1:]))

    def test_blank_other_payment_role_in_table_does_not_hide_the_only_payment_source(self):
        for header, row in (("支付凭证 | 本期已付 | 累计已付", "P-A.pdf | 2000元 |"), ("支付凭证 | 累计已付 | 本期已付", "P-A.pdf | | 2000元")):
            data = parse(f"| {header} |\n| --- | --- | --- |\n| {row} |")
            self.assertEqual(1, len(data.payments))
            self.assertEqual("P-A.pdf", data.payments[0]["payment_source"])
            self.assertEqual("2000元", data.payments[0]["paid"])

    def test_team_reference_uses_explicit_period_not_an_earlier_period_of_same_team(self):
        data = parse("分包单位：甲木工班\n结算期：2026-08\n确认量：120平\n结算期：2026-09\n确认量：80平\n另外木工班预支过生活费30000")
        payment = _owned(data, data.payments)[0]
        self.assertEqual(("甲木工班", "2026-09"), (payment["company"], payment["period"]))

    def test_proposed_deduction_with_no_voucher_stays_raw_not_approved_or_offset(self):
        md = draft("质量罚款：2400元")
        rows = table(md, "6 扣款")
        self.assertEqual("2400元", rows[1][rows[0].index("用户拟扣款金额原文（未核）")])
        self.assertEqual(MISSING, rows[1][rows[0].index("用户扣款依据 / 凭证")])
        self.assertEqual("未执行 / 未抵扣", rows[1][-1])

    def test_payment_roles_keep_previous_current_advance_living_and_wages_distinct(self):
        data = parse("累计已付：10000元\n支付凭证：P-A.pdf\n本期已付：2000元\n预付款已付：3000元\n生活费预支：500元\n工资代发已付：4000元")
        self.assertEqual(5, len(data.payments))
        keys = ("cumulative_paid", "paid", "advance_paid", "living_paid", "wage_paid")
        self.assertEqual(["10000元", "2000元", "3000元", "500元", "4000元"], [r[k] for r, k in zip(data.payments, keys)])
        self.assertTrue(all("payment_source" not in r for r in data.payments[1:]))
        self.assertTrue(all(sum(k in r for k in keys) == 1 for r in data.payments))

    def test_pending_or_hearsay_paid_label_does_not_populate_actual_payment(self):
        for text in ("本期已付：预计2000元", "累计已付：据说10000元", "工资代发已付：尚未支付4000元"):
            with self.subTest(text=text):
                data = parse(text)
                self.assertTrue(all(not any(k in r for k in ("paid", "cumulative_paid", "wage_paid")) for r in data.payments))
                self.assertTrue(any(text.split("：")[1] in r.get("payment_record", "") for r in data.payments))

    def test_planned_living_advance_is_not_actual_or_assigned_to_other_team(self):
        data = parse("老李的木工班和鑫源架子队这个月一块结，架子队搭的7#楼外架，木工班计划预支生活费30000")
        row = _owned(data, data.payments)[0]
        self.assertEqual("老李的木工班", row["company"])
        self.assertEqual("30000", row["planned_payment"])
        self.assertNotIn("living_paid", row)

    def test_quantity_parameter_date_code_and_floor_digits_not_money(self):
        for key in ("本期已付", "水电费", "单价", "应付人工费"):
            for value in ("HRB400", "2026-09-22", "35吨", "3#楼", "RW-0823", "35m2", "是否30000元？"):
                with self.subTest(key=key, value=value):
                    data = parse(key+"："+value)
                    records = [*data.rows, *data.deductions, *data.payments, *data.wages]
                    money_fields = {"paid", "deduction_amount", "price", "labor_payable"}
                    self.assertTrue(all(not any(r.get(k) not in {None, MISSING} for k in money_fields) for r in records))
        for text in ("之前累计已经付了35m2", "本期要扣水电费35吨", "生活费30000kg"):
            data = parse(text)
            self.assertEqual([], data.payments)
            self.assertEqual([], data.deductions)

    def test_unknown_price_does_not_erase_explicit_foreman_quantity(self):
        data = parse("工长核完是4120平但单价46.5还没定")
        self.assertEqual("4120平", data.rows[0]["foreman_qty"])
        self.assertEqual(MISSING, data.rows[0]["price"])
        self.assertIn("还没定", data.rows[0]["record"])

    def test_currency_and_user_tax_basis_do_not_create_conversion_or_amount(self):
        data = parse("币种：SGD\n分包单位：甲班\n确认量：30吨\n单价：SGD920/t\n价税口径：用户声明含税\n币种：CNY\n确认量：20吨\n单价：CNY800/t")
        self.assertEqual(["SGD", "CNY"], [r["currency"] for r in data.rows])
        self.assertNotIn("tax_basis", data.rows[1])
        self.assertTrue(all("claimed_amount" not in r and "user_payable" not in r for r in data.rows))

    def test_explicit_retention_rate_has_no_default_limit_return_or_warranty_end(self):
        data = parse("质保比例：5%\n质保金：30000元\n质保依据：用户合同A\n缺陷责任期：24个月\n保修期：5年\n质保金返还记录：用户称已返还10000元")
        self.assertEqual(("5%", "30000元", "24个月", "5年"), tuple(data.quality[0][k] for k in ("retention_rate", "retention_amount", "defect_period", "warranty")))
        md = draft("质保比例：5%\n质保金返还记录：用户称已返还10000元")
        self.assertNotIn("3%", md)
        self.assertNotIn("无效", md)
        self.assertIn("未执行", table(md, "7 质量")[1][-1])

    def test_variation_signature_is_not_quantity_or_inclusion_in_settlement(self):
        data = parse("变更项目：用户洽商A\n签证来源：V-A.pdf\n签证状态：用户称经理已签字\n变更申请量：20平\n变更金额：2000元")
        self.assertEqual("20平", data.variations[0]["variation_qty"])
        self.assertNotIn("confirmed", data.rows[0])
        self.assertIn("未纳入", table(draft("签证状态：用户称经理已签字"), "5 合同外")[1][-1])

    def test_labor_payable_living_advance_and_subcontract_payable_are_separate(self):
        data = parse("用户应付人工费：30000元\n用户应付分包工程款：100000元\n生活费预支：5000元\n工资表：W-A.xlsx\n工人签字记录：用户称已签字")
        self.assertEqual("30000元", data.wages[0]["labor_payable"])
        self.assertEqual("100000元", data.rows[0]["user_payable"])
        self.assertEqual("5000元", data.payments[0]["living_paid"])
        self.assertNotIn("wage_paid", data.payments[0])
        self.assertEqual(["TBD · 未核定", "未执行 / 待核"], table(draft("工资表：W-A.xlsx"), "8 农民工")[1][-2:])

    def test_generic_source_is_not_task_rate_confirmation_payment_or_deduction_authority(self):
        data = parse("来源：generic.pdf\n确认量：30吨\n单价：920元/吨\n水电费：100元\n累计已付：10000元\n变更项目：A")
        self.assertEqual("generic.pdf", data.rows[0]["source"])
        self.assertTrue(all(k not in data.rows[0] for k in ("task_source", "confirmed_source", "price_source")))
        self.assertNotIn("deduction_source", data.deductions[0])
        self.assertNotIn("payment_source", data.payments[0])
        self.assertNotIn("variation_source", data.variations[0])

    def test_late_party_name_owns_initial_quantities_deductions_and_payment(self):
        data = parse("确认量：30吨\n水电费：100元\n累计已付：10000元\n分包单位：甲班\n合同编号：A")
        self.assertEqual(1, len(data.rows))
        self.assertEqual("甲班", _owned(data, data.deductions)[0]["company"])
        self.assertEqual("A", _owned(data, data.payments)[0]["contract"])

    def test_english_explicit_fields_keep_roles_and_unknown_units_unspecified(self):
        data = parse("Project: Synthetic A\nJurisdiction: SG\nSubcontractor: Team A\nContract: C-A\nPeriod: this month\nName: panel installation\nDeclared quantity: 20\nConfirmed quantity: 15\nContract rate: SGD920\nDeduction type: user-proposed deduction\nDeduction amount: SGD100\nPayment source: P-A.pdf\nCumulative paid: SGD4000")
        self.assertEqual(("Team A", "C-A", "20", "15", "SGD920"), tuple(data.rows[0][k] for k in ("company", "contract", "declared", "confirmed", "price")))
        self.assertNotIn("unit", data.rows[0])
        self.assertEqual("SGD100", data.deductions[0]["deduction_amount"])
        self.assertEqual("P-A.pdf", data.payments[0]["payment_source"])

    def test_example_scope_masked_and_explicit_actual_reset_preserved(self):
        data = parse("例如甲班确认量30吨\n分包单位：示例队\n确认量：30吨\n实际情况：\n分包单位：乙班\n确认量：20吨")
        self.assertEqual(1, len(data.rows))
        self.assertEqual("乙班", data.rows[0]["company"])
        self.assertEqual("20吨", data.rows[0]["confirmed"])

    def test_legacy_short_command_keeps_unassigned_not_confirmed_quantity(self):
        data = parse("写一份分包结算 模板 120m2；钢筋 2.5t")
        self.assertEqual(["模板", "钢筋"], [r["name"] for r in data.rows])
        self.assertEqual(["120m2", "2.5t"], [r["unassigned"] for r in data.rows])
        self.assertTrue(all("confirmed" not in r for r in data.rows))

    def test_physical_input_line_kept_separate_from_original_page_and_party(self):
        data = parse("### a.pdf\n分包单位：甲班\n确认量：30吨")
        evidence = next(r for r in _owned(data, data.evidence) if r["field"] == "用户验工量")
        self.assertEqual("甲班", evidence["company"])
        self.assertEqual("a.pdf · 本轮输入第3行（非原件页码）", evidence["input_position"])


class SubcontractRuntime(unittest.TestCase):
    def setUp(self):
        from packing_assistant.runtime import agent_loop, memory
        directory = tempfile.TemporaryDirectory(prefix="subcontract-regression-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for module in (expert_turn, agent_loop, memory):
            context = patch.object(module, "_OUT", self.root)
            context.start()
            self.addCleanup(context.stop)
        self.scheduler = Scheduler()
        for context in (patch("packing_assistant.runtime.scheduler.get_scheduler", return_value=self.scheduler), patch.dict(os.environ, {"CIVIL_JOB_ROOT": "", "CIVIL_SANDBOX_ROOTS": str(self.root), "CIVIL_SANDBOX": "workspace-write", "CIVIL_APPROVAL": "on-request"})):
            context.start()
            self.addCleanup(context.stop)

    def test_real_office_tables_match_low_risk_draft_without_confirmation_and_no_formula(self):
        import openpyxl
        from xml.etree import ElementTree as ET
        from zipfile import ZipFile
        result = expert_turn.run_expert_turn("写一份分包结算\n分包单位：=Team|A & B\n确认量：30吨\n合同单价：920元/吨\n质量罚款：2400元\n通知单：=1+1\n累计已付：10000元", "subcontract", session_id="formats-subcontract", force_intent="run")
        self.assertTrue(result["wrote"] and result["submit_blocked"], result)
        self.assertFalse(result["hitl_pending"])
        self.assertEqual("done", self.scheduler.get(result["run_id"]).state)
        paths = {Path(f["path"]).suffix: Path(f["path"]) for f in result["files"]}
        expected = table(paths[".md"].read_text(encoding="utf-8"), "6 扣款")
        book = openpyxl.load_workbook(paths[".xlsx"])
        try:
            sheet = next(book[name] for name in book.sheetnames if name.startswith("6 扣款"))
            self.assertEqual(expected, [["" if c.value is None else c.value for c in row] for row in sheet])
            self.assertTrue(all(c.data_type != "f" for sheet in book for row in sheet for c in row))
        finally:
            book.close()
        with ZipFile(paths[".docx"]) as archive:
            document = ET.fromstring(archive.read("word/document.xml"))
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        tables = [[["".join(t.text or "" for t in c.findall(".//w:t", ns)) for c in r.findall("w:tc", ns)] for r in tbl.findall("w:tr", ns)] for tbl in document.findall(".//w:tbl", ns)]
        self.assertIn(expected, tables)

    def test_chat_and_readonly_never_write_or_approve_payments(self):
        chat = expert_turn.run_expert_turn("分包结算和付款批准怎么区分？", "subcontract", session_id="chat-subcontract")
        self.assertFalse(chat["wrote"])
        with patch.dict(os.environ, {"CIVIL_SANDBOX": "read-only"}):
            denied = expert_turn.run_named_exclusive(TOOL, {"text": "写分包结算", "session_id": "ro-subcontract"})
        self.assertFalse(denied["wrote"])
        self.assertFalse(list(self.root.rglob("*.md")))

    def test_hostile_inputs_keep_tables_intact_without_own_payment_verdicts(self):
        from scripts.test_post_robustness import INPUTS, VERDICTS, table_rows
        import re
        for name, source in INPUTS.items():
            with self.subTest(input=name):
                result = expert_turn.run_named_exclusive(TOOL, {"text": source, "session_id": "hostile-subcontract"})
                self.assertTrue(result["wrote"] and result["submit_blocked"], result)
                path = next(Path(f["path"]) for f in result["files"] if f["path"].endswith(".md"))
                md = path.read_text(encoding="utf-8")
                parsed = table_rows(md)
                for i in range(1, len(parsed)):
                    line, cells = parsed[i]
                    previous_line, previous = parsed[i-1]
                    if line != previous_line+1 or all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                        continue
                    self.assertEqual(len(previous), len(cells))
                self.assertTrue(all(p.search(md) is None for p in VERDICTS))


if __name__ == "__main__":
    unittest.main()
