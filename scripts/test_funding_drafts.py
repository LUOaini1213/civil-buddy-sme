#!/usr/bin/env python3
"""Offline funding records, account/currency ownership and real export checks."""
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
from packing_assistant.post_drafts import build_draft as dispatch
from packing_assistant.post_drafts.funding import MISSING, TOOL, _balances, build_draft, parse
from packing_assistant.runtime.scheduler import Scheduler
from packing_assistant.runtime.tool_engine import default_engine


def draft(text):
    return build_draft("finance-fund", TOOL, text)


def table(md, title):
    return next(rows for name, rows in tables_from_md(md) if name.startswith(title))


class FundingDrafts(unittest.TestCase):
    def test_dispatch_is_exclusive_and_no_country_currency_or_approval_default(self):
        md = draft("")
        self.assertEqual(md, dispatch("finance-fund", TOOL, ""))
        self.assertIsNone(build_draft("finance-fund", "equip__ledger", ""))
        self.assertIsNone(build_draft("equip", TOOL, ""))
        self.assertEqual(10, len([s for s in md.splitlines() if s.startswith("## ")]))
        for marker in ("AI 草稿", "submit_blocked=true", "无法试算", "签认：（空）", "不构成付款指令"):
            self.assertIn(marker, md)
        for word in ("CN：", "SG：", "人民币", "IRAS", "财建", "## 用户原文", "资金充足", "可以付款", "同意付款"):
            self.assertNotIn(word, md)

    def test_all_dev_facts_have_semantically_correct_fields(self):
        from scripts.eval_post_content import classify, load, spots, split_echo
        for case in load("cases"):
            if case["post"] != "finance-fund":
                continue
            with self.subTest(case=case["id"]):
                md = draft(case["request"])
                body, echo = split_echo(md)
                for fact in case["facts"]:
                    self.assertEqual("placed", classify(fact, body, echo, spots(body)), fact)
                self.assertTrue(all(w not in body for w in case.get("forbidden", [])))

    def test_signed_valuation_remains_forecast_not_cash_received(self):
        data = parse("收入：业主进度款，计量单已经签认，1725万，说是10月18日到基本户")
        self.assertEqual(1, len(data.income))
        row = data.income[0]
        self.assertEqual(("业主进度款", "1725万", "10月18日", "基本户"), tuple(row[k] for k in ("item", "amount", "planned_date", "account")))
        self.assertNotIn("actual_amount", row)
        self.assertNotIn("actual_date", row)
        self.assertIn("计量单已经签认", row["evidence"])

    def test_narrative_receipts_and_payments_own_amounts_accounts_and_evidence(self):
        data = parse("做一下10月的资金计划，业主那笔进度款计量单已经签认，1725万，说是10月18日到基本户；"
                     "支出上要给华远商砼付材料款420万，月底前还得往工资专户拨93万代发木工班人工费，机械租赁押金还没谈定先空着。")
        self.assertEqual("10月", data.contexts[0]["period"])
        first, wages, deposit = data.payments
        self.assertEqual(("材料款", "华远商砼", "420万"), tuple(first[k] for k in ("item", "counterparty", "amount")))
        self.assertNotIn("account", first)
        self.assertEqual(("人工费", "93万", "工资专户", "月底前"), tuple(wages[k] for k in ("item", "amount", "account", "planned_date")))
        self.assertNotIn("counterparty", wages)
        self.assertNotIn("amount", deposit)
        self.assertNotIn("currency", first)

    def test_unknown_owner_approval_does_not_become_paid_or_received(self):
        data = parse("一头是七号楼那笔进度款，商务说报了320万上去，业主批没批我不知道；"
                     "另一头脚手架租赁，鑫盛架管天天催，合同写的按月付，这个月得付58万，从基本结算户走，工资那块等劳资把实名制工资表给我再说。")
        row = data.income[0]
        self.assertEqual("320万", row["amount"])
        self.assertEqual("七号楼", row["unit"])
        self.assertIn("批没批我不知道", row["evidence"])
        self.assertNotIn("state", row)
        first, wages = data.payments
        self.assertEqual(("脚手架租赁", "鑫盛架管", "58万", "基本结算户", "按月付"), tuple(first[k] for k in ("item", "counterparty", "amount", "account", "method")))
        self.assertIn("实名制工资表", wages["evidence"])
        self.assertNotIn("amount", wages)
        self.assertTrue(all("actual_amount" not in r for r in data.income+data.payments))

    def test_explicit_actual_amount_and_date_do_not_replace_plan_fields(self):
        data = parse("收款：进度款\n计划金额：300万元\n计划日：2026-10-18\n实际收款金额：280万元\n实际到账日：2026-10-21\n状态：已到账\n证据：回单A.pdf")
        row = data.income[0]
        self.assertEqual(["300万元", "280万元", "2026-10-18", "2026-10-21"], [row[k] for k in ("amount", "actual_amount", "planned_date", "actual_date")])
        self.assertEqual("已到账", row["state"])
        self.assertEqual("未执行", table(draft("收款：进度款\n状态：已到账"), "5 收入")[1][-1])

    def test_reported_actual_narrative_is_raw_unverified_not_payment_approval(self):
        data = parse("收入：进度款已到账300元，10月18日已经收到基本户；付款：钢筋款已支付80元")
        self.assertEqual("300元", data.income[0]["actual_amount"])
        self.assertEqual("10月18日", data.income[0]["actual_date"])
        self.assertEqual("80元", data.payments[0]["actual_amount"])
        self.assertNotIn("amount", data.income[0])
        self.assertEqual("未执行 /（空）", table(draft("付款：钢筋款已支付80元"), "6 支出")[1][-1])

    def test_plan_or_negative_actual_language_cannot_fill_actual_amount(self):
        for phrase in ("预计已到账300元", "计划已到账300元", "还未到账300元", "并未已支付300元", "据说已到账300元", "不是已到账300元", "已到账300元？"):
            with self.subTest(phrase=phrase):
                row = parse("收入：进度款 "+phrase).income[0]
                self.assertNotIn("actual_amount", row)

    def test_status_or_date_alone_cannot_promote_forecast_money_to_actual(self):
        data = parse("收入：进度款\n计划金额：300元\n状态：已到账\n实际到账日：2026-10-20")
        self.assertEqual("300元", data.income[0]["amount"])
        self.assertNotIn("actual_amount", data.income[0])

    def test_standalone_actual_payment_cannot_become_actual_receipt(self):
        data = parse("收入：款项A\n金额：300元\n已支付80元")
        self.assertNotIn("actual_amount", data.income[0])
        self.assertEqual("80元", data.payments[0]["actual_amount"])
        self.assertNotIn("item", data.payments[0])

    def test_conflicting_receipt_name_and_paid_claim_stays_unassigned(self):
        data = parse("收入：进度款已支付300元")
        self.assertNotIn("actual_amount", data.income[0])
        self.assertFalse(data.payments)
        self.assertEqual("300元", data.unassigned[0]["actual_amount"])
        self.assertIn("方向冲突", data.unassigned[0]["kind"])

    def test_table_opposite_direction_actual_amount_does_not_join_named_node(self):
        data = parse("| 收支类别 | 节点名称 | 实际付款金额 |\n| --- | --- | --- |\n| 收款 | 进度款 | 300元 |")
        self.assertFalse(data.income)
        self.assertFalse(data.payments)
        self.assertEqual("300元", data.unassigned[0]["actual_amount"])

    def test_two_actual_directions_in_a_row_are_unassigned_not_last_value_wins(self):
        data = parse("| 收款 | 实际收款金额 | 实际付款金额 |\n| --- | --- | --- |\n| 进度款 | 300元 | 80元 |")
        self.assertNotIn("actual_amount", data.income[0])
        self.assertEqual("300元；80元", data.unassigned[0]["actual_amount"])

    def test_money_values_keep_units_currency_commas_and_sign_without_conversion(self):
        for value in ("SGD 1,800.25", "人民币320万元", "-0.05万元", "USD 200", "HKD 1,200", "AUD 300", "JPY 900", "S$ 123.45", "0元"):
            with self.subTest(value=value):
                self.assertEqual(value, parse("收入：款项A\n金额："+value).income[0]["amount"])

    def test_money_values_cannot_come_from_codes_dates_versions_or_examples(self):
        for label in ("金额", "实际收款金额", "授信额度"):
            for value in ("EQ-300", "V2.0", "2026-10-20", "10月20日", "账单编号300", "例如300元", "不是300元", "如果300元", "300元？", "300万元未到账", "没数"):
                with self.subTest(label=label, value=value):
                    data = parse("收入：款项A\n"+label+"："+value)
                    rows = data.financing if label == "授信额度" else data.income
                    key = {"金额": "amount", "实际收款金额": "actual_amount", "授信额度": "credit"}[label]
                    self.assertEqual(MISSING, (rows or [{}])[0].get(key, MISSING))

    def test_explicit_actual_short_labels_are_not_forecast_amounts(self):
        for label in ("实际收款", "实际付款", "已到账", "已支付", "实收", "实付"):
            with self.subTest(label=label):
                data = parse("收入：款项A\n"+label+"：SGD 1,200")
                rows = data.income if label in {"实际收款", "已到账", "实收"} else data.payments
                self.assertEqual("SGD 1,200", rows[0]["actual_amount"])
                self.assertNotIn("amount", rows[0])
                if rows is data.payments:
                    self.assertNotIn("actual_amount", data.income[0])
        data = parse("实际收款：进度款300元")
        self.assertEqual("300元", data.income[0]["actual_amount"])

    def test_conditional_actual_label_cannot_turn_expected_or_negated_receipt_actual(self):
        data = parse("实际收款：计划进度款300元")
        self.assertEqual("300元", data.income[0]["amount"])
        self.assertNotIn("actual_amount", data.income[0])
        data = parse("实际收款：不是300元")
        self.assertNotIn("actual_amount", data.income[0])

    def test_unverified_reported_arrival_is_still_reported_actual_not_verified_cash(self):
        data = parse("收入：进度款已到账300元还没核原件")
        self.assertEqual("300元", data.income[0]["actual_amount"])
        self.assertNotIn("amount", data.income[0])
        self.assertEqual("未执行", table(draft("收入：进度款已到账300元还没核原件"), "5 收入")[1][-1])

    def test_expected_actual_status_qualifier_is_not_removed(self):
        row = parse("收入：进度款预计已到账300元").income[0]
        self.assertEqual("300元", row["amount"])
        self.assertEqual("进度款预计已到账300元", row["state"])
        self.assertNotIn("actual_amount", row)

    def test_changed_period_currency_or_region_cannot_borrow_prior_amount_account(self):
        for label, first, second, key in (("计划期", "10月", "11月", "period"), ("币种", "SGD", "CNY", "currency"), ("辖区", "CN", "SG", "jurisdiction")):
            with self.subTest(label=label):
                data = parse("收入：进度款\n"+label+"："+first+"\n金额：30万\n账户：账户A\n"+label+"："+second+"\n实际收款金额：20万")
                self.assertEqual(2, len(data.income))
                self.assertEqual([first, second], [r[key] for r in data.income])
                self.assertNotIn("account", data.income[1])
                self.assertNotIn("amount", data.income[1])
                self.assertNotIn("actual_amount", data.income[0])

    def test_account_before_balance_is_not_lost_or_loaned_from_a_transaction(self):
        data = parse("账户：账户A\n期初余额：30万\n余额来源：bankA.pdf")
        self.assertEqual("账户A", data.balances[0]["account"])
        self.assertFalse(data.unassigned)
        data = parse("收入：进度款\n账户：账户A\n期初余额：30万\n余额来源：bankA.pdf")
        self.assertNotIn("account", data.balances[0])

    def test_full_period_range_and_currency_are_not_reduced_to_first_date(self):
        data = parse("计划期：2026-10-06至2026-11-02\n币种：人民币\n收入：款项A\n金额：300万\n计划日：2026-10-20")
        self.assertEqual("2026-10-06至2026-11-02", data.income[0]["period"])
        self.assertEqual("人民币", data.income[0]["currency"])

    def test_dates_are_not_invented_from_ids_or_month_without_year(self):
        for value in ("V2.0", "BANK-20261020", "2026-10-20？", "不是2026-10-20", "未给"):
            self.assertEqual(MISSING, parse("收入：款项A\n计划日："+value).income[0].get("planned_date", MISSING))
        self.assertEqual("月底前", parse("收入：款项A\n计划日：月底前").income[0]["planned_date"])

    def test_balance_without_own_source_stays_missing_but_raw_claim_has_provenance(self):
        text = "期初余额：300元\n账户：账户A\n收入：进度款\n金额：800元\n来源：银行回单A.pdf"
        data = parse(text)
        self.assertEqual("300元", data.balances[0]["opening_amount"])
        self.assertEqual(MISSING, _balances(data.balances)[0]["opening_amount"])
        self.assertTrue(any(r["value"] == "300元" and r["field"] == "期初可用余额" for r in data.evidence))
        self.assertEqual(MISSING, table(draft(text), "2 期间")[1][-3])

    def test_balance_with_matching_source_is_only_a_user_report_not_available_cash(self):
        data = parse("期初余额：300元\n账户：账户A\n余额来源：bankA.pdf\n期末余额：280元")
        self.assertEqual("300元", _balances(data.balances)[0]["opening_amount"])
        self.assertEqual("280元", _balances(data.balances)[0]["closing_amount"])
        self.assertEqual("本轮未核验", table(draft("期初余额：300元\n余额来源：bankA.pdf"), "2 期间")[1][-1])

    def test_pending_balance_source_and_unknown_amount_cannot_establish_balance(self):
        for source in ("待提供bankA.pdf", "以银行对账单为准", "bankA.pdf未收到", "还没拿到"):
            data = parse("期初余额：300元\n余额来源："+source)
            self.assertEqual(MISSING, _balances(data.balances)[0]["opening_amount"])
        data = parse("期初可用余额：以银行对账单为准，我手上没数")
        self.assertEqual(MISSING, data.balances[0]["opening_amount"])

    def test_two_accounts_do_not_borrow_balance_sources_or_amounts(self):
        data = parse("期初余额：300元\n账户：账户A\n余额来源：bankA.pdf\n账户：账户B\n期初余额：80元")
        first, second = _balances(data.balances)
        self.assertEqual(("账户A", "300元", "bankA.pdf"), tuple(first[k] for k in ("account", "opening_amount", "balance_source")))
        self.assertEqual("账户B", second["account"])
        self.assertEqual(MISSING, second["opening_amount"])
        self.assertNotIn("balance_source", second)

    def test_generic_table_uses_row_direction_not_previous_row(self):
        data = parse("| 节点名称 | 收支类别 | 金额 | 币种 | 账户 | 状态 |\n| --- | --- | --- | --- | --- | --- |\n"
                     "| 甲款 | 收款 | 30万 | SGD | 账户A | 已报未批 |\n| 乙款 | 付款 | 20万 | CNY | 账户B | 未付 |\n| | | | | | |")
        self.assertEqual("甲款", data.income[0]["item"])
        self.assertEqual("SGD", data.income[0]["currency"])
        self.assertEqual("乙款", data.payments[0]["item"])
        self.assertEqual("账户B", data.payments[0]["account"])
        self.assertEqual(1, len(data.unassigned))
        self.assertEqual(MISSING, data.unassigned[0]["item"])
        self.assertEqual(MISSING, data.unassigned[0]["amount"])

    def test_explicit_row_currency_overrides_file_currency_without_extra_phantom_row(self):
        data = parse("币种：SGD\n收入：甲款\n金额：300元\n付款：材料款 CNY 200")
        self.assertEqual(1, len(data.income))
        self.assertEqual(1, len(data.payments))
        self.assertEqual("SGD", data.income[0]["currency"])
        self.assertEqual("CNY", data.payments[0]["currency"])
        data = parse("币种：SGD\n| 收支类别 | 节点名称 | 金额 | 币种 |\n| --- | --- | --- | --- |\n| 付款 | 乙款 | 200 | CNY |")
        self.assertEqual("CNY", data.payments[0]["currency"])

    def test_actual_opposite_direction_currency_does_not_create_empty_prior_direction_row(self):
        data = parse("币种：SGD\n收入：甲款\n金额：300元\n已支付CNY 200")
        self.assertEqual(1, len(data.income))
        self.assertEqual(1, len(data.payments))
        self.assertEqual("CNY", data.payments[0]["currency"])
        self.assertEqual("CNY 200", data.payments[0]["actual_amount"])

    def test_changed_building_does_not_borrow_prior_transaction_amount(self):
        data = parse("收入：进度款\n楼栋：7号楼\n金额：300元\n楼栋：8号楼\n计划日：10月20日")
        self.assertEqual(2, len(data.income))
        self.assertEqual(["7号楼", "8号楼"], [r["unit"] for r in data.income])
        self.assertNotIn("amount", data.income[1])

    def test_two_direction_cells_with_generic_amount_are_unassigned_not_last_payment(self):
        data = parse("| 收款 | 付款 | 金额 | 账户 |\n| --- | --- | --- | --- |\n| 甲款 | 乙款 | 30万 | 账户X |")
        self.assertNotIn("amount", data.income[0])
        self.assertNotIn("amount", data.payments[0])
        self.assertEqual(("30万", "账户X"), tuple(data.unassigned[0][k] for k in ("amount", "account")))

    def test_table_end_clears_owner_and_tail_amount_is_unassigned(self):
        data = parse("| 收款 | 金额 |\n| --- | --- |\n| 甲款 | 30万 |\n\n金额：20万")
        self.assertEqual("30万", data.income[0]["amount"])
        self.assertEqual("20万", data.unassigned[0]["amount"])

    def test_repeated_item_does_not_merge_two_transactions(self):
        data = parse("收款：进度款\n款项编号：TX-A\n金额：30万\n收款：进度款\n款项编号：TX-B\n计划日：10月20日")
        self.assertEqual(2, len(data.income))
        self.assertNotIn("amount", data.income[1])
        self.assertNotIn("planned_date", data.income[0])

    def test_changed_counterparty_or_transaction_id_does_not_borrow_money(self):
        for label in ("对象", "款项编号"):
            data = parse("付款：材料款\n"+label+"：甲\n金额：30万\n账户：账户A\n"+label+"：乙\n计划日：10月20日")
            self.assertEqual(2, len(data.payments))
            self.assertNotIn("amount", data.payments[1])
            self.assertNotIn("account", data.payments[1])

    def test_financing_and_approval_never_become_receipts_or_cash_permission(self):
        text = "融资方：贷款方A\n授信编号：CR-A\n授信额度：SGD 800,000\n融资限制：需董事签字\n"
        text += "审批事项：材料款申请\n审批状态：用户说已通过\n审批条件：补合同\n审批人：张测试"
        data = parse(text)
        self.assertFalse(data.income)
        self.assertFalse(data.payments)
        self.assertEqual("SGD 800,000", data.financing[0]["credit"])
        self.assertEqual("用户说已通过", data.approvals[0]["approval_state"])
        self.assertEqual("未执行 /（空）", table(draft(text), "9 自检")[1][-1])

    def test_second_finance_identity_does_not_inherit_credit_or_restriction(self):
        data = parse("融资方：甲\n授信额度：300元\n融资限制：需签字\n融资方：乙\n授信编号：CR-B")
        self.assertEqual(2, len(data.financing))
        self.assertNotIn("credit", data.financing[1])
        self.assertNotIn("restriction", data.financing[1])

    def test_files_repeated_names_projects_versions_and_regions_remain_isolated(self):
        data = parse("### A.txt\n项目：甲\n辖区：CN\n币种：人民币\n收入：进度款\n金额：30万\n"
                     "### A.txt\n项目：乙\n收入：进度款\n账户：账户B\n版本：V2\n金额：20万")
        self.assertEqual(2, len(data.income))
        self.assertEqual(["CN", "UNSPECIFIED"], [r["jurisdiction"] for r in data.income])
        self.assertNotIn("currency", data.income[1])
        self.assertNotIn("account", data.income[0])

    def test_orphan_metadata_in_next_file_does_not_change_previous_receipt(self):
        data = parse("### A.txt\n收入：进度款\n金额：30万\n### B.txt\n账户：账户B\n实际收款金额：20万")
        self.assertNotIn("account", data.income[0])
        self.assertNotIn("actual_amount", data.income[0])
        self.assertEqual("20万", data.income[1]["actual_amount"])
        self.assertNotIn("item", data.income[1])
        self.assertNotIn("account", data.income[1])
        self.assertEqual("账户B", data.unassigned[0]["account"])

    def test_examples_are_not_facts_but_actual_reset_retains_literal_records(self):
        data = parse("例如收入：进度款300元，账户：账户X\n实际情况：收入：进度款\n金额：80元")
        self.assertEqual(1, len(data.income))
        self.assertEqual("80元", data.income[0]["amount"])
        self.assertNotIn("账户X", repr(data))

    def test_english_explicit_labels_preserve_raw_values_and_missing_fields(self):
        data = parse("Project: Synthetic A\nCurrency: SGD\nIncome: Draw A\nAmount: 1,200.00\nAccount: Account A\nPlanned date: 2026-10-20\nActual amount: 900.00\nEvidence: bankA.pdf")
        row = data.income[0]
        self.assertEqual(("Draw A", "1,200.00", "900.00", "Account A", "SGD"), tuple(row[k] for k in ("item", "amount", "actual_amount", "account", "currency")))
        self.assertNotIn("actual_date", row)

    def test_evidence_names_physical_lines_not_fabricated_original_page(self):
        data = parse("### A.txt\n收入：甲款\n金额：30万\n### B.txt\n付款：乙款\n账户：账户B")
        self.assertEqual(["A.txt · 本轮输入第2行（非原件页码）", "A.txt · 本轮输入第3行（非原件页码）",
                          "B.txt · 本轮输入第5行（非原件页码）", "B.txt · 本轮输入第6行（非原件页码）"], [r["location"] for r in data.evidence])


class FundingRuntime(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="funding-regression-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for context in (patch.object(expert_turn, "_OUT", self.root),
                        patch("packing_assistant.runtime.memory._OUT", self.root),
                        patch("packing_assistant.runtime.scheduler.get_scheduler", return_value=Scheduler()),
                        patch.dict(os.environ, {"CIVIL_JOB_ROOT": "", "CIVIL_SANDBOX_ROOTS": str(self.root), "CIVIL_SANDBOX": "workspace-write", "CIVIL_APPROVAL": "on-request"})):
            context.start()
            self.addCleanup(context.stop)

    def test_actual_markdown_word_and_excel_match_and_formulas_are_neutralized(self):
        from xml.etree import ElementTree as ET
        from zipfile import ZipFile
        import openpyxl
        result = default_engine().execute(TOOL, {"text": "Income: =Draw|A\nAmount: SGD 1,200\nCounterparty: A & B",
                                                "session_id": "export-fund"}, expert_id="finance-fund", intent="run")
        self.assertTrue(result["ok"] and result["wrote"] and result["submit_blocked"], result)
        paths = {Path(f["path"]).suffix: Path(f["path"]) for f in result["files"]}
        self.assertTrue({".md", ".docx", ".xlsx"}.issubset(paths), paths)
        expected = table(paths[".md"].read_text(encoding="utf-8"), "5 收入")
        book = openpyxl.load_workbook(paths[".xlsx"])
        try:
            sheet = next(book[n] for n in book.sheetnames if n.startswith("5 收入"))
            self.assertEqual(expected, [["" if c.value is None else c.value for c in row] for row in sheet])
            self.assertTrue(all(c.data_type != "f" for sheet in book for row in sheet for c in row))
        finally:
            book.close()
        with ZipFile(paths[".docx"]) as archive:
            document = ET.fromstring(archive.read("word/document.xml"))
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        tables = [[["".join(t.text or "" for t in c.findall(".//w:t", ns)) for c in r.findall("w:tc", ns)]
                   for r in tbl.findall("w:tr", ns)] for tbl in document.findall(".//w:tbl", ns)]
        self.assertIn(expected, tables)

    def test_question_and_readonly_paths_cannot_write(self):
        result = expert_turn.run_expert_turn("以收定支怎么理解？", "finance-fund", session_id="chat-fund")
        self.assertFalse(result["wrote"])
        with patch.dict(os.environ, {"CIVIL_SANDBOX": "read-only"}):
            denied = expert_turn.run_named_exclusive(TOOL, {"text": "写资金计划", "session_id": "ro-fund"})
            self.assertFalse(denied["wrote"])
        self.assertFalse(list(self.root.rglob("*.md")))

    def test_hostile_inputs_leave_columns_intact_and_do_not_assert_verdicts(self):
        from scripts.test_post_robustness import INPUTS, VERDICTS, table_rows
        import re
        for name, source in INPUTS.items():
            with self.subTest(input=name):
                result = expert_turn.run_named_exclusive(TOOL, {"text": source, "session_id": "hostile-fund"})
                self.assertTrue(result["wrote"] and result["submit_blocked"], result)
                md = next(Path(f["path"]) for f in result["files"] if f["path"].endswith(".md")).read_text(encoding="utf-8")
                parsed = table_rows(md)
                for i in range(1, len(parsed)):
                    number, cells = parsed[i]
                    previous_number, previous = parsed[i-1]
                    if number != previous_number+1 or all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                        continue
                    self.assertEqual(len(previous), len(cells))
                self.assertTrue(all(pattern.search(md) is None for pattern in VERDICTS))


if __name__ == "__main__":
    unittest.main()
